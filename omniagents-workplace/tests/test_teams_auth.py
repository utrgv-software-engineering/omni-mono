import base64
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import Account, Accounts, SESSION_COOKIE
from omniagents_workplace.easy_auth import EasyAuth
from omniagents_workplace.server import SignedIn
from omniagents_workplace.store import Store
from omniagents_workplace.teams_auth import ANCESTORS, PREFIX, TeamsAuth, TeamsMiddleware

TENANT = "11111111-1111-1111-1111-111111111111"
OBJECT = "22222222-2222-2222-2222-222222222222"
CLIENT = "33333333-3333-3333-3333-333333333333"
BASE = "https://workplace.example"


def headers(**changes):
    claims = {"tid": TENANT, "oid": OBJECT, "azp": CLIENT, "scp": "access_as_user", "exp": str(time.time() + 600), **changes}
    principal = {"auth_typ": "aad", "claims": [{"typ": key, "val": value} for key, value in claims.items() if value is not None]}
    return {"x-ms-client-principal": base64.b64encode(json.dumps(principal).encode()).decode(), "x-workplace-proxy-token": "proxy-secret", "origin": BASE}


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "teams.db")
    accounts = Accounts(store)
    accounts.add(Account("alice", "alice", "Alice", "alice@example.test", "", "", ""))
    identity = EasyAuth(BASE, {"WORKPLACE_AZURE_EASY_AUTH": "true", "WORKPLACE_AZURE_TENANT_ID": TENANT, "WORKPLACE_AZURE_ACCOUNT_MAP": json.dumps({OBJECT: "alice"}), "WORKPLACE_AZURE_PROXY_TOKEN": "proxy-secret"})
    teams = TeamsAuth(identity, accounts, {"WORKPLACE_TEAMS_CLIENT_ID": CLIENT})
    app = FastAPI()
    teams.install(app)
    app.add_middleware(SignedIn, accounts=accounts, base_url=BASE, identity=identity)
    app.add_middleware(TeamsMiddleware, teams=teams)

    @app.get("/workplace/api/state")
    def state(request: Request):
        return {"id": request.state.account.id}

    @app.get("/workplace/api/shared/content")
    def content(request: Request):
        return {"id": request.state.account.id, "query": str(request.query_params)}

    @app.get("/workplace/api/session")
    def session(request: Request):
        account = accounts.signed_in(request.cookies.get(SESSION_COOKIE))
        return {"id": account.id if account else None}

    @app.websocket("/workplace/api/desktop/ws")
    async def socket(websocket: WebSocket):
        account = accounts.signed_in(websocket.cookies.get(SESSION_COOKIE))
        if account is None:
            await websocket.close(1008)
            return
        await websocket.accept()
        await websocket.send_text(account.id)
        await websocket.close()

    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, accounts=accounts, teams=teams)
    store.close()


def start(setup):
    response = setup.client.post(PREFIX + "/session", headers=headers())
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    return {**headers(), "x-workplace-session": response.json()["session"]}


def test_cookie_free_session_and_expiry(setup):
    authenticated = start(setup)
    assert setup.client.get("/workplace/api/state", headers=authenticated).json() == {"id": "alice"}
    assert setup.client.get("/workplace/api/session", headers=authenticated).json() == {"id": "alice"}
    entry = next(iter(setup.accounts._sessions.values()))
    assert entry.expires_at <= time.time() + 300
    entry.expires_at = 0
    assert setup.client.get("/workplace/api/state", headers=authenticated).status_code == 401


@pytest.mark.parametrize("changes", [{"tid": CLIENT}, {"oid": CLIENT}, {"azp": OBJECT}, {"scp": "User.Read"}, {"scp": None}, {"exp": "0"}, {"exp": "NaN"}, {"exp": None}])
def test_rejects_wrong_identity_scope_client_and_expiry(setup, changes):
    assert setup.client.post(PREFIX + "/session", headers=headers(**changes)).status_code == 403
    assert not setup.accounts._sessions


def test_proxy_and_origin_required(setup):
    for name in ["x-workplace-proxy-token", "origin"]:
        value = headers()
        value.pop(name)
        assert setup.client.post(PREFIX + "/session", headers=value).status_code == 403
    assert setup.client.post(PREFIX + "/session", headers={**headers(), "origin": "https://evil.example"}).status_code == 403


def test_credential_is_not_sufficient_without_fresh_identity(setup):
    authenticated = start(setup)
    assert setup.client.get("/workplace/api/state", headers={"x-workplace-session": authenticated["x-workplace-session"]}).status_code == 403
    setup.accounts.update(setup.accounts.accounts["alice"], disabled=True)
    assert setup.client.get("/workplace/api/state", headers=authenticated).status_code == 403


