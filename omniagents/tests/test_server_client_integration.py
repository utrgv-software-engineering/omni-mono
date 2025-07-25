import asyncio
import socket
import pytest
from unittest.mock import patch, AsyncMock

import uvicorn
from omniagents.server import app as server_app
from omniagents.core.specs import AgentSpec
from omniagents.rpc.agents.remote import RemoteAgent
from agents import Agent


async def dummy_build_agent(settings, mcp, app):
    return Agent(name="a", tools=[], instructions="", model="gpt")


async def fake_stream_agent(agent, prompt, sess, channel, max_turns=20):
    print(f"DEBUG fake_stream_agent: Started with prompt={prompt}")
    run_id = asyncio.current_task().get_name()
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)
    
    print(f"DEBUG fake_stream_agent: Sending message_output")
    await channel.async_call(
        "message_output",
        {"run_id": run_id, "content": "pong", "is_final": False},
    )
    assistant_msg = {"role": "assistant", "content": "pong"}
    sess.history.append(assistant_msg)
    append_message(sess.id, assistant_msg)

    approval_id = "appr1"
    print(f"DEBUG fake_stream_agent: Sending tool_approval_request")
    await channel.async_call(
        "tool_approval_request",
        {"approval_id": approval_id, "tool": "echo", "arguments": "{}"},
    )
    await asyncio.sleep(0.01)

    print(f"DEBUG fake_stream_agent: Sending tool_called")
    await channel.async_call(
        "tool_called",
        {"run_id": run_id, "tool": "echo", "input": "{}"},
    )
    call_item = {
        "role": "assistant",
        "type": "tool_call",
        "name": "echo",
        "arguments": "{}",
    }
    sess.history.append(call_item)
    append_message(sess.id, call_item)

    print(f"DEBUG fake_stream_agent: Sending tool_result")
    await channel.async_call(
        "tool_result",
        {"run_id": run_id, "tool": "echo", "output": "ok"},
    )
    
    print(f"DEBUG fake_stream_agent: Finished")
    out_item = {
        "role": "assistant",
        "type": "tool_call_output",
        "name": "echo",
        "output": "ok",
    }
    sess.history.append(out_item)
    append_message(sess.id, out_item)
    
    # Don't send run_end here - it's sent by AgentService._worker()


def _get_free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _run_server(app, port):
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task


@patch('omniagents.core.agent_service.stream_agent', new=fake_stream_agent)
async def _integration_flow():
    spec = AgentSpec(
        name="A",
        header_title="A",
        page_title="A",
        welcome_text="hi",
        client_storage_prefix="a.",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
    )

    app = server_app.build_app(spec=spec)

    port = _get_free_port()
    server, task = await _run_server(app, port)

    try:
        proxy = RemoteAgent(f"ws://127.0.0.1:{port}/ws")
        proxy.DEBUG_CLIENT = True  # Enable debug output
        events = {"msg": [], "tool_call": [], "tool_out": []}
        run = proxy.run_streamed([{"role": "user", "content": "ping"}])
        event_count = 0
        async for event in run.stream_events():
            event_count += 1
            print(f"DEBUG: Got event {event_count}: type={event.type}")
            if event.type == "message_output_created":
                events["msg"].append(event.item.raw_item.content[0].text)
            elif event.type == "tool_approval_request":
                await proxy._client.other.tool_approval_response(
                    approval_id=event.item["approval_id"], approved=True
                )
            elif event.type == "tool_called":
                events["tool_call"].append((event.item.name, event.item.arguments))
            elif event.type == "tool_output":
                events["tool_out"].append(event.item.output)
        print(f"DEBUG: Total events received: {event_count}")

        assert events["msg"] == ["pong"]
        assert events["tool_call"] == [("echo", "{}")]
        assert events["tool_out"] == ["ok"]

        sess_id = proxy._session_id
        hist_resp = await proxy._client.other.get_session_history(session_id=sess_id)
        history = hist_resp if isinstance(hist_resp, list) else getattr(hist_resp, "result", hist_resp)
        assert history[0]["content"] == "ping"
        assert history[1]["content"] == "pong"
        assert history[2]["type"] == "tool_call"
        assert history[2]["name"] == "echo"
        assert history[3]["type"] == "tool_call_output"
        assert history[3]["output"] == "ok"

        sessions = await proxy.list_sessions()
        assert sessions[0]["id"] == sess_id
        assert sessions[0]["archived"] is False

        res = await proxy.archive_session(sess_id)
        assert res is True

        sessions2 = await proxy.list_sessions()
        assert sessions2[0]["archived"] is True
    finally:
        server.should_exit = True
        await task
        if proxy._client:
            await proxy._client.__aexit__(None, None, None)


def test_server_client_integration():
    asyncio.run(_integration_flow())
