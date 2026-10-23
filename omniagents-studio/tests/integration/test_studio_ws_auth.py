"""Connection-authentication tests for Studio's WebSocket surfaces.

Mirror of ``test_server_auth.py`` for the Studio server: every
``/api/**/ws`` route resolves its principal through the same
:class:`~omniagents.rpc.auth.ConnectionAuthenticator` model used by
``/ws``, ``/ws/realtime`` and ``/ws/terminal``:

* ``Authorization: Bearer`` headers and one-time ``/api/auth/ws-ticket``
  connect tickets are the supported credential transports.
* The legacy ``?token=`` query parameter is rejected unless
  ``OMNIAGENTS_LEGACY_QUERY_AUTH=1`` (documented migration path).
* Auth failures close with 4401 — a terminal code, no retry.
* Each agent's runtime (``/api/agents/<key>/runtime``) is the real
  omniagents server: the same credentials reach it, and its dispatch runs
  every request through ``AgentService.authorize_rpc``.
"""

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents.core.project.runtime import load_project
from omniagents.rpc.auth import WS_CLOSE_UNAUTHENTICATED
from omniagents.rpc.generated.gui_v2 import GUI_PROTOCOL_VERSION
from omniagents_studio.api import create_api_app

AUTH_HEADER = {"Authorization": "Bearer studio-secret"}


def _build_client(monkeypatch, *, legacy: bool = False, **kwargs):
    if legacy:
        monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "1")
    else:
        monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    app = create_api_app(auth_token="studio-secret", **kwargs)
    return app, TestClient(app)


@pytest.fixture
def auth_client(monkeypatch):
    app, client = _build_client(monkeypatch)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def auth_app_and_client(monkeypatch):
    app, client = _build_client(monkeypatch)
    try:
        yield app, client
    finally:
        client.close()


@pytest.fixture
def legacy_auth_client(monkeypatch):
    app, client = _build_client(monkeypatch, legacy=True)
    try:
        yield client
    finally:
        client.close()


def _mint(client) -> str:
    response = client.post("/api/auth/ws-ticket", headers=AUTH_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] > 0
    # The response never echoes the caller's access token.
    assert "studio-secret" not in response.text
    return body["ticket"]


# ---------------------------------------------------------------------------
# A representative Studio socket: a job stream for a job that does not exist.
# Authentication runs first, so the close code says how the dial fared:
# 4401 unauthenticated, 4404 authenticated (and no such job).
# ---------------------------------------------------------------------------

SOCKET = "/api/evaluation/jobs/no-such-job/ws"


