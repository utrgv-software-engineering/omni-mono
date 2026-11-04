"""Opt-in Azure App Service identity exchange; never trust headers by default.

The operator must prevent direct access around App Service Easy Auth. Identity
headers are assertions from that trusted boundary, not signed tokens themselves.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from .accounts import Accounts, SESSION_COOKIE

PREFIX = "/workplace/auth/azure"
STATE_COOKIE = "__Host-workplace_azure_state"
TENANT = "http://schemas.microsoft.com/identity/claims/tenantid"
OBJECT = "http://schemas.microsoft.com/identity/claims/objectidentifier"


class EasyAuth:
    def __init__(self, base_url, environ=None):
        env = os.environ if environ is None else environ
        value = env.get("WORKPLACE_AZURE_EASY_AUTH", "false").lower()
        if value not in {"true", "false"}:
            raise ValueError("WORKPLACE_AZURE_EASY_AUTH must be true or false")
        self.enabled = value == "true"
        self.tenant = None
        self.accounts = {}
        self.origin = ""
        if not self.enabled:
            return
        url = urlsplit(base_url)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.query
            or url.fragment
        ):
            raise ValueError("Azure Easy Auth requires a public HTTPS base URL")
        self.origin = f"https://{url.netloc}"
        try:
            self.tenant = str(UUID(env["WORKPLACE_AZURE_TENANT_ID"]))
            mapping = json.loads(env["WORKPLACE_AZURE_ACCOUNT_MAP"])
            if not isinstance(mapping, dict):
                raise ValueError()
            self.accounts = {str(UUID(key)): value for key, value in mapping.items()}
            if any(
                not isinstance(value, str) or not value
                for value in self.accounts.values()
            ):
                raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError(
                "Azure Easy Auth requires a tenant UUID and an object-ID to Workplace account-ID JSON map"
            ) from exc

    def metadata(self):
        if not self.enabled:
            return None
        return {
            "login_url": PREFIX + "/login",
            "logout_url": "/.auth/logout?"
            + urlencode({"post_logout_redirect_uri": self.origin + "/"}),
        }

    def account_id(self, request):
        return self.account_id_from(request.headers)

    def account_id_from(self, headers):
        """The Workplace account a request's Microsoft identity maps to."""
        try:
            raw = headers.get("x-ms-client-principal", "")
            if len(raw) > 65536:
                raise ValueError()
            principal = json.loads(base64.b64decode(raw, validate=True))
            if principal.get("auth_typ") != "aad":
                raise ValueError()
            claims = principal["claims"]

            def unique(*names):
                values = {c["val"] for c in claims if c["typ"] in names}
                if len(values) != 1:
                    raise ValueError()
                return str(UUID(values.pop()))

            if unique("tid", TENANT) != self.tenant:
                raise ValueError()
            oid = unique("oid", OBJECT)
            return self.accounts[oid]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(
                403, "This Microsoft identity is not linked to a Workplace account"
            ) from None

    def matches(self, headers, account, accounts) -> bool:
        """Whether a request still carries the Microsoft identity of the
        session's account (an installation's factories: its identity)."""
        try:
            mapped = self.account_id_from(headers)
        except HTTPException:
            return False
        return accounts.username_of(mapped) == account.username

    def require(self, policy, accounts):
        """Under the strict policy, Microsoft sign-in is the only sign-in."""
        if self.enabled and policy.strict:
            getattr(accounts, "local", accounts).passwords = False

    def install(self, app, accounts):
        # Mounted factories use installation identities, never local SSO accounts.
        if not self.enabled or not isinstance(accounts, Accounts):
            return

        @app.get(PREFIX + "/login")
        def login():
            state = secrets.token_urlsafe(32)
            key = hashlib.sha256(state.encode()).hexdigest()
            with accounts.store.transaction():
                for old, record in accounts.store.docs("account.azure_state"):
                    if record["expires"] < time.time():
                        accounts.store.delete("account.azure_state", old)
                accounts.store.put(
                    "account.azure_state", key, {"expires": time.time() + 600}
                )
            callback = self.origin + PREFIX + "/callback?" + urlencode({"state": state})
            response = RedirectResponse(
                "/.auth/login/aad?" + urlencode({"post_login_redirect_uri": callback}),
                303,
            )
            response.set_cookie(
                STATE_COOKIE,
                state,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
                max_age=600,
            )
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.get(PREFIX + "/callback")
        def callback(request: Request):
            state = request.query_params.get("state", "")
            cookie = request.cookies.get(STATE_COOKIE, "")
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{43}", state)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie)
                or not secrets.compare_digest(state, cookie)
            ):
                raise HTTPException(
                    403, "Microsoft sign-in expired; start sign-in again"
                )
            key = hashlib.sha256(state.encode()).hexdigest()
            with accounts.store.transaction():
                record = dict(accounts.store.docs("account.azure_state")).get(key)
                accounts.store.delete("account.azure_state", key)
            if not record or record["expires"] < time.time():
                raise HTTPException(
                    403, "Microsoft sign-in expired; start sign-in again"
                )
            account = accounts.accounts.get(self.account_id(request))
            if not account or account.disabled:
                raise HTTPException(403, "Workplace account is unavailable")
            token = accounts.start_session(account)
            accounts.sign_out(request.cookies.get(SESSION_COOKIE))
            response = RedirectResponse("/", 303)
            response.set_cookie(
                SESSION_COOKIE,
                token,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
                max_age=accounts.session_seconds,
            )
            response.delete_cookie(
                STATE_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
            )
            response.headers["Cache-Control"] = "no-store"
            return response
