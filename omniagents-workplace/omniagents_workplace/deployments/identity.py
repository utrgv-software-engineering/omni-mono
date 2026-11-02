"""Factory-scoped OAuth handoff for Coolify's built-in Authentik-compatible client.

Only registered clients and already-provisioned factory administrators are
accepted. Codes are short-lived, single-use, and bound to the originating
Workplace login. This is not a general-purpose identity provider.
"""

import hashlib
import secrets
import time
from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.responses import JSONResponse, RedirectResponse


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class DeploymentIdentity:
    def __init__(self, installation):
        self.installation = installation
        self.store = installation.store

    def issue(self, kind, data, seconds=60):
        token = secrets.token_urlsafe(32)
        with self.store.transaction():
            for key, value in self.store.docs("deployment." + kind):
                if value["expires"] <= time.time():
                    self.store.delete("deployment." + kind, key)
            self.store.put(
                "deployment." + kind,
                digest(token),
                {**data, "expires": time.time() + seconds},
            )
        return token

    def read(self, kind, token, consume=False):
        with self.store.transaction():
            value = dict(self.store.docs("deployment." + kind)).get(digest(token or ""))
            if consume and value:
                self.store.delete("deployment." + kind, digest(token))
        return value if value and value["expires"] > time.time() else None

    def account(self, value, factory_id):
        if not value or value.get("factory") != factory_id:
            raise HTTPException(403, "No access to this factory")
        account = self.installation.accounts.signed_in(value.get("login"))
        member = (
            self.installation.membership(factory_id, account.id) if account else None
        )
        if not account or not member or member["role"] != "admin":
            raise HTTPException(403, "Factory administrator access required")
        return account

    def client(self, factory_id):
        record = self.installation.record(factory_id)
        config = (record or {}).get("deployment")
        if not config or not config.get("enabled"):
            raise HTTPException(404, "Coolify is not enabled")
        return config

    async def oauth(self, request, factory_id, operation, grant):
        config = self.client(factory_id)
        base = self.installation.deployments.url(factory_id)
        callback = base + "/auth/authentik/callback"
        if operation == "authorize" and request.method == "GET":
            self.account(grant, factory_id)
            query = request.query_params
            if (
                query.get("client_id") != factory_id
                or query.get("redirect_uri") != callback
                or query.get("response_type") != "code"
                or not query.get("state")
            ):
                raise HTTPException(400, "Invalid authorization request")
            code = self.issue("code", {**grant, "redirect": callback})
            return RedirectResponse(
                callback + "?" + urlencode({"code": code, "state": query["state"]}), 303
            )
        if operation == "token" and request.method == "POST":
            form = await request.form()
            if form.get("client_id") != factory_id or not secrets.compare_digest(
                str(form.get("client_secret", "")), config["secret"]
            ):
                raise HTTPException(401, "Invalid client")
            if (
                form.get("grant_type") != "authorization_code"
                or form.get("redirect_uri") != callback
            ):
                raise HTTPException(400, "Invalid grant")
            code = self.read("code", str(form.get("code", "")), consume=True)
            self.account(code, factory_id)
            if code["redirect"] != callback:
                raise HTTPException(400, "Invalid redirect")
            token = self.issue("token", code, 120)
            return JSONResponse(
                {"access_token": token, "token_type": "Bearer", "expires_in": 120},
                headers={"Cache-Control": "no-store"},
            )
        if operation == "userinfo" and request.method == "GET":
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer":
                raise HTTPException(401, "Bearer token required")
            account = self.account(self.read("token", token), factory_id)
            return JSONResponse(
                {
                    "sub": account.id,
                    "name": account.name or account.username,
                    "preferred_username": account.username,
                    "email": account.id + "@workplace.invalid",
                    "email_verified": False,
                },
                headers={"Cache-Control": "no-store"},
            )
        raise HTTPException(404, "Unknown authentication endpoint")
