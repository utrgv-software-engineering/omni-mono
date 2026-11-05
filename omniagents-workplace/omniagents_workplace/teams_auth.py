import base64
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from .accounts import Accounts, SESSION_COOKIE

PREFIX = "/workplace/auth/teams"
ANCESTORS = "https://teams.microsoft.com https://*.teams.microsoft.com https://*.cloud.microsoft"
SESSION_HEADER = "x-workplace-session"
SCOPE_CLAIM = "http://schemas.microsoft.com/identity/claims/scope"
SOCKETS = re.compile(r"^/workplace/api/(?:desktop/ws|factory/runtime/agents/[a-z][a-z0-9_]*/ws|personal/chats/[^/]+/runtime/ws(?:/realtime)?)$")
FILES = re.compile(r"^/(?:slack/files/[^/]+(?:/.*)?|linear/uploads/[^/]+|workplace/api/shared/(?:content|download-folder))$")


class TicketRequest(BaseModel):
    path: str


class TeamsAuth:
    def __init__(self, identity, accounts, environ=None):
        env = os.environ if environ is None else environ
        self.identity = identity
        self.accounts = accounts
        self.client_id = env.get("WORKPLACE_TEAMS_CLIENT_ID", "").strip()
        self.enabled = bool(self.client_id)
        self.tickets = {}
        self.lock = threading.Lock()
        if self.enabled:
            self.client_id = str(UUID(self.client_id))
            if not identity.enabled or not isinstance(accounts, Accounts):
                raise ValueError("Teams requires Azure EasyAuth and standalone Workplace accounts")
        self.scope = f"api://{self.client_id}/access_as_user"

    def principal(self, headers):
        account_id = self.identity.account_id_from(headers)
        try:
            principal = json.loads(base64.b64decode(headers.get("x-ms-client-principal", ""), validate=True))
            values = {}
            for claim in principal["claims"]:
                values.setdefault(claim["typ"], set()).add(claim["val"])
            scopes = values.get("scp", set()) | values.get(SCOPE_CLAIM, set())
            callers = values.get("azp", set()) | values.get("appid", set())
            expires = values.get("exp", set())
            if len(scopes) != 1 or "access_as_user" not in next(iter(scopes)).split():
                raise ValueError()
            if callers != {self.client_id} or len(expires) != 1:
                raise ValueError()
            deadline = float(next(iter(expires)))
            if not math.isfinite(deadline) or deadline <= time.time():
                raise ValueError()
            account = self.accounts.accounts[account_id]
            if account.disabled:
                raise ValueError()
            return account, min(deadline, time.time() + 300)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(403, "A delegated Workplace access token from this Teams client is required") from None

    def session(self, headers):
        account, deadline = self.principal(headers)
        token = headers.get(SESSION_HEADER, "")
        if self.accounts.signed_in(token) is not account:
            raise HTTPException(401, "Teams session expired")
        return token, account, deadline

    def install(self, app):
        if not self.enabled:
            return

        @app.get(PREFIX + "/config")
        def config():
            return JSONResponse({"clientId": self.client_id, "tenantId": self.identity.tenant, "scope": self.scope}, headers={"Cache-Control": "no-store"})

        @app.post(PREFIX + "/session")
        def exchange(request: Request):
            if request.headers.get("origin") != self.identity.origin:
                raise HTTPException(403, "Request must come from this workplace")
            account, deadline = self.principal(request.headers)
            token = request.headers.get(SESSION_HEADER, "")
            if self.accounts.signed_in(token) is not account:
                token = self.accounts.start_session(account, expires_at=deadline)
                self.accounts.audit_log.record("auth.teams_signed_in", account_id=account.id)
            else:
                self.accounts.refresh_session(token, deadline)
            return JSONResponse({"session": token, "expiresAt": deadline}, headers={"Cache-Control": "no-store"})

        @app.post(PREFIX + "/ticket")
        def ticket(body: TicketRequest, request: Request):
            if request.headers.get("origin") != self.identity.origin:
                raise HTTPException(403, "Request must come from this workplace")
            token, account, deadline = self.session(request.headers)
            target = urlsplit(body.path)
            socket = bool(SOCKETS.fullmatch(target.path))
            if target.scheme or target.netloc or target.fragment or "%" in target.path or "\\" in target.path or not (socket or FILES.fullmatch(target.path)):
                raise HTTPException(400, "Unsupported ticket destination")
            query = parse_qsl(target.query, keep_blank_values=True)
            if any(key == "workplace_ticket" for key, _ in query):
                raise HTTPException(400, "Invalid ticket destination")
            value = secrets.token_urlsafe(32)
            saved = {name: request.headers[name] for name in ("x-ms-client-principal", "x-workplace-proxy-token") if name in request.headers}
            with self.lock:
                self.tickets = {key: entry for key, entry in self.tickets.items() if entry["expires"] > time.time()}
                if len(self.tickets) >= 4096:
                    raise HTTPException(429, "Too many active tickets")
                self.tickets[hashlib.sha256(value.encode()).hexdigest()] = {
                    "path": target.path, "query": query, "socket": socket, "token": token,
                    "headers": saved, "expires": min(deadline, time.time() + 60), "account": account.id,
                }
            return JSONResponse({"ticket": value}, headers={"Cache-Control": "no-store"})

    def redeem(self, scope, headers):
        query = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
        values = [value for key, value in query if key == "workplace_ticket"]
        if not values:
            return None
        if len(values) != 1:
            raise HTTPException(401, "Invalid ticket")
        key = hashlib.sha256(values[0].encode()).hexdigest()
        remaining = [(name, value) for name, value in query if name != "workplace_ticket"]
        with self.lock:
            entry = self.tickets.get(key)
            if not entry or entry["expires"] <= time.time() or entry["path"] != scope["path"] or entry["query"] != remaining:
                raise HTTPException(401, "Invalid or expired ticket")
            if entry["socket"] != (scope["type"] == "websocket") or scope.get("method", "GET") not in ("GET", "HEAD"):
                raise HTTPException(401, "Invalid ticket use")
            if entry["socket"]:
                if headers.get("origin") != self.identity.origin:
                    raise HTTPException(403, "Request must come from this workplace")
                del self.tickets[key]
        if self.accounts.signed_in(entry["token"]) is None:
            raise HTTPException(401, "Teams session expired")
        scope["query_string"] = urlencode(remaining).encode()
        return entry


