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
* The playground dispatch runs every request through
  ``AgentService.authorize_rpc`` (method classification + ownership).
"""

import shutil
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.rpc.auth import WS_CLOSE_UNAUTHENTICATED
from omniagents_studio.api import _guardrail_keys, create_api_app

AUTH_HEADER = {"Authorization": "Bearer studio-secret"}

JOB_WS_ROUTES = [
    "/api/evaluation/suite/run/{job_id}/ws",
    "/api/evaluation/scenarios/run/{job_id}/ws",
    "/api/evaluation/scenarios/generate/{job_id}/ws",
    "/api/evaluation/judges/optimize/{job_id}/ws",
    "/api/evaluation/agents/optimize/{job_id}/ws",
]


def _build_client(monkeypatch, *, legacy: bool = False, **kwargs):
    if legacy:
        monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "1")
    else:
        monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    app, _ = create_api_app(auth_token="studio-secret", **kwargs)
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
# /api/ws/traces — representative always-available Studio surface
# ---------------------------------------------------------------------------


def test_traces_ws_rejects_missing_credentials(auth_client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect("/api/ws/traces"):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_traces_ws_rejects_wrong_header(auth_client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect(
            "/api/ws/traces", headers={"Authorization": "Bearer wrong"}
        ):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_traces_ws_accepts_authorization_header(auth_client):
    with auth_client.websocket_connect("/api/ws/traces", headers=AUTH_HEADER):
        pass


def test_traces_ws_rejects_query_token_by_default(auth_client):
    """The legacy ?token= transport is off unless explicitly re-enabled."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect("/api/ws/traces?token=studio-secret"):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_traces_ws_accepts_query_token_in_legacy_mode(legacy_auth_client):
    with legacy_auth_client.websocket_connect("/api/ws/traces?token=studio-secret"):
        pass


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


def test_ticket_flow_dials_traces_ws(auth_client):
    with auth_client.websocket_connect(f"/api/ws/traces?ticket={_mint(auth_client)}"):
        pass


def test_ticket_is_single_use(auth_client):
    ticket = _mint(auth_client)
    with auth_client.websocket_connect(f"/api/ws/traces?ticket={ticket}"):
        pass
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect(f"/api/ws/traces?ticket={ticket}"):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_expired_ticket_rejected(auth_app_and_client):
    app, client = auth_app_and_client
    ticket = _mint(client)
    broker = app.state.connection_authenticator.ticket_broker
    broker._pending[ticket].expires_at = time.monotonic() - 1
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/api/ws/traces?ticket={ticket}"):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_invalid_ticket_rejected(auth_client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect("/api/ws/traces?ticket=forged"):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


# ---------------------------------------------------------------------------
# Evaluation job-stream routes — same authenticator on every surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", JOB_WS_ROUTES)
def test_job_ws_requires_authentication(auth_client, route):
    url = route.format(job_id="no-such-job")
    # Unauthenticated: refused before the job lookup (4401).
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect(url):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED
    # Authenticated: passes auth and reaches the job lookup (4404).
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect(url, headers=AUTH_HEADER):
            pass
    assert exc.value.code == 4404
    # A correct query token is no longer a credential.
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect(f"{url}?token=studio-secret"):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


# ---------------------------------------------------------------------------
# Token leakage
# ---------------------------------------------------------------------------


def test_token_absent_from_logs(auth_client, caplog, capsys):
    """A normal authenticated exchange never materializes the token in logs."""
    import logging

    with caplog.at_level(logging.DEBUG):
        ticket = _mint(auth_client)
        with auth_client.websocket_connect(f"/api/ws/traces?ticket={ticket}"):
            pass
    assert "studio-secret" not in caplog.text
    captured = capsys.readouterr()
    assert "studio-secret" not in captured.out
    assert "studio-secret" not in captured.err


# ---------------------------------------------------------------------------
# Playground: authentication + authorize_rpc at the dispatch site
# ---------------------------------------------------------------------------


@pytest.fixture
def playground_resources(tmp_path):
    fixture_root = (
        Path(__file__).resolve().parent.parent / "fixtures" / "studio" / "basic_project"
    )
    destination = tmp_path / "project"
    shutil.copytree(fixture_root, destination)
    manifest_path = destination / "project.yml"
    context = load_project(str(manifest_path))
    agent_specs = {
        key: build_spec_from_project(context, key) for key in context.agents_map.keys()
    }
    base_spec = agent_specs[context.entrypoint]
    return {
        "project_name": context.product.slug,
        "tool_registry": context.tool_registry,
        "guardrails_registry": context.guardrails_registry,
        "agent_specs": agent_specs,
        "entry_agent_key": context.entrypoint,
        "manifest_path": str(manifest_path),
        "base_spec": base_spec,
        "default_model_settings": base_spec.model_settings or {},
        "default_safe_agent": bool(getattr(base_spec, "use_safe_agent", False)),
        "default_safe_agent_options": (
            asdict(base_spec.safe_agent_options) if base_spec.safe_agent_options else {}
        ),
        "default_guardrails": {
            "input": _guardrail_keys(
                getattr(base_spec, "input_guardrails", None),
                context.guardrails_registry.get("input", {}),
            ),
            "output": _guardrail_keys(
                getattr(base_spec, "output_guardrails", None),
                context.guardrails_registry.get("output", {}),
            ),
        },
    }


@pytest.fixture
def playground_auth_client(monkeypatch, playground_resources):
    app, client = _build_client(
        monkeypatch,
        default_project="auth-playground",
        playground_resources=playground_resources,
    )
    try:
        yield client
    finally:
        client.close()


def _create_playground_session(client) -> str:
    response = client.post(
        "/api/playground/session",
        json={"model": "gpt-4o", "instructions": "auth test", "tools": []},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 200
    return response.json()["session_id"]


def test_playground_ws_rejects_missing_credentials(playground_auth_client):
    session_id = _create_playground_session(playground_auth_client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with playground_auth_client.websocket_connect(
            f"/api/playground/ws?session_id={session_id}"
        ):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_playground_ws_rejects_query_token_by_default(playground_auth_client):
    session_id = _create_playground_session(playground_auth_client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with playground_auth_client.websocket_connect(
            f"/api/playground/ws?session_id={session_id}&token=studio-secret"
        ):
            pass
    assert exc.value.code == WS_CLOSE_UNAUTHENTICATED


def test_playground_ws_ticket_flow_and_rpc_authorization(playground_auth_client):
    """Ticket-authenticated playground connections are authorized per-RPC.

    ``get_agent_info`` (classified globally safe) succeeds, while
    ``disable_tool`` (global-mutating: affects every principal) is denied
    with ``-32014`` in enforced mode — proof that
    ``AgentService.authorize_rpc`` and the method classification run at
    the playground dispatch site.
    """
    client = playground_auth_client
    session_id = _create_playground_session(client)
    ticket = _mint(client)

    with client.websocket_connect(
        f"/api/playground/ws?session_id={session_id}&ticket={ticket}"
    ) as ws:
        ws.send_json(
            {"jsonrpc": "2.0", "id": 1, "method": "get_agent_info", "params": {}}
        )
        reply = ws.receive_json()
        assert reply["id"] == 1
        assert "result" in reply

        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "disable_tool",
                "params": {"tool_name": "greet_tool"},
            }
        )
        denial = ws.receive_json()
        assert denial["id"] == 2
        assert denial["error"]["code"] == -32014
        assert denial["error"]["data"]["kind"] == "authorization_denied"
