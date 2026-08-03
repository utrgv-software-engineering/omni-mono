"""Connection-authentication tests for every WebSocket surface.

The unified model (see ``omniagents/rpc/auth.py``):

* ``Authorization: Bearer`` headers and one-time ``/auth/ws-ticket``
  connect tickets are the supported credential transports.
* The legacy ``?token=`` query parameter is rejected unless
  ``OMNIAGENTS_LEGACY_QUERY_AUTH=1`` (documented migration path).
* ``/ws``, ``/ws/realtime`` and ``/ws/terminal`` all resolve the same
  principal through the same authenticator.
* Auth failures close with 4401/4403 — terminal codes, no retry.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from types import SimpleNamespace

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime import bridge as runner_bridge
from agents import Agent


async def mock_build_agent(settings, mcp, app):
    return Agent(
        name="auth_test_agent",
        tools=[],
        instructions="Auth Test",
        model="gpt-3.5-turbo",
    )


async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20):
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)


class StubTerminal:
    def __init__(self):
        self.exit_code = 0


class StubTerminalManager:
    def __init__(self):
        self.authorized = []
        self.principals = []
        self.read_calls = 0
        self.input_payloads = []
        self.resize_calls = []
        self.closed = []

    async def authorize(
        self, session_id, terminal_id, terminal_token, *, principal=None
    ):
        self.authorized.append((session_id, terminal_id, terminal_token))
        self.principals.append(principal)
        return StubTerminal()

    async def read_output(self, terminal):
        self.read_calls += 1
        if self.read_calls == 1:
            return b"hello"
        return None

    def encode_output(self, chunk):
        if isinstance(chunk, bytes):
            return chunk.decode()
        return str(chunk)

    def decode_input(self, data):
        return data.encode()

    async def write_input(self, terminal, payload):
        self.input_payloads.append(payload)

    async def resize(self, terminal, cols, rows):
        self.resize_calls.append((cols, rows))

    async def close_terminal(self, session_id, terminal_id):
        self.closed.append((session_id, terminal_id))


def _build_auth_app(monkeypatch, *, legacy: bool = False) -> SimpleNamespace:
    manager = StubTerminalManager()

    class StubAgentService(server_app.AgentService):
        def __init__(self, spec, *args, **kwargs):
            super().__init__(spec, *args, **kwargs)
            self.terminal_manager = manager

    monkeypatch.setattr(server_app, "AgentService", StubAgentService)
    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    if legacy:
        monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "1")
    else:
        monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)

    spec = AgentSpec(
        name="AuthAgent",
        welcome_text="Auth",
        get_agent_instructions=lambda: "Auth",
        build_agent=mock_build_agent,
    )

    app = server_app.build_app(
        spec=spec, auth_token="secret", default_host_workspace_root="/tmp"
    )
    client = TestClient(app)
    return SimpleNamespace(client=client, terminal_manager=manager)


@pytest.fixture
def auth_context(monkeypatch):
    return _build_auth_app(monkeypatch)


@pytest.fixture
def legacy_auth_context(monkeypatch):
    return _build_auth_app(monkeypatch, legacy=True)


AUTH_HEADER = {"Authorization": "Bearer secret"}


# ---------------------------------------------------------------------------
# /ws — main surface
# ---------------------------------------------------------------------------


def test_ws_rejects_missing_token(auth_context):
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect("/ws"):
            pass


def test_ws_rejects_wrong_token(auth_context):
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect(
            "/ws", headers={"Authorization": "Bearer wrong"}
        ):
            pass


def test_ws_accepts_authorization_header(auth_context):
    with auth_context.client.websocket_connect("/ws", headers=AUTH_HEADER):
        pass


def test_ws_rejects_query_token_by_default(auth_context):
    """The legacy ?token= transport is off unless explicitly re-enabled."""
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect("/ws?token=secret"):
            pass


def test_ws_accepts_query_token_in_legacy_mode(legacy_auth_context):
    with legacy_auth_context.client.websocket_connect("/ws?token=secret"):
        pass


# ---------------------------------------------------------------------------
# /auth/ws-ticket — browser connect tickets
# ---------------------------------------------------------------------------


def test_ticket_endpoint_requires_authentication(auth_context):
    assert auth_context.client.post("/auth/ws-ticket").status_code == 401
    assert (
        auth_context.client.post(
            "/auth/ws-ticket", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )


def test_ticket_flow_dials_all_surfaces(auth_context):
    client = auth_context.client

    def mint() -> str:
        response = client.post("/auth/ws-ticket", headers=AUTH_HEADER)
        assert response.status_code == 200
        body = response.json()
        assert body["expires_in"] > 0
        # The response never echoes the caller's access token.
        assert "secret" not in response.text
        return body["ticket"]

    with client.websocket_connect(f"/ws?ticket={mint()}"):
        pass
    # /ws/terminal authenticates with the same ticket mechanism (the
    # attach handshake is exercised in test_server_terminal.py).
    with client.websocket_connect(f"/ws/terminal?ticket={mint()}"):
        pass


def test_ticket_is_single_use(auth_context):
    client = auth_context.client
    ticket = client.post("/auth/ws-ticket", headers=AUTH_HEADER).json()["ticket"]
    with client.websocket_connect(f"/ws?ticket={ticket}"):
        pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?ticket={ticket}"):
            pass


def test_invalid_ticket_rejected(auth_context):
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect("/ws?ticket=forged"):
            pass


# ---------------------------------------------------------------------------
# /ws/realtime + /ws/terminal — same authenticator
# ---------------------------------------------------------------------------


def test_realtime_route_requires_authentication(auth_context):
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect("/ws/realtime"):
            pass


def test_terminal_rejects_missing_token(auth_context):
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect("/ws/terminal"):
            pass


def test_terminal_accepts_header_and_first_message_attach(auth_context):
    messages = []
    with auth_context.client.websocket_connect(
        "/ws/terminal", headers=AUTH_HEADER
    ) as ws:
        ws.send_json(
            {
                "type": "attach",
                "session_id": "s1",
                "terminal_id": "t1",
                "terminal_token": "tt",
            }
        )
        try:
            first = ws.receive_json()
        except WebSocketDisconnect:
            pytest.fail("connection closed before message")
        messages.append(first)
        ws.send_json({"type": "input", "data": "abc"})
        ws.send_json({"type": "resize", "cols": 80, "rows": 24})
        ws.send_json({"type": "close"})
        for _ in range(5):
            try:
                messages.append(ws.receive_json())
            except WebSocketDisconnect:
                break

    types = {msg.get("type") for msg in messages}
    assert "exit" in types
    assert auth_context.terminal_manager.authorized == [("s1", "t1", "tt")]
    # The resolved principal reached the terminal authorization layer.
    principal = auth_context.terminal_manager.principals[0]
    assert principal is not None and principal.user_id == "token_user"
    assert auth_context.terminal_manager.input_payloads == [b"abc"]
    assert auth_context.terminal_manager.resize_calls[-1] == (80, 24)
    assert auth_context.terminal_manager.closed == [("s1", "t1")]


def test_terminal_legacy_query_attach_still_works_in_legacy_mode(
    legacy_auth_context,
):
    url = "/ws/terminal?token=secret&session_id=s1&terminal_id=t1&terminal_token=tt"
    with legacy_auth_context.client.websocket_connect(url) as ws:
        first = ws.receive_json()
        assert first["type"] == "output"
        ws.send_json({"type": "close"})
        for _ in range(5):
            try:
                ws.receive_json()
            except WebSocketDisconnect:
                break
    assert legacy_auth_context.terminal_manager.authorized == [("s1", "t1", "tt")]


# ---------------------------------------------------------------------------
# Token leakage
# ---------------------------------------------------------------------------


def test_token_absent_from_logs_and_urls(auth_context, caplog, capsys):
    """A normal authenticated exchange never materializes the token in logs."""
    import logging

    with caplog.at_level(logging.DEBUG):
        response = auth_context.client.post("/auth/ws-ticket", headers=AUTH_HEADER)
        ticket = response.json()["ticket"]
        with auth_context.client.websocket_connect(f"/ws?ticket={ticket}"):
            pass
    assert "secret" not in caplog.text
    captured = capsys.readouterr()
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_legacy_mode_warns_without_leaking_token(legacy_auth_context, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        with legacy_auth_context.client.websocket_connect("/ws?token=secret"):
            pass
    assert "Deprecated query-string token" in caplog.text
    assert "secret" not in caplog.text


def test_readiness_payload_omits_query_token_by_default(monkeypatch):
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    from omniagents.product_serve import build_readiness_payload

    payload = build_readiness_payload(
        host="127.0.0.1",
        port=8123,
        auth_token="secret",
        session=None,
        workspace="/tmp",
        profile=SimpleNamespace(raw={}, sources=[]),
        service_urls={},
        resume_tier=None,
        agent_host_id="agenthost-1",
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    assert "secret" not in payload["ws_url"]
    assert payload["auth_token"] == "secret"

    monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "1")
    legacy_payload = build_readiness_payload(
        host="127.0.0.1",
        port=8123,
        auth_token="secret",
        session=None,
        workspace="/tmp",
        profile=SimpleNamespace(raw={}, sources=[]),
        service_urls={},
        resume_tier=None,
        agent_host_id="agenthost-1",
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    assert legacy_payload["ws_url"].endswith("?token=secret")


def test_remote_agent_url_never_contains_token():
    from omniagents.rpc.agents.remote import RemoteAgent

    agent = RemoteAgent(url="ws://example.test/ws", token="secret")
    assert "secret" not in (agent._url or "")
