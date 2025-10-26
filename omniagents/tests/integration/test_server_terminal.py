import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime import bridge as runner_bridge
from agents import Agent


async def mock_build_agent(settings, mcp, app):
    return Agent(
        name="terminal_agent",
        tools=[],
        instructions="Terminal",
        model="gpt-3.5-turbo",
    )


async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20):
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)


class StubTerminal:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code


class StubTerminalManager:
    def __init__(self):
        self.valid_tokens = {"good-token"}
        self.authorize_calls = []
        self.outputs = [b"ready", None]
        self.write_payloads = []
        self.resize_calls = []
        self.close_calls = []
        self.cleanup_sessions = []

    async def authorize(self, session_id, terminal_id, terminal_token):
        self.authorize_calls.append((session_id, terminal_id, terminal_token))
        if terminal_token not in self.valid_tokens:
            raise ValueError("invalid token")
        return StubTerminal()

    async def read_output(self, terminal):
        if self.outputs:
            return self.outputs.pop(0)
        return None

    def encode_output(self, chunk):
        return base64.b64encode(chunk).decode()

    def decode_input(self, data):
        return base64.b64decode(data)

    async def write_input(self, terminal, payload):
        self.write_payloads.append(payload)

    async def resize(self, terminal, cols, rows):
        self.resize_calls.append((cols, rows))

    async def close_terminal(self, session_id, terminal_id):
        self.close_calls.append((session_id, terminal_id))

    async def cleanup_session(self, session_id):
        self.cleanup_sessions.append(session_id)


@pytest.fixture
def terminal_context(monkeypatch):
    manager = StubTerminalManager()

    class StubAgentService(server_app.AgentService):
        def __init__(self, spec):
            super().__init__(spec)
            self.terminal_manager = manager

    monkeypatch.setattr(server_app, "AgentService", StubAgentService)
    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)

    spec = AgentSpec(
        name="TerminalAgent",
        header_title="TerminalAgent",
        page_title="TerminalAgent",
        welcome_text="Terminal",
        client_storage_prefix="terminal.",
        get_agent_instructions=lambda: "Terminal",
        build_agent=mock_build_agent,
    )

    app = server_app.build_app(spec=spec)
    client = TestClient(app)
    return SimpleNamespace(client=client, manager=manager)


def test_terminal_websocket_success_flow(terminal_context):
    context = terminal_context
    url = "/ws/terminal?session_id=s1&terminal_id=t1&terminal_token=good-token"
    payload = base64.b64encode(b"ls\n").decode()
    with context.client.websocket_connect(url) as ws:
        first = ws.receive_json()
        assert first["type"] == "output"
        assert first["terminal_id"] == "t1"
        assert base64.b64decode(first["data"]) == b"ready"
        ws.send_json({"type": "input", "data": payload})
        ws.send_json({"type": "resize", "cols": 120, "rows": 40})
        ws.send_json({"type": "close"})
        exit_msg = ws.receive_json()
        assert exit_msg["type"] == "exit"
        assert exit_msg["terminal_id"] == "t1"
    assert context.manager.authorize_calls == [("s1", "t1", "good-token")]
    assert context.manager.write_payloads == [b"ls\n"]
    assert context.manager.resize_calls[-1] == (120, 40)
    assert context.manager.close_calls == [("s1", "t1")]


@pytest.mark.parametrize(
    "url",
    [
        "/ws/terminal?terminal_id=t1&terminal_token=good-token",
        "/ws/terminal?session_id=s1&terminal_token=good-token",
        "/ws/terminal?session_id=s1&terminal_id=t1",
    ],
)
def test_terminal_websocket_missing_params(url, terminal_context):
    with pytest.raises(WebSocketDisconnect):
        with terminal_context.client.websocket_connect(url):
            pass
    assert terminal_context.manager.authorize_calls == []
    assert terminal_context.manager.close_calls == []


def test_terminal_websocket_authorize_failure(terminal_context):
    url = "/ws/terminal?session_id=s1&terminal_id=t1&terminal_token=bad"
    with pytest.raises(WebSocketDisconnect):
        with terminal_context.client.websocket_connect(url):
            pass
    assert terminal_context.manager.authorize_calls == [("s1", "t1", "bad")]
    assert terminal_context.manager.close_calls == []


def test_terminal_websocket_disconnect_triggers_cleanup(terminal_context):
    context = terminal_context
    url = "/ws/terminal?session_id=s2&terminal_id=t7&terminal_token=good-token"
    with context.client.websocket_connect(url) as ws:
        ws.receive_json()
    assert context.manager.authorize_calls[0] == ("s2", "t7", "good-token")
    assert context.manager.close_calls == [("s2", "t7")]