def _dial(client, url: str = SOCKET, **kwargs) -> int:
    """How the dial ended: refused before acceptance (4401) or accepted and
    then closed (4404, no such job)."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, **kwargs) as ws:
            ws.receive_json()
    return exc.value.code


def test_socket_rejects_missing_credentials(auth_client):
    assert _dial(auth_client) == WS_CLOSE_UNAUTHENTICATED


def test_socket_rejects_wrong_header(auth_client):
    assert (
        _dial(auth_client, headers={"Authorization": "Bearer wrong"})
        == WS_CLOSE_UNAUTHENTICATED
    )


def test_socket_accepts_authorization_header(auth_client):
    assert _dial(auth_client, headers=AUTH_HEADER) == 4404


def test_socket_rejects_query_token_by_default(auth_client):
    """The legacy ?token= transport is off unless explicitly re-enabled."""
    assert (
        _dial(auth_client, f"{SOCKET}?token=studio-secret") == WS_CLOSE_UNAUTHENTICATED
    )


def test_socket_accepts_query_token_in_legacy_mode(legacy_auth_client):
    assert _dial(legacy_auth_client, f"{SOCKET}?token=studio-secret") == 4404


# ---------------------------------------------------------------------------
# /api/auth/ws-ticket — browser connect tickets
# ---------------------------------------------------------------------------


def test_ticket_endpoint_requires_authentication(auth_client):
    assert auth_client.post("/api/auth/ws-ticket").status_code == 401
    assert (
        auth_client.post(
            "/api/auth/ws-ticket", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )


def test_ticket_flow_dials_a_socket(auth_client):
    assert _dial(auth_client, f"{SOCKET}?ticket={_mint(auth_client)}") == 4404


def test_ticket_is_single_use(auth_client):
    ticket = _mint(auth_client)
    assert _dial(auth_client, f"{SOCKET}?ticket={ticket}") == 4404
    assert _dial(auth_client, f"{SOCKET}?ticket={ticket}") == WS_CLOSE_UNAUTHENTICATED


def test_expired_ticket_rejected(auth_app_and_client):
    app, client = auth_app_and_client
    ticket = _mint(client)
    broker = app.state.connection_authenticator.ticket_broker
    broker._pending[ticket].expires_at = time.monotonic() - 1
    assert _dial(client, f"{SOCKET}?ticket={ticket}") == WS_CLOSE_UNAUTHENTICATED


def test_invalid_ticket_rejected(auth_client):
    assert _dial(auth_client, f"{SOCKET}?ticket=forged") == WS_CLOSE_UNAUTHENTICATED


# ---------------------------------------------------------------------------
# Evaluation job-stream routes — same authenticator on every surface
# ---------------------------------------------------------------------------


def test_job_ws_requires_authentication(auth_client):
    # Unauthenticated: refused before the job lookup (4401).
    assert _dial(auth_client) == WS_CLOSE_UNAUTHENTICATED
    # Authenticated: passes auth and reaches the job lookup (4404).
    assert _dial(auth_client, headers=AUTH_HEADER) == 4404
    # A correct query token is no longer a credential.
    assert (
        _dial(auth_client, f"{SOCKET}?token=studio-secret") == WS_CLOSE_UNAUTHENTICATED
    )


# ---------------------------------------------------------------------------
# Token leakage
# ---------------------------------------------------------------------------


def test_token_absent_from_logs(auth_client, caplog, capsys):
    """A normal authenticated exchange never materializes the token in logs."""
    import logging

    with caplog.at_level(logging.DEBUG):
        assert _dial(auth_client, f"{SOCKET}?ticket={_mint(auth_client)}") == 4404
    assert "studio-secret" not in caplog.text
    captured = capsys.readouterr()
    assert "studio-secret" not in captured.out
    assert "studio-secret" not in captured.err


# ---------------------------------------------------------------------------
# Agent runtimes: the same credentials reach each agent's real server
# ---------------------------------------------------------------------------


@pytest.fixture
def project_resources(tmp_path):
    fixture_root = (
        Path(__file__).resolve().parent.parent / "fixtures" / "studio" / "basic_project"
    )
    destination = tmp_path / "project"
    shutil.copytree(fixture_root, destination)
    manifest_path = destination / "project.yml"
    context = load_project(str(manifest_path))
    return {
        "project_name": context.product.slug,
        "manifest_path": str(manifest_path),
    }


@pytest.fixture
def runtime_auth_client(monkeypatch, project_resources):
    app, client = _build_client(
        monkeypatch,
        default_project="auth-runtime",
        project_resources=project_resources,
    )
    try:
        yield client, "primary"
    finally:
        client.close()


def _initialize(ws) -> dict:
    ws.send_json(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocol_version": GUI_PROTOCOL_VERSION,
                "identity": {"name": "studio-tests", "version": "0.0.0"},
                "platform": {"os": "linux", "arch": "x86_64"},
                "capabilities": {
                    "realtime": False,
                    "mcp_apps": False,
                    "client_functions": False,
                    "approvals": True,
                    "artifacts": False,
                    "replay": True,
                    "terminal": False,
                    "experimental_operations": [],
                    "disabled_notifications": [],
                },
            },
        }
    )
    while True:
        message = ws.receive_json()
        if message.get("id") == "init":
            break
    ws.send_json({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    return message


def test_agent_runtime_ws_rejects_missing_credentials(runtime_auth_client):
    client, agent = runtime_auth_client
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/api/agents/{agent}/runtime/ws"):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_agent_runtime_ws_rejects_query_token_by_default(runtime_auth_client):
    client, agent = runtime_auth_client
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/agents/{agent}/runtime/ws?token=studio-secret"
        ):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_agent_runtime_ticket_requires_studio_credentials(runtime_auth_client):
    client, agent = runtime_auth_client
    response = client.post(f"/api/agents/{agent}/runtime/auth/ws-ticket")
    assert response.status_code == 401


def test_agent_runtime_is_the_real_gui_endpoint(runtime_auth_client):
    """A ticket from the runtime's own endpoint opens its /ws, which is the
    real GUI endpoint: nothing is served before the initialize handshake,
    and only GUI protocol methods are served after it."""
    client, agent = runtime_auth_client
    response = client.post(
        f"/api/agents/{agent}/runtime/auth/ws-ticket", headers=AUTH_HEADER
    )
    assert response.status_code == 200
    ticket = response.json()["ticket"]

    with client.websocket_connect(
        f"/api/agents/{agent}/runtime/ws?ticket={ticket}"
    ) as ws:
        ws.send_json(
            {"jsonrpc": "2.0", "id": 0, "method": "get_agent_info", "params": {}}
        )
        assert ws.receive_json()["error"]["code"] == -32010
        initialized = _initialize(ws)
        assert initialized["result"]["protocol_version"] == GUI_PROTOCOL_VERSION
        ws.send_json(
            {"jsonrpc": "2.0", "id": 1, "method": "get_agent_info", "params": {}}
        )
        reply = ws.receive_json()
        assert reply["id"] == 1 and "result" in reply

        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "disable_tool",
                "params": {"tool_name": "greet_tool"},
            }
        )
        refusal = ws.receive_json()
        assert refusal["id"] == 2
        assert refusal["error"]["code"] == -32013


def test_unknown_agent_runtime_is_not_found(runtime_auth_client):
    client, _ = runtime_auth_client
    response = client.post(
        "/api/agents/nobody/runtime/auth/ws-ticket", headers=AUTH_HEADER
    )
    assert response.status_code == 404
