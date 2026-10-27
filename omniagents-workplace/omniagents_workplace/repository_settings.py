"""Workplace GitHub connection settings, exported through the existing factory env."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from typing import Literal

from .accounts import SESSION_COOKIE, admin, me
from .credentials import decrypt, encrypt
from .github_device import GitHubDeviceLogin

KEY = "repository.settings"
CALLBACK = "/workplace/api/repositories/github/callback"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RepositoryProviderBoundary:
    """The local GitHub UI and tools cannot silently act on stand-ins in real mode."""

    def __init__(self, app, workplace):
        self.app, self.w = app, workplace

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] == "http"
            and self.w.repository_settings.settings()["provider"] == "github"
            and (path == "/github/mcp" or path.startswith("/workplace/api/github"))
        ):
            await JSONResponse(
                {
                    "detail": "GitHub stand-in is disabled. Use the connected GitHub repositories."
                },
                status_code=409,
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RepositorySettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["standin", "github"]
    repositories: list[str] = Field(default_factory=list, max_length=100)
    factory_repository: str | None = None
    self_improvement: bool = False


class GitHubTokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr


class GitHubAppBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    client_secret: str | None = Field(default=None, max_length=512)
    webhook_secret: str | None = Field(default=None, max_length=512)


class RepositorySettings:
    def __init__(self, workplace):
        self.w = workplace
        self.lock = threading.RLock()
        self.device = GitHubDeviceLogin()
        self._search_scope = None

    def settings(self):
        saved = self.w.store.meta(KEY)
        if saved is not None:
            return saved
        repositories = []
        config = self.w.factory_config
        if config and (config.root / "project.yml").is_file():
            import yaml

            project = yaml.safe_load((config.root / "project.yml").read_text()) or {}
            for entry in project.get("integrations", []):
                if isinstance(entry, dict) and entry.get("type") == "github":
                    repositories = [
                        r
                        for r in entry.get("repositories", [])
                        if isinstance(r, str) and REPO.fullmatch(r)
                    ]
                    break
        return {
            "provider": "standin",
            "repositories": repositories,
            "factory_repository": None,
            "self_improvement": False,
        }

    def _key(self):
        path = self.w.data_dir / "github-connection.key"
        with self.lock:
            if not path.exists():
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as out:
                    out.write(secrets.token_hex(32))
            return path.read_text().strip()

    def connection(self):
        sealed = self.w.store.meta("repository.github.connection")
        return json.loads(decrypt(sealed, self._key())) if sealed else None

    def _connection(self, value):
        self.w.store.set_meta(
            "repository.github.connection",
            encrypt(json.dumps(value), self._key()) if value else None,
        )

    def oauth_config(self):
        sealed = self.w.store.meta("repository.github.app")
        if sealed:
            return json.loads(decrypt(sealed, self._key()))
        return {
            "client_id": os.environ.get("WORKPLACE_GITHUB_CLIENT_ID", ""),
            "client_secret": os.environ.get("WORKPLACE_GITHUB_CLIENT_SECRET", ""),
        }

    def save_app(self, body):
        old = self.oauth_config()
        if body.client_id != old.get("client_id") and not body.client_secret:
            raise HTTPException(422, "Enter the client secret for the new GitHub App.")
        config = {
            "client_id": body.client_id,
            "client_secret": body.client_secret or old.get("client_secret", ""),
            "webhook_secret": body.webhook_secret or old.get("webhook_secret", ""),
        }
        if not config["client_secret"]:
            raise HTTPException(422, "Enter the GitHub App client secret.")
        with self.lock:
            self.device.close()
            self.w.store.set_meta(
                "repository.github.app", encrypt(json.dumps(config), self._key())
            )
            self.w.store.set_meta("repository.github.oauth", None)
            if (
                config["client_id"] != old.get("client_id")
                and (self.connection() or {}).get("auth_type", "oauth") == "oauth"
            ):
                self._connection(None)
                self.disable()
            self.write_environment()
        return self.public()

    def public(self):
        connection = self.connection()
        return {
            **self.settings(),
            "connected_as": connection.get("login") if connection else None,
            "auth_type": connection.get("auth_type", "oauth") if connection else None,
            "oauth_configured": bool(
                self.oauth_config().get("client_id")
                and self.oauth_config().get("client_secret")
            ),
            "client_id": self.oauth_config().get("client_id", ""),
            "webhook_configured": bool(self.oauth_config().get("webhook_secret")),
            "webhook_url": self.w.base_url.rstrip("/") + "/github/events",
            "callback_url": self.w.base_url.rstrip("/") + CALLBACK,
        }

    def exchange(self, fields):
        try:
            response = httpx.post(
                "https://github.com/login/oauth/access_token",
                data={
                    k: v
                    for k, v in {**self.oauth_config(), **fields}.items()
                    if k != "webhook_secret"
                },
                headers={"Accept": "application/json"},
                timeout=20,
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(
                502, "GitHub authentication is unavailable. Try again."
            ) from None
        if not result.get("access_token"):
            raise HTTPException(
                400, "GitHub authorization expired or was declined. Connect again."
            )
        result["expires_at"] = (
            time.time() + result["expires_in"] if result.get("expires_in") else None
        )
        return result

    def token(self):
        with self.lock:
            connection = self.connection()
            owner = (
                self.w.accounts.accounts.get(connection.get("account_id"))
                if connection
                else None
            )
            if not connection or not owner or owner.disabled or not owner.admin:
                raise HTTPException(
                    409, "Connect GitHub with an active Workplace administrator."
                )
            if (
                connection.get("expires_at")
                and connection["expires_at"] < time.time() + 120
            ):
                if not connection.get("refresh_token"):
                    self.disable()
                    raise HTTPException(
                        409, "GitHub authorization expired. Connect again."
                    )
                try:
                    refreshed = self.exchange(
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": connection["refresh_token"],
                        }
                    )
                except HTTPException:
                    self.disable()
                    raise
                connection.update(refreshed)
                self._connection(connection)
            return connection["access_token"]

    def github(self, path, *, token=None):
        try:
            response = httpx.get(
                "https://api.github.com" + path,
                headers={
                    "Authorization": f"Bearer {token or self.token()}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=20,
            )
        except httpx.HTTPError:
            raise HTTPException(502, "GitHub is unavailable. Try again.") from None
        if response.status_code == 429 or (
            response.status_code == 403
            and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or response.headers.get("retry-after")
            )
        ):
            raise HTTPException(
                429, "GitHub search is rate-limited. Wait a moment and try again."
            )
        if response.status_code == 422:
            raise HTTPException(
                422,
                "GitHub could not search with these terms. Try an owner/name or a shorter repository name.",
            )
        if response.status_code in (401, 403, 404):
            raise HTTPException(
                409,
                "GitHub access is unavailable. Check your token or app's repository access and reconnect.",
            )
        if response.is_error:
            raise HTTPException(502, "GitHub could not complete the request.")
        return response.json()

    def search_owners(self):
        # Organization names are small; never enumerate their repositories to search.
        identity = hashlib.sha256(self.token().encode()).hexdigest()
        cached = self._search_scope
        if cached and cached[0] == identity and cached[1] > time.monotonic():
            return cached[2]
        owners = {(self.connection() or {})["login"]}
        page = 1
        while True:
            batch = self.github(f"/user/orgs?per_page=100&page={page}")
            owners.update(org["login"] for org in batch)
            if len(batch) < 100:
                break
            page += 1
        result = sorted(owners)
        self._search_scope = (identity, time.monotonic() + 300, result)
        return result

    def available(self, provider=None, *, page=1, query=""):
        size = 30
        query = query.strip()
        if (provider or self.settings()["provider"]) == "standin":
            repos = sorted(self.w.github.repos.values(), key=lambda r: r.full_name)
            repos = [r for r in repos if query.lower() in r.full_name.lower()]
            start = (page - 1) * size
            return {
                "repositories": [
                    {
                        "full_name": r.full_name,
                        "default_branch": r.default_branch,
                        "html_url": f"/github/{r.full_name}",
                    }
                    for r in repos[start : start + size]
                ],
                "page": page,
                "has_more": start + size < len(repos),
                "limited": False,
                "incomplete": False,
            }
        limited = incomplete = False
        if query:
            # Accept names, not arbitrary qualifiers that could remove the owner scope.
            if not re.fullmatch(r"[A-Za-z0-9_. /-]+", query) or query.count("/") > 1:
                raise HTTPException(422, "Search by repository name or owner/name.")
            if "/" in query:
                owner, term = query.split("/", 1)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", owner):
                    raise HTTPException(422, "Use owner/name to search a repository.")
                owners = [owner]
            else:
                owners, term = self.search_owners(), query
                matching = [owner for owner in owners if query.lower() == owner.lower()]
                if matching:
                    owners, term = matching, ""
            scope = " ".join(f"user:{owner}" for owner in owners)
            terms = " ".join(f'"{word}"' for word in term.split())
            params = urlencode(
                {
                    "q": f"{terms} in:name fork:true {scope}",
                    "per_page": size,
                    "page": page,
                }
            )
            if (page - 1) * size >= 1000:
                raise HTTPException(422, "Narrow your search to see more results.")
            result = self.github(f"/search/repositories?{params}")
            batch = result["items"]
            total = result["total_count"]
            has_more = page * size < min(total, 1000)
            limited = total > 1000
            incomplete = bool(result.get("incomplete_results"))
        else:
            batch = self.github(f"/user/repos?per_page={size}&page={page}")
            has_more = len(batch) == size
        return {
            "repositories": [
                {
                    "full_name": r["full_name"],
                    "default_branch": r["default_branch"],
                    "html_url": f"https://github.com/{r['full_name']}",
                }
                for r in batch
            ],
            "page": page,
            "has_more": has_more,
            "limited": limited,
            "incomplete": incomplete,
        }

    def validate_factory(self, name, provider):
        if (
            not name
            or not REPO.fullmatch(name)
            or any(p in (".", "..") for p in name.split("/"))
        ):
            raise HTTPException(
                422, "Link a factory repository before enabling self-improvement."
            )
        if provider == "standin":
            repo = self.w.github.repos.get(name)
            if not repo:
                raise HTTPException(422, "The factory repository does not exist.")
            return repo.default_branch
        repo = self.github(f"/repos/{name}")
        if (
            repo.get("archived")
            or repo.get("disabled")
            or not repo.get("permissions", {}).get("push")
        ):
            raise HTTPException(
                422, "The factory repository must be writable and active."
            )
        return repo["default_branch"]

    def disable(self):
        settings = self.settings()
        settings["self_improvement"] = False
        self.w.store.set_meta(KEY, settings)

    def save(self, body):
        settings = body.model_dump()
        for name in settings["repositories"]:
            if not REPO.fullmatch(name) or any(
                p in (".", "..") for p in name.split("/")
            ):
                raise HTTPException(422, "Repositories must use owner/name.")
        if len({r.rsplit("/", 1)[-1].lower() for r in body.repositories}) != len(
            body.repositories
        ):
            raise HTTPException(
                422, "Selected repositories must have distinct checkout names."
            )
        if body.provider == "github":
            self.token()
            # Validate through the API before exporting repository names to the runtime.
            for name in body.repositories:
                self.github(f"/repos/{name}")
        elif any(name not in self.w.github.repos for name in body.repositories):
            raise HTTPException(422, "A selected stand-in repository does not exist.")
        if body.factory_repository:
            if body.factory_repository not in body.repositories:
                raise HTTPException(
                    422, "Select the factory repository in the repository list first."
                )
            settings["factory_branch"] = self.validate_factory(
                body.factory_repository, body.provider
            )
        elif body.self_improvement:
            raise HTTPException(
                422, "Link a factory repository before enabling self-improvement."
            )
        self.w.store.set_meta(KEY, settings)
        self.write_environment()
        return self.public()

    def environment(self):
        settings = self.settings()
        base = (self.w.internal_url or self.w.base_url).rstrip("/")
        env = {
            "FACTORY_SELF_IMPROVEMENT": str(settings["self_improvement"]).lower(),
            "FACTORY_LINKED_REPOSITORY": settings.get("factory_repository") or "",
            "FACTORY_SETTINGS_URL": f"{base}/factory/repository-policy",
        }
        if self.w.store.meta(KEY) is not None:
            env["GITHUB_REPOSITORIES"] = json.dumps(settings["repositories"])
        if settings["provider"] == "github":
            try:
                token = self.token()
            except HTTPException:
                token = (
                    ""  # Fail closed, never reuse a stand-in token or expired token.
                )
            connection = self.connection() or {}
            if not token:
                # Keep chat usable after disconnect: do not fail startup trying
                # to clone private repositories with an absent credential.
                env["GITHUB_REPOSITORIES"] = "[]"
            env.update(
                GITHUB_MCP_URL="https://api.githubcopilot.com/mcp/",
                GITHUB_MCP_TOKEN=token,
                GITHUB_GIT_URL="",
                GITHUB_ACCOUNT=connection.get("login", ""),
                GITHUB_WEBHOOK_SECRET=self.oauth_config().get("webhook_secret")
                or os.environ.get("WORKPLACE_GITHUB_WEBHOOK_SECRET", ""),
            )
        else:
            env["GITHUB_ACCOUNT"] = ""  # Clear a previous real identity on switch back.
        env["FACTORY_LINKED_BRANCH"] = settings.get("factory_branch", "main")
        env["FACTORY_REPOSITORY_REVISION"] = self.revision()
        return env

    def revision(self):
        settings = self.settings()
        connection = self.connection() if settings["provider"] == "github" else None
        scope = {
            k: settings.get(k)
            for k in ("provider", "repositories", "factory_repository")
        }
        scope["identity"] = (connection or {}).get("login")
        return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()

    def write_environment(self):
        config = self.w.factory_config
        if config is not None:
            config.write_environment(self.w)

    def policy(self):
        settings = self.settings()
        enabled = bool(
            settings["self_improvement"] and settings.get("factory_repository")
        )
        if enabled:
            try:
                self.validate_factory(
                    settings["factory_repository"], settings["provider"]
                )
            except HTTPException:
                self.disable()
                enabled = False
        return {
            "enabled": enabled,
            "repository": settings.get("factory_repository"),
            "provider": settings["provider"],
            "branch": settings.get("factory_branch", "main"),
            "revision": self.revision(),
        }

    async def maintain(self):
        while True:
            await asyncio.sleep(30)
            try:
                with self.lock:
                    self.device.expire()
                await asyncio.to_thread(self.policy)
                await asyncio.to_thread(self.write_environment)
            except Exception:
                # No tokens or response bodies in logs. Policy readers fail closed.
                import logging

                logging.getLogger(__name__).warning(
                    "GitHub settings refresh failed; will retry"
                )


def repository_router(w):
    router = APIRouter()
    settings = w.repository_settings

    @router.get("/workplace/api/repositories")
    def get_settings(request: Request):
        admin(request)
        return settings.public()

    @router.put("/workplace/api/repositories")
    def save_settings(body: RepositorySettingsBody, request: Request):
        admin(request)
        return settings.save(body)

    @router.get("/workplace/api/repositories/available")
    def available(
        request: Request,
        provider: Literal["standin", "github"] | None = None,
        page: int = Query(default=1, ge=1, le=100000),
        q: str = Query(default="", max_length=200),
    ):
        admin(request)
        return settings.available(provider, page=page, query=q)

    @router.get("/workplace/api/repositories/view")
    def view(request: Request):
        account = me(request)
        value = settings.settings()
        return {
            "provider": value["provider"],
            "repositories": value["repositories"] if account.admin else [],
        }

    def device_owner(request):
        account = admin(request)
        session = hashlib.sha256(
            request.cookies.get(SESSION_COOKIE, "").encode()
        ).hexdigest()
        return account, (account.id, session)

    @router.post("/workplace/api/repositories/github/device")
    def start_device(request: Request):
        _, owner = device_owner(request)
        with settings.lock:
            result = settings.device.start(owner)
            w.store.set_meta("repository.github.oauth", None)
            return result

    @router.post("/workplace/api/repositories/github/device/{attempt}")
    def poll_device(attempt: str, request: Request):
        account, owner = device_owner(request)
        with settings.lock:
            pending = settings.device.check(owner, attempt)
            if pending["process"].poll() is None:
                return settings.device.view()
            try:
                if pending["process"].returncode:
                    raise HTTPException(
                        400,
                        "GitHub sign-in was declined, expired, or failed. Start again.",
                    )
                token = settings.device.token()
                user = settings.github("/user", token=token)
                settings._connection(
                    {
                        "access_token": token,
                        "auth_type": "device",
                        "login": user["login"],
                        "account_id": account.id,
                    }
                )
                w.store.set_meta("repository.github.oauth", None)
                w.store.set_meta(
                    KEY,
                    {
                        "provider": "github",
                        "repositories": [],
                        "factory_repository": None,
                        "self_improvement": False,
                    },
                )
                settings.write_environment()
                return {"status": "connected", "settings": settings.public()}
            finally:
                settings.device.close()

    @router.delete("/workplace/api/repositories/github/device/{attempt}")
    def cancel_device(attempt: str, request: Request):
        _, owner = device_owner(request)
        with settings.lock:
            pending = settings.device.pending
            if pending and secrets.compare_digest(pending["id"], attempt):
                settings.device.check(owner, attempt)
                settings.device.close()
        return {"ok": True}

    @router.put("/workplace/api/repositories/github/token")
    def connect_token(body: GitHubTokenBody, request: Request):
        account = admin(request)
        # Validate before replacing a working connection. Never echo GitHub's response.
        token = body.token.get_secret_value().strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,512}", token):
            raise HTTPException(422, "Enter a valid personal access token.")
        user = settings.github("/user", token=token)
        with settings.lock:
            settings.device.close()
            settings._connection(
                {
                    "access_token": token,
                    "auth_type": "pat",
                    "login": user["login"],
                    "account_id": account.id,
                }
            )
            w.store.set_meta("repository.github.oauth", None)
            w.store.set_meta(
                KEY,
                {
                    "provider": "github",
                    "repositories": [],
                    "factory_repository": None,
                    "self_improvement": False,
                },
            )
            settings.write_environment()
        return settings.public()

    @router.post("/workplace/api/repositories/github/connect")
    def connect(request: Request):
        account = admin(request)
        if not settings.public()["oauth_configured"]:
            raise HTTPException(
                409,
                "Configure the GitHub App client ID and secret in repository settings first.",
            )
        with settings.lock:
            settings.device.close()
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        session = hashlib.sha256(
            request.cookies.get(SESSION_COOKIE, "").encode()
        ).hexdigest()
        w.store.set_meta(
            "repository.github.oauth",
            {
                "state": state,
                "verifier": verifier,
                "account_id": account.id,
                "session": session,
                "expires": time.time() + 600,
            },
        )
        params = {
            "client_id": settings.oauth_config()["client_id"],
            "state": state,
            "redirect_uri": w.base_url.rstrip("/") + CALLBACK,
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            )
            .decode()
            .rstrip("="),
            "code_challenge_method": "S256",
        }
        return {"url": "https://github.com/login/oauth/authorize?" + urlencode(params)}

    @router.get(CALLBACK)
    def callback(request: Request, state: str = "", code: str = ""):
        account = admin(request)
        with settings.lock:
            pending = w.store.meta("repository.github.oauth") or {}
            session = hashlib.sha256(
                request.cookies.get(SESSION_COOKIE, "").encode()
            ).hexdigest()
            if (
                not state
                or not secrets.compare_digest(state, pending.get("state", ""))
                or pending.get("account_id") != account.id
                or pending.get("session") != session
                or pending.get("expires", 0) < time.time()
            ):
                raise HTTPException(
                    400,
                    "GitHub sign-in expired or belongs to another session. Connect again.",
                )
            w.store.set_meta("repository.github.oauth", None)
        if not code:
            return RedirectResponse("/github?connection=declined", status_code=303)
        tokens = settings.exchange(
            {
                "code": code,
                "code_verifier": pending["verifier"],
                "redirect_uri": w.base_url.rstrip("/") + CALLBACK,
            }
        )
        user = settings.github("/user", token=tokens["access_token"])
        settings._connection(
            {**tokens, "login": user["login"], "account_id": account.id}
        )
        # A new identity never inherits another connection's selected repositories or opt-in.
        w.store.set_meta(
            KEY,
            {
                "provider": "github",
                "repositories": [],
                "factory_repository": None,
                "self_improvement": False,
            },
        )
        settings.write_environment()
        return RedirectResponse("/github?connection=connected", status_code=303)

    @router.put("/workplace/api/repositories/github/app")
    def save_app(body: GitHubAppBody, request: Request):
        admin(request)
        return settings.save_app(body)

    @router.post("/github/events")
    async def events(request: Request):
        value = settings.settings()
        secret = settings.oauth_config().get("webhook_secret") or os.environ.get(
            "WORKPLACE_GITHUB_WEBHOOK_SECRET", ""
        )
        body = await request.body()
        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        if (
            value["provider"] != "github"
            or not secret
            or not hmac.compare_digest(
                signature, request.headers.get("x-hub-signature-256", "")
            )
        ):
            raise HTTPException(401, "Invalid GitHub signature or inactive provider")
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "Invalid GitHub event") from None
        if request.headers.get("x-github-event") == "ping":
            return {"ok": True}
        if payload.get("repository", {}).get("full_name") not in value["repositories"]:
            return {"ignored": True}
        factory = w.factory_config.load().get("factory", {}) if w.factory_config else {}
        if not factory.get("url"):
            raise HTTPException(503, "Factory runtime is not configured")
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                response = await client.post(
                    factory["url"].rstrip("/") + "/integrations/github/events",
                    content=body,
                    headers={
                        k: request.headers[k]
                        for k in (
                            "x-hub-signature-256",
                            "x-github-event",
                            "x-github-delivery",
                        )
                        if k in request.headers
                    },
                )
            except httpx.HTTPError:
                raise HTTPException(
                    503, "Factory runtime is unavailable; retry this delivery"
                ) from None
        if response.is_error:
            raise HTTPException(
                503,
                "Factory runtime has not accepted this delivery; retry it after settings reload",
            )
        return {"ok": True}

    @router.delete("/workplace/api/repositories/github/connection")
    def disconnect(request: Request):
        admin(request)
        with settings.lock:
            settings.device.close()
            settings._connection(None)
        settings.disable()
        w.store.set_meta("repository.github.oauth", None)
        settings.write_environment()
        return settings.public()

    @router.get("/factory/repository-policy")
    def policy(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        config = w.factory_config.load() if w.factory_config else {}
        app = w.apps.by_username((config.get("app") or {}).get("username", ""))
        if (
            not token
            or not app
            or app.removed
            or not secrets.compare_digest(token, app.memory_token)
        ):
            raise HTTPException(401, "Unauthorized")
        return settings.policy()

    return router
