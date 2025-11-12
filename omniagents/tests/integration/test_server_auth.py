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
        self.read_calls = 0
        self.input_payloads = []
        self.resize_calls = []
        self.closed = []

    async def authorize(self, session_id, terminal_id, terminal_token):
        self.authorized.append((session_id, terminal_id, terminal_token))
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


@pytest.fixture
def auth_context(monkeypatch):
    manager = StubTerminalManager()

    class StubAgentService(server_app.AgentService):
        def __init__(self, spec):
            super().__init__(spec)
            self.terminal_manager = manager

    monkeypatch.setattr(server_app, "AgentService", StubAgentService)
    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)

    spec = AgentSpec(
        name="AuthAgent",
        welcome_text="Auth",
        get_agent_instructions=lambda: "Auth",
        build_agent=mock_build_agent,
    )

    app = server_app.build_app(spec=spec, auth_token="secret")
    client = TestClient(app)
    return SimpleNamespace(client=client, terminal_manager=manager)


def test_ws_rejects_missing_token(auth_context):
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect("/ws"):
            pass


def test_ws_accepts_valid_token(auth_context):
    with auth_context.client.websocket_connect("/ws?token=secret"):
        pass


def test_terminal_rejects_missing_token(auth_context):
    url = "/ws/terminal?session_id=s1&terminal_id=t1&terminal_token=tt"
    with pytest.raises(WebSocketDisconnect):
        with auth_context.client.websocket_connect(url):
            pass


def test_terminal_accepts_valid_token(auth_context):
    url = "/ws/terminal?token=secret&session_id=s1&terminal_id=t1" "&terminal_token=tt"
    messages = []
    with auth_context.client.websocket_connect(url) as ws:
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
    assert auth_context.terminal_manager.input_payloads == [b"abc"]
    assert auth_context.terminal_manager.resize_calls[-1] == (80, 24)
    assert auth_context.terminal_manager.closed == [("s1", "t1")]