def test_refresh_preserves_session_and_scope_checks(setup):
    authenticated = start(setup)
    response = setup.client.post(PREFIX + "/session", headers=authenticated)
    assert response.json()["session"] == authenticated["x-workplace-session"]
    assert len(setup.accounts._sessions) == 1


def test_download_ticket_is_exact_and_revocable(setup):
    authenticated = start(setup)
    path = "/workplace/api/shared/content?path=report.pdf"
    response = setup.client.post(PREFIX + "/ticket", headers=authenticated, json={"path": path})
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    url = path + "&workplace_ticket=" + ticket
    assert setup.client.get(url).json() == {"id": "alice", "query": "path=report.pdf"}
    assert setup.client.get(url.replace("report.pdf", "other.pdf")).status_code == 401
    assert setup.client.get(url.replace("shared/content", "state")).status_code == 401
    assert setup.client.post(url, json={}).status_code == 401
    setup.accounts.sign_out(authenticated["x-workplace-session"])
    assert setup.client.get(url).status_code == 401


@pytest.mark.parametrize("path", ["https://evil.example/file", "/workplace/api/me/tokens", "/workplace/api/shared/content#fragment", "/workplace/api/shared/content?workplace_ticket=x", "/workplace/api/shared/%63ontent", "/workplace/api/shared/../state"])
def test_rejects_unscoped_ticket_targets(setup, path):
    assert setup.client.post(PREFIX + "/ticket", headers=start(setup), json={"path": path}).status_code == 400


def test_socket_ticket_single_use(setup):
    path = "/workplace/api/desktop/ws"
    ticket = setup.client.post(PREFIX + "/ticket", headers=start(setup), json={"path": path}).json()["ticket"]
    with setup.client.websocket_connect(path + "?workplace_ticket=" + ticket, headers={"origin": BASE}) as socket:
        assert socket.receive_text() == "alice"
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with setup.client.websocket_connect(path + "?workplace_ticket=" + ticket, headers={"origin": BASE}):
            pass


def test_framing_and_browser_auth_preserved(setup):
    response = setup.client.get("/workplace/api/session")
    assert "x-frame-options" not in response.headers
    assert ANCESTORS in response.headers["content-security-policy"]
    token = setup.accounts.start_session(setup.accounts.accounts["alice"])
    setup.client.cookies.set(SESSION_COOKIE, token)
    assert setup.client.get("/workplace/api/state", headers=headers()).status_code == 200
    assert setup.client.get("/workplace/api/state").status_code == 401


def test_ticket_expiry_and_cross_origin_socket(setup):
    path = "/workplace/api/desktop/ws"
    ticket = setup.client.post(PREFIX + "/ticket", headers=start(setup), json={"path": path}).json()["ticket"]
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with setup.client.websocket_connect(path + "?workplace_ticket=" + ticket, headers={"origin": "https://evil.example"}):
            pass
    for entry in setup.teams.tickets.values():
        entry["expires"] = 0
    with pytest.raises(WebSocketDisconnect):
        with setup.client.websocket_connect(path + "?workplace_ticket=" + ticket, headers={"origin": BASE}):
            pass


def test_disabled_feature_keeps_browser_framing(setup):
    setup.teams.enabled = False
    response = setup.client.get("/workplace/api/session")
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "content-security-policy" not in response.headers


def test_builtin_bridge_keeps_teams_identity(setup):
    from omniagents_workplace.extensions import builtin
    app = setup.client.app
    registry = SimpleNamespace(get=lambda name: {"enabled": True})

    @app.post("/workplace/api/extensions/test")
    async def bridge(request: Request):
        return await builtin.request(registry, "slack", {"path": "/workplace/api/state"}, request)

    response = setup.client.post("/workplace/api/extensions/test", headers=start(setup), json={})
    assert response.status_code == 200
    assert response.json()["status"] == 200


def test_mcp_frames_allow_teams_ancestors_without_changing_parent_trust():
    from omniagents_workplace.extensions.sandbox import AppSandboxOrigin, isolated_origin
    origin = isolated_origin(BASE, "slack", "https://apps.example")
    app = AppSandboxOrigin(FastAPI(), base_url=BASE, configured="https://apps.example", frame_ancestors=ANCESTORS)
    with TestClient(app, base_url=origin) as client:
        response = client.get('/mcp-app-sandbox')
        assert response.status_code == 200
        assert 'frame-ancestors ' + BASE + ' ' + ANCESTORS in response.headers['content-security-policy']
        assert 'const expected=' + json.dumps(BASE) in response.text
        assert client.get('/workplace/api/session').status_code == 404
