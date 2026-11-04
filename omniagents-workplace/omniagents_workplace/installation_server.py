"""Shared authentication and explicit factory routing for an installation."""

import asyncio
import re
import secrets
from importlib import resources
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, StrictInt

from .kill_switch import Reason as StopReason
from .accounts import (
    SESSION_COOKIE,
    Account,
    AccountError,
    Accounts,
    admin,
    me,
    valid_username,
)
from .server import (
    AcceptInvite,
    ChangePassword,
    RequestLimits,
    Setup,
    SignedIn,
    SignIn,
    _cookie,
    _serve_ui,
    person_json,
)


class FactoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    project: str = ""


class MemberChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    role: Literal["admin", "member"] | None


class PersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    name: str
    email: str


class PreviewSetupRequest(BaseModel):
    repository: str
    path: str = ""
    revision: str = ""
    provider: Literal["standin", "github"] | None = None
    services: dict[str, StrictInt] = {}
    variables: dict[str, str] = {}
    # Open pull requests to preview as well as those opened from now on.
    include: list[StrictInt] = []


class FactoryRouter:
    def __init__(self, app, installation):
        self.app, self.installation = app, installation

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        server = scope.get("server") or ("", 0)
        if (
            self.installation.sandbox_port
            and server[1] == self.installation.sandbox_port
        ):
            from .server import _SANDBOX_PATHS

            normalized = re.sub(r"^/factories/[a-f0-9]{32}", "", path)
            if not _SANDBOX_PATHS.match(normalized):
                return await self.reject(
                    scope, receive, send, 403, "Sandbox service endpoint only"
                )
        match = re.match(r"^/factories/([a-f0-9]{32})(/.*)?$", path)
        if not match:
            legacy = self.installation.store.meta("installation.legacy_factory")
            if legacy and path.startswith(
                (
                    "/slack/",
                    "/github/",
                    "/linear/",
                    "/canvas/view/",
                    "/canvas/mcp",
                    "/library/",
                    "/memory/",
                    "/factory/repository-policy",
                    "/factory/events/",
                )
            ):
                return await self(
                    {
                        **scope,
                        "path": "/factories/" + legacy + path,
                        "raw_path": ("/factories/" + legacy).encode()
                        + scope.get("raw_path", path.encode()),
                        "state": {
                            **scope.get("state", {}),
                            "legacy_factory_path": True,
                        },
                    },
                    receive,
                    send,
                )
            if path.startswith("/factories/"):
                return await self.reject(scope, receive, send, 404, "No such factory")
            return await self.app(scope, receive, send)
        factory_id, inner = match[1], match[2] or "/"
        server = scope.get("server") or ("", 0)
        if (
            self.installation.sandbox_port
            and server[1] == self.installation.sandbox_port
        ):
            from .server import _SANDBOX_PATHS

            if not _SANDBOX_PATHS.match(inner):
                return await self.reject(
                    scope, receive, send, 403, "Sandbox service endpoint only"
                )
        install = self.installation
        if factory_id not in install.records:
            return await self.reject(scope, receive, send, 404, "No such factory")
        headers = {
            key.decode().lower(): value.decode() for key, value in scope["headers"]
        }
        cookie = _cookie(headers.get("cookie", ""), SESSION_COOKIE)
        # Machine-facing services retain their own per-factory bearer/signature
        # checks. Human UI/API/viewer routes require installation membership.
        machine = inner.startswith(
            (
                "/slack/",
                "/coolify/mcp",
                "/github/",
                "/linear/",
                "/canvas/mcp",
                "/canvas/view/",
                "/library/view/",
                "/share/",
                "/memory/",
                "/library/api/",
                "/factory/repository-policy",
                "/factory/events/",
            )
        )

        def authorized():
            identity = install.accounts.signed_in(cookie)
            return (
                (install.membership(factory_id, identity.id) or {}).get("role")
                if identity
                else None
            )

        initial_role = authorized()
        if not machine and not initial_role:
            if scope["type"] == "http" and not inner.startswith("/workplace/"):
                return await RedirectResponse("/", status_code=303)(
                    scope, receive, send
                )
            return await self.reject(
                scope, receive, send, 403, "No access to this factory"
            )
        if inner == "/coolify/mcp":
            target = f"/factory-deployments/{factory_id}/mcp"
            return await self.app(
                {**scope, "path": target, "raw_path": target.encode()}, receive, send
            )
        if inner in {"/workplace/api/signout", "/workplace/api/me/password"}:
            return await self.app(
                {**scope, "path": inner, "raw_path": inner.encode()}, receive, send
            )
        if inner.startswith(
            (
                "/workplace/api/setup",
                "/workplace/api/claim",
                "/workplace/api/signin",
                "/workplace/api/invites",
            )
        ) or (
            inner.startswith("/workplace/api/people")
            and scope.get("method") not in {"GET", "HEAD"}
        ):
            return await self.reject(
                scope,
                receive,
                send,
                403,
                "Manage accounts and factory access from Factories",
            )
        try:
            _, app = await install.mount(factory_id)
        except Exception:
            return await self.reject(
                scope, receive, send, 503, "Factory is unavailable"
            )
        child_scope = {
            **scope,
            "path": inner,
            "raw_path": scope.get("raw_path", path.encode())[
                len("/factories/" + factory_id) :
            ]
            or b"/",
            "root_path": "",
            "state": {
                **scope.get("state", {}),
                "factory_prefix": (
                    ""
                    if scope.get("state", {}).get("legacy_factory_path")
                    else "/factories/" + factory_id
                ),
            },
        }
        original_send = send

        async def scoped_send(message):
            if message["type"] == "http.response.start":
                headers = []
                for key, value in message.get("headers", []):
                    if key.lower() == b"location":
                        url = urlsplit(value.decode("latin-1"))
                        own_hosts = {
                            urlsplit(install.base_url).netloc,
                            urlsplit(install.internal_url).netloc,
                        }
                        if (
                            (not url.netloc or url.netloc in own_hosts)
                            and url.path.startswith("/")
                            and not url.path.startswith("/factories/")
                        ):
                            value = urlunsplit(
                                url._replace(path="/factories/" + factory_id + url.path)
                            ).encode("latin-1")
                    headers.append((key, value))
                message = {**message, "headers": headers}
            await original_send(message)

        send = scoped_send
        # Browser sockets/SSE stop when the account/session/membership is revoked.
        if not machine and (
            scope["type"] == "websocket" or inner == "/workplace/api/stream"
        ):
            task = asyncio.create_task(app(child_scope, receive, send))

            async def watch():
                while authorized() == initial_role:
                    await asyncio.sleep(1)

            watcher = asyncio.create_task(watch())
            try:
                done, _ = await asyncio.wait(
                    [task, watcher], return_when=asyncio.FIRST_COMPLETED
                )
                if task in done:
                    await task
                elif scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send(
                        {"type": "http.response.body", "body": b"", "more_body": False}
                    )
            finally:
                task.cancel()
                watcher.cancel()
                await asyncio.gather(task, watcher, return_exceptions=True)
        else:
            await app(child_scope, receive, send)

    @staticmethod
    async def reject(scope, receive, send, status, detail):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"detail": detail}, status_code=status)(
                scope, receive, send
            )


