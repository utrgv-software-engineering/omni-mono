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
from .governance import Services as AllowedServices
from .governance import Decision as GovernanceDecision
from .governance import Owners as GovernanceOwners
from .governance import ReviewDone
from .egress import Allowlist as EgressList
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
    from .easy_auth import EasyAuth

    azure_auth = EasyAuth(i.base_url)
    app.add_middleware(
        SignedIn,
        accounts=i.accounts,
        base_url=i.base_url,
        identity=azure_auth if azure_auth.enabled else None,
    )
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

    azure_auth.install(app, i.accounts)
    azure_auth.require(i.policy, i.accounts)

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

    @app.get("/workplace/api/installation/allowed-services")
    def allowed_services(request: Request):
        from .installation import SERVICES_KEY

        me(request)
        return {"services": i.store.meta(SERVICES_KEY) or []}

    @app.put("/workplace/api/installation/allowed-services")
    def save_allowed_services(body: AllowedServices, request: Request):
        from .installation import SERVICES_KEY

        identity = admin(request)
        record = i.set_allowed_services(identity, body.services, body.reason)
        return {
            "services": i.store.meta(SERVICES_KEY) or [],
            "pending": record if record["status"] == "pending" else None,
        }

    @app.get("/workplace/api/installation/governance")
    def installation_governance(request: Request):
        from .installation import OWNERS_KEY

        identity = me(request)
        factories = []
        for record in i.records.values():
            if record["id"] in i.instances and (
                identity.admin or i.membership(record["id"], identity.id)
            ):
                g = i.instances[record["id"]][0].governance
                factories.append(
                    {
                        "id": record["id"],
                        "name": record["name"],
                        "profile": g.profile(),
                        "decisions": g.decisions.all(),
                    }
                )
        return {
            "owner": i.is_governance_owner(identity),
            "owners": i.store.meta(OWNERS_KEY) or [],
            "gated": i.policy.strict,
            "decisions": i.decisions.all(),
            "factories": factories,
        }

    @app.post("/workplace/api/installation/governance/decisions/{decision_id}")
    def installation_decide(
        decision_id: str, body: GovernanceDecision, request: Request
    ):
        identity = me(request)
        if not i.is_governance_owner(identity):
            raise HTTPException(403, "Only the governance owner decides")
        if i.decisions.get(decision_id):
            return i.decisions.decide(
                decision_id, identity.username, body.approve, body.reasoning
            )
        for w, _ in i.instances.values():
            if w.governance.decisions.get(decision_id):
                return w.governance.decisions.decide(
                    decision_id, identity.username, body.approve, body.reasoning
                )
        raise HTTPException(404, "No such decision")

    @app.get("/workplace/api/installation/egress")
    def installation_egress(request: Request):
        from .installation import EGRESS_KEY

        me(request)
        return {
            "enabled": bool(i.egress_proxy),
            "deployment": i.store.meta(EGRESS_KEY) or [],
        }

    @app.put("/workplace/api/installation/egress")
    def save_installation_egress(body: EgressList, request: Request):
        from .egress import set_deployment
        from .installation import EGRESS_KEY

        identity = admin(request)
        pending = set_deployment(
            account=identity,
            entries=body.allow or [],
            reason=body.reason,
            current=i.store.meta(EGRESS_KEY) or [],
            save=i.save_egress,
            decisions=i.decisions,
            gated=i.policy.strict,
            owner=i.is_governance_owner(identity),
            audit_log=i.audit_log,
        )
        return {"deployment": i.store.meta(EGRESS_KEY) or [], "pending": pending}

    @app.get("/workplace/api/installation/inventory")
    def inventory(request: Request):
        from .governance_reports import card_markdown

        me(request)
        cards = i.cards()
        return {"cards": cards, "markdown": "\n".join(card_markdown(c) for c in cards)}

    @app.get("/workplace/api/installation/reviews")
    def reviews(request: Request):
        me(request)
        return {
            "calendar": i.reviews.calendar(i.governance_factories()),
            "completed": i.reviews.completions(),
        }

    @app.post("/workplace/api/installation/reviews")
    def complete_review(body: ReviewDone, request: Request):
        from .governance_reports import REVIEWS

        identity = me(request)
        performer = {r[0]: r[3] for r in REVIEWS}.get(body.kind)
        if performer is None:
            raise HTTPException(422, "Unknown review")
        allowed = (
            identity.admin
            if performer == "administrator"
            else i.is_governance_owner(identity)
        )
        if not allowed:
            raise HTTPException(403, f"The {performer} records this review")
        return i.reviews.complete(
            body.kind, identity.username, body.result, body.factory
        )

    def _may_report(identity):
        if not (identity.admin or i.is_governance_owner(identity)):
            raise HTTPException(
                403, "Reports are for administrators and the governance owner"
            )

    @app.get("/workplace/api/installation/quarterly-report")
    def quarterly(quarter: str, request: Request):
        from .governance_reports import quarterly_report

        identity = me(request)
        _may_report(identity)
        try:
            text = quarterly_report(
                i.store.meta("installation.name") or "Workplace",
                quarter,
                i.governance_sources(),
                i.cards(),
                i.reviews.calendar(i.governance_factories()),
                i.reviews.completions(),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        i.audit_log.record(
            "governance.report_generated", actor=identity.username, quarter=quarter
        )
        return Response(text, media_type="text/markdown")

    @app.get("/workplace/api/installation/evidence")
    def evidence(request: Request, since: float = 0, until: float = 0):
        import time as _time

        from .governance_reports import (
            evidence_export,
            quarter_bounds,
            quarterly_report,
        )

        identity = me(request)
        _may_report(identity)
        until = until or _time.time()
        quarters = {}
        year = _time.gmtime(since or until).tm_year
        for y in range(year, _time.gmtime(until).tm_year + 1):
            for q in range(1, 5):
                name = f"{y}Q{q}"
                start, end = quarter_bounds(name)
                if start < until and end > since:
                    quarters[name] = quarterly_report(
                        i.store.meta("installation.name") or "Workplace",
                        name,
                        i.governance_sources(),
                        i.cards(),
                        i.reviews.calendar(i.governance_factories()),
                        i.reviews.completions(),
                    )
        i.audit_log.record(
            "evidence.exported", actor=identity.username, since=since, until=until
        )
        return evidence_export(
            i.governance_sources(),
            i.cards(),
            i.reviews.calendar(i.governance_factories()),
            i.reviews.completions(),
            since,
            until,
            quarters,
        )

    @app.put("/workplace/api/installation/governance-owners")
    def governance_owners(body: GovernanceOwners, request: Request):
        identity = admin(request)
        try:
            i.set_governance_owners(identity, body.account_ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"owners": body.account_ids}

    @app.get("/workplace/api/installation/access-review")
    def access_review(request: Request):
        """Everyone with access, by factory, for the annual access review (UTS 165)."""
        import time

        identity = admin(request)
        people = i.accounts.accounts
        review = {
            "generated_at": time.time(),
            "generated_by": identity.username,
            "installation_admins": [
                {"username": p.username, "name": p.name, "disabled": p.disabled}
                for p in people.values()
                if p.admin
            ],
            "factories": [
                {
                    "id": record["id"],
                    "name": record["name"],
                    "members": [
                        {
                            "username": people[m["identity_id"]].username,
                            "name": people[m["identity_id"]].name,
                            "role": m["role"],
                            "granted_at": m.get("granted_at"),
                            "disabled": people[m["identity_id"]].disabled,
                        }
                        for m in i.members.values()
                        if m["factory_id"] == record["id"]
                        and m["identity_id"] in people
                    ],
                }
                for record in i.records.values()
            ],
        }
        i.audit_log.record("access_review.exported", actor=identity.username)
        return review

    @app.put("/workplace/api/factories/{factory_id}/members")
    def membership(factory_id: str, body: MemberChange, request: Request):
        identity = admin(request)
        try:
            i.grant(factory_id, body.account_id, body.role, actor=identity.username)
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
