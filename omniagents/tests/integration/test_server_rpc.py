import asyncio
import importlib
from contextlib import ExitStack
from fastapi.testclient import TestClient
from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from agents import Agent
from omniagents.core.runtime import bridge as runner_bridge


async def dummy_build_agent(settings, mcp, app):
    return Agent(name="a", tools=[], instructions="", model="gpt")


async def fake_stream_agent(agent, prompt, sess, channel, max_turns=20):
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)
    await channel.async_call("run_end", {"run_id": asyncio.current_task().get_name()})


def test_rpc_methods(monkeypatch):
    spec = AgentSpec(
        name="A",
        header_title="A",
        page_title="A",
        welcome_text="hi",
        client_storage_prefix="a.",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
    )

    monkeypatch.setattr(runner_bridge, "stream_agent", fake_stream_agent)
    app = server_app.build_app(spec=spec)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "hello"},
            }
        )
        while True:
            resp = ws.receive_json()
            # Skip notifications (no id) such as run_started
            if "id" not in resp:
                continue
            if resp.get("id") == 1 and "result" in resp:
                break
        session_id = resp["result"]["session_id"]
        ws.receive_json()  # run_end

        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "get_session_history",
                "params": {"session_id": session_id},
            }
        )
        hist_resp = ws.receive_json()
        hist = hist_resp["result"]
        assert hist[0]["content"] == "hello"

        ws.send_json(
            {"jsonrpc": "2.0", "id": 3, "method": "list_sessions", "params": {}}
        )
        list_resp = ws.receive_json()
        sess_meta = list_resp["result"][0]
        assert sess_meta["id"] == session_id
        assert sess_meta["archived"] is False

        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "archive_session",
                "params": {"session_id": session_id},
            }
        )
        arch_resp = ws.receive_json()
        assert arch_resp["result"] is True

        ws.send_json(
            {"jsonrpc": "2.0", "id": 5, "method": "list_sessions", "params": {}}
        )
        list_resp2 = ws.receive_json()
        sess_meta2 = list_resp2["result"][0]
        assert sess_meta2["archived"] is True

    # Reload modules to ensure archive state persists
    import omniagents.core.session.manager as sessions_mod

    importlib.reload(sessions_mod)
    importlib.reload(server_app)
    app2 = server_app.build_app(spec=spec)
    client2 = TestClient(app2)
    with client2.websocket_connect("/ws") as ws2:
        ws2.send_json(
            {"jsonrpc": "2.0", "id": 6, "method": "list_sessions", "params": {}}
        )
        list_resp3 = ws2.receive_json()
        sess_meta3 = list_resp3["result"][0]
        assert sess_meta3["archived"] is True


def _wait_for_result(ws, target_id):
    while True:
        resp = ws.receive_json()
        if resp.get("id") == target_id and ("result" in resp or "error" in resp):
            return resp


def test_concurrent_websocket_connections(monkeypatch):
    spec = AgentSpec(
        name="B",
        header_title="B",
        page_title="B",
        welcome_text="hi",
        client_storage_prefix="b.",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
    )

    monkeypatch.setattr(runner_bridge, "stream_agent", fake_stream_agent)
    app = server_app.build_app(spec=spec)
    client = TestClient(app)

    with ExitStack() as stack:
        ws1 = stack.enter_context(client.websocket_connect("/ws"))
        ws2 = stack.enter_context(client.websocket_connect("/ws"))

        ws1.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "hello one"},
            }
        )
        ws2.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "hello two"},
            }
        )

        result1 = _wait_for_result(ws1, 1)
        result2 = _wait_for_result(ws2, 1)
        sess1 = result1["result"]["session_id"]
        sess2 = result2["result"]["session_id"]
        assert sess1 != sess2

        ws1.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "get_session_history",
                "params": {"session_id": sess1},
            }
        )
        ws2.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "get_session_history",
                "params": {"session_id": sess2},
            }
        )

        hist1 = _wait_for_result(ws1, 2)["result"]
        hist2 = _wait_for_result(ws2, 2)["result"]
        assert hist1[0]["content"] == "hello one"
        assert hist2[0]["content"] == "hello two"


def test_connection_cleanup_resets_channel(monkeypatch):
    spec = AgentSpec(
        name="C",
        header_title="C",
        page_title="C",
        welcome_text="hi",
        client_storage_prefix="c.",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
    )

    monkeypatch.setattr(runner_bridge, "stream_agent", fake_stream_agent)
    app = server_app.build_app(spec=spec)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "cleanup"},
            }
        )
        _wait_for_result(ws, 1)

    service = app.state.agent_service
    assert service.channel is None