def create_installation_app(installation, *, static_dir=None):
    i = installation
    i.static_dir = static_dir
    app = FastAPI(title="OmniAgents factories", lifespan=i.lifespan)
    app.add_middleware(SignedIn, accounts=i.accounts, base_url=i.base_url)
    app.add_middleware(RequestLimits)

    @app.exception_handler(AccountError)
    async def account_error(_request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    def cookie(response, token):
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=i.accounts.session_seconds,
            httponly=True,
            secure=i.base_url.startswith("https://"),
            samesite="lax",
            path="/",
        )

    from .easy_auth import EasyAuth

    azure_auth = EasyAuth(i.base_url)
    azure_auth.install(app, i.accounts)

    @app.get("/healthz")
    def health():
        i.store.meta("installation.name")
        return {"ok": True}

    @app.get("/workplace/api/session")
    def session(request: Request):
        account = i.accounts.signed_in(request.cookies.get(SESSION_COOKIE))
        return {
            "account": person_json(account, private=True) if account else None,
            "azure_auth": azure_auth.metadata(),
            "installation": True,
            "setup": i.accounts.needs_setup(),
            "setup_here": False,
            "organization": i.store.meta("installation.name", "Factories"),
            "environment": i.environment,
        }

    @app.post("/workplace/api/setup")
    def setup(body: Setup, response: Response):
        if not secrets.compare_digest(body.token, i.setup_token):
            raise HTTPException(403, "Use the setup link printed by the installation")
        with i.lock:
            if not i.accounts.needs_setup():
                raise HTTPException(409, "Installation is already set up")
            if not valid_username(body.username):
                raise HTTPException(422, "Invalid username")
            i.accounts.validate_password(body.password)
            account = i.accounts.add(
                Account(
                    id=Accounts.new_id(),
                    username=body.username,
                    name=body.name,
                    email=body.email,
                    slack_id="",
                    github_login="",
                    linear_id="",
                    admin=True,
                )
            )
            i.accounts.set_password(account, body.password)
            i.store.set_meta("installation.name", body.organization or "Factories")
            cookie(response, i.accounts.start_session(account))
            return {"account": person_json(account, private=True)}

    @app.post("/workplace/api/signin")
    def signin(body: SignIn, response: Response):
        account, token = i.accounts.sign_in(body.username, body.password)
        cookie(response, token)
        return {"account": person_json(account, private=True)}

    @app.post("/workplace/api/signout")
    def signout(request: Request, response: Response):
        i.accounts.sign_out(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.post("/workplace/api/me/password")
    def password(body: ChangePassword, request: Request, response: Response):
        cookie(
            response, i.accounts.change_password(me(request), body.current, body.new)
        )
        return {"ok": True}

    @app.get("/workplace/api/invites/{token}")
    def invite(token: str):
        account = i.accounts.invited(token)
        if not account:
            raise HTTPException(404, "Invalid invite")
        return {
            "username": account.username,
            "name": account.name,
            "organization": i.store.meta("installation.name", "Factories"),
        }

    @app.post("/workplace/api/invites/{token}")
    def accept(token: str, body: AcceptInvite, response: Response):
        account, session = i.accounts.accept_invite(token, body.password)
        cookie(response, session)
        return {"account": person_json(account, private=True)}

    @app.get("/workplace/api/factories")
    def factories(request: Request):
        identity = me(request)
        return {
            "template_available": i.template is not None,
            "factories": [
                i.view(r, identity)
                for r in i.records.values()
                if identity.admin or i.membership(r["id"], identity.id)
            ],
        }

    @app.post("/workplace/api/factories")
    async def create(body: FactoryCreate, request: Request):
        identity = admin(request)
        try:
            record = i.add(**body.model_dump(), owner=identity)
            await i.mount(record["id"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return i.view(record, identity)

    @app.get("/workplace/api/installation/stop")
    def installation_stop_state(request: Request):
        from .installation import STOP_KEY
        from .kill_switch import describe

        me(request)
        state = i.store.meta(STOP_KEY)
        return {"stopped": state, "message": describe(state)}

    @app.post("/workplace/api/installation/stop")
    async def stop_all(body: StopReason, request: Request):
        from .kill_switch import reason_of

        identity = admin(request)
        await i.stop_all(identity, reason_of(body))
        return installation_stop_state(request)

    @app.post("/workplace/api/installation/resume")
    async def resume_all(body: StopReason, request: Request):
        from .kill_switch import reason_of

        identity = admin(request)
        try:
            await i.resume_all(identity, reason_of(body))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return installation_stop_state(request)

    @app.post("/workplace/api/factories/{factory_id}/{action}")
    async def control(
        factory_id: str, action: Literal["start", "stop"], request: Request
    ):
        admin(request)  # Starting project code is an installation-owner operation.
        if not i.record(factory_id):
            raise HTTPException(404, "No such factory")
        try:
            await (i.start(factory_id) if action == "start" else i.stop(factory_id))
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return i.view(i.records[factory_id], me(request))

    def deployment_admin(factory_id, request):
        identity = i.accounts.signed_in(request.cookies.get(SESSION_COOKIE))
        if (
            not identity
            or (i.membership(factory_id, identity.id) or {}).get("role") != "admin"
        ):
            raise HTTPException(403, "Factory administrator access required")
        return identity

    @app.api_route(
        "/factory-deployments/{factory_id}/mcp", methods=["GET", "POST", "DELETE"]
    )
    async def coolify_mcp(factory_id: str, request: Request):
        from .deployments.mcp import proxy

        return await proxy(i, factory_id, request)

    @app.post("/workplace/api/factories/{factory_id}/previews/{action}")
    async def preview_setup(
        factory_id: str,
        action: Literal["inspect", "enable"],
        request: Request,
        body: PreviewSetupRequest,
    ):
        deployment_admin(factory_id, request)
        from .deployments.setup import PreviewSetup

        await i.mount(factory_id)
        body = body.model_dump()
        setup = PreviewSetup(i, factory_id)
        lock = i.deployments.mcp_locks.setdefault("setup:" + factory_id, asyncio.Lock())
        try:
            async with lock:
                if action == "inspect":
                    value = await setup.inspect(
                        body["repository"], body.get("path", "")
                    )
                    return {k: v for k, v in value.items() if k != "_raw"}
                return await setup.enable(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except RuntimeError:
            raise HTTPException(
                502,
                "Coolify setup failed. Check repository access and the Coolify logs, then retry.",
            ) from None

    @app.post("/workplace/api/factories/{factory_id}/coolify/{action}")
    async def deployment_action(
        factory_id: str, action: Literal["start", "stop"], request: Request
    ):
        deployment_admin(factory_id, request)
        i.deployments.configure(factory_id, action == "start")
        if action == "start":
            await i.deployments.start(factory_id)
        else:
            await i.deployments.stop(factory_id)
        return i.deployments.view(factory_id)

    @app.get("/workplace/deployments/open/{factory_id}")
    async def deployment_open(factory_id: str, request: Request):
        from urllib.parse import urlencode

        identity = deployment_admin(factory_id, request)
        state = request.query_params.get("state", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,100}", state):
            raise HTTPException(400, "Invalid sign-in state")
        i.deployments.identity.client(factory_id)
        try:
            await i.deployments.provision_user(factory_id, identity)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        code = i.deployments.identity.issue(
            "entry",
            {
                "factory": factory_id,
                "login": request.cookies[SESSION_COOKIE],
                "state": state,
            },
        )
        return RedirectResponse(
            i.deployments.url(factory_id)
            + "/__workplace/callback?"
            + urlencode({"code": code}),
            303,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.get("/workplace/api/factories/{factory_id}/members")
    def members(factory_id: str, request: Request):
        admin(request)
        return {
            "members": [m for m in i.members.values() if m["factory_id"] == factory_id]
        }

    @app.put("/workplace/api/factories/{factory_id}/members")
    def membership(factory_id: str, body: MemberChange, request: Request):
        admin(request)
        try:
            i.grant(factory_id, body.account_id, body.role)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.get("/workplace/api/installation/people")
    def people(request: Request):
        admin(request)
        return {"people": [person_json(a) for a in i.accounts.people()]}

    @app.post("/workplace/api/installation/people")
    def add_person(body: PersonCreate, request: Request):
        admin(request)
        if not valid_username(body.username):
            raise HTTPException(422, "Invalid username")
        account = i.accounts.add(
            Account(
                id=Accounts.new_id(),
                username=body.username,
                name=body.name,
                email=body.email,
                slack_id="",
                github_login="",
                linear_id="",
            )
        )
        return {
            "person": person_json(account),
            "invite_url": i.base_url + "/invite/" + i.accounts.invite(account),
        }

    @app.post("/workplace/api/installation/people/{account_id}/invite")
    def reinvite(account_id: str, request: Request):
        admin(request)
        account = i.accounts.accounts.get(account_id)
        if not account:
            raise HTTPException(404, "No such person")
        return {"invite_url": i.base_url + "/invite/" + i.accounts.invite(account)}

    _serve_ui(
        app,
        static_dir
        or Path(str(resources.files("omniagents_workplace").joinpath("static"))),
    )
    from .deployments.cookies import HostCookies
    from .deployments.gateway import DeploymentGateway

    from .crawl import NoIndex
    from .extensions.sandbox import AppSandboxOrigin
    import os

    return AppSandboxOrigin(
        HostCookies(DeploymentGateway(NoIndex(FactoryRouter(app, i)), i), i.base_url),
        base_url=i.base_url,
        configured=os.environ.get("WORKPLACE_APP_ORIGIN", ""),
        static_dir=static_dir,
    )