class TeamsMiddleware:
    def __init__(self, app, teams):
        self.app = app
        self.teams = teams

    async def __call__(self, scope, receive, send):
        if not self.teams.enabled or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        scope = dict(scope)
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        try:
            entry = self.teams.redeem(scope, headers)
            token = None
            if entry:
                headers.update(entry["headers"])
                token = entry["token"]
            elif SESSION_HEADER in headers and not scope["path"].startswith(PREFIX):
                token, _, _ = self.teams.session(headers)
            if token:
                cookies = [item.strip() for item in headers.get("cookie", "").split(";") if item.strip() and item.strip().partition("=")[0] != SESSION_COOKIE]
                headers["cookie"] = "; ".join([*cookies, f"{SESSION_COOKIE}={token}"])
                scope["headers"] = [(key.encode(), value.encode()) for key, value in headers.items()]
        except HTTPException as error:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": error.detail}, status_code=error.status_code, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return

        async def framed(message):
            if message["type"] == "http.response.start":
                result = []
                has_csp = False
                for name, value in message.get("headers", []):
                    if name.lower() == b"x-frame-options":
                        continue
                    if name.lower() == b"content-security-policy":
                        has_csp = True
                        text = value.decode()
                        if "frame-ancestors" in text:
                            text = re.sub(r"(frame-ancestors[^;]*)", r"\1 " + ANCESTORS, text)
                        else:
                            text += "; frame-ancestors 'self' " + ANCESTORS
                        value = text.encode()
                    result.append((name, value))
                if not has_csp:
                    result.append((b"content-security-policy", ("frame-ancestors 'self' " + ANCESTORS).encode()))
                message = {**message, "headers": result}
            await send(message)
        await self.app(scope, receive, framed)
