"""An absent feature's socket must close with a code a browser can see.

Closing a WebSocket before accepting it reaches a real client as an HTTP
403 handshake failure (close code 1006), which clients treat as transient
and retry for minutes; the voice probe did exactly that and held up a chat
column's boot. Only a real server and client show the difference: the test
client reports the intended code either way.
"""

import pytest
import uvicorn
from agents import Agent
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from omniagents.backends.server.app import build_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.utils.serving import BackgroundServer, bind_socket


async def _build_agent(settings, mcp, app):
    return Agent(name="unit", instructions="", tools=[], model="gpt")


@pytest.fixture
def server():
    spec = AgentSpec(
        name="unit",
        welcome_text="",
        get_agent_instructions=lambda: "",
        build_agent=_build_agent,
    )
    running = BackgroundServer(
        uvicorn.Config(build_app(spec=spec), log_level="error"),
        bind_socket("127.0.0.1", 0),
    ).start()
    try:
        yield running
    finally:
        running.stop()


@pytest.mark.parametrize("path", ["/ws/realtime", "/ws/terminal"])
def test_an_absent_feature_closes_its_socket_with_a_permanent_code(server, path):
    with connect(f"ws://127.0.0.1:{server.port}{path}") as ws:
        with pytest.raises(ConnectionClosed) as closed:
            ws.recv(timeout=10)
    assert closed.value.rcvd.code == 4404
