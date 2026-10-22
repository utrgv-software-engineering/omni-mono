import asyncio
import os
import pytest
from unittest.mock import patch, AsyncMock

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc.agents.remote import RemoteAgent
from agents import Agent
from tests.fixtures.servers import start_server


async def dummy_build_agent(settings, mcp, app):
    return Agent(name="a", tools=[], instructions="", model="gpt")


async def fake_stream_agent(
    agent, prompt, sess, channel, max_turns=20, context=None, **kwargs
):
    print(f"DEBUG fake_stream_agent: Started with prompt={prompt}")
    run_id = asyncio.current_task().get_name()
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)

    print(f"DEBUG fake_stream_agent: Sending message_output")
    await channel.async_call(
        "message_output",
        {
            "run_id": run_id,
            "session_id": sess.id,
            "content": "pong",
            "is_final": False,
        },
    )
    assistant_msg = {"role": "assistant", "content": "pong"}
    sess.history.append(assistant_msg)
    append_message(sess.id, assistant_msg)

    approval_id = "appr1"
    print(f"DEBUG fake_stream_agent: Sending client_request for approval")
    await channel.async_call(
        "client_request",
        {
            "request_id": approval_id,
            "function": "ui.request_tool_approval",
            "args": {"tool": "echo", "arguments": "{}"},
            "run_id": run_id,
            "session_id": sess.id,
            "approval_id": approval_id,
        },
    )
    await asyncio.sleep(0.01)

    print(f"DEBUG fake_stream_agent: Sending tool_called")
    call_id = "call-1"
    await channel.async_call(
        "tool_called",
        {
            "run_id": run_id,
            "session_id": sess.id,
            "tool": "echo",
            "input": "{}",
            "call_id": call_id,
        },
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
        {
            "run_id": run_id,
            "session_id": sess.id,
            "tool": "echo",
            "output": "ok",
            "call_id": call_id,
        },
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






@patch("omniagents.core.agents.service.stream_agent", new=fake_stream_agent)
async def _integration_flow():
    spec = AgentSpec(
        name="A",
        welcome_text="hi",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
    )

    app = server_app.build_app(spec=spec, default_host_workspace_root="/tmp")

    server, task, port = await start_server(app)

    try:
        proxy = RemoteAgent(f"ws://127.0.0.1:{port}/ws")
        proxy.DEBUG_CLIENT = True  # Enable debug output
        events = {"msg": [], "tool_call": [], "tool_out": []}
        run = proxy.run_streamed([{"role": "user", "content": "ping"}])
        event_count = 0
        async for event in run.stream_events():
            event_count += 1
            print(f"DEBUG: Got event {event_count}: type={event.type}")
            if event.type == "run_item_stream_event":
                item = event.item
                item_type = getattr(item, "type", "")
                if item_type == "message_output_item":
                    events["msg"].append(item.to_input_item()["content"])
                elif item_type == "tool_call_item":
                    events["tool_call"].append((item.name, item.arguments))
                elif item_type == "tool_call_output_item":
                    events["tool_out"].append(item.output)
            elif event.type == "client_request":
                payload = event.item if isinstance(event.item, dict) else {}
                if payload.get("function") == "ui.request_tool_approval":
                    await proxy._client.other.client_response(
                        request_id=payload.get("request_id"),
                        ok=True,
                        result={
                            "approved": True,
                            "always_approve": False,
                            "error_message": None,
                        },
                        error=None,
                    )
        print(f"DEBUG: Total events received: {event_count}")

        assert events["msg"] == ["pong"]
        assert events["tool_call"] == [("echo", "{}")]
        assert events["tool_out"] == ["ok"]

        sess_id = proxy._session_id
        hist_resp = await proxy._client.other.get_session_history(session_id=sess_id)
        history = (
            hist_resp
            if isinstance(hist_resp, list)
            else getattr(hist_resp, "result", hist_resp)
        )
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


@patch("omniagents.core.agents.service.stream_agent", new=fake_stream_agent)
async def _proxy_flow():
    remote_token_env = "OMNIAGENTS_TEST_REMOTE_TOKEN"
    remote_token_value = "remote-secret"
    os.environ[remote_token_env] = remote_token_value

    remote_spec = AgentSpec(
        name="A",
        welcome_text="hi",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
    )

    remote_app = server_app.build_app(
        spec=remote_spec,
        auth_token=remote_token_value,
        default_host_workspace_root="/tmp",
    )
    remote_server, remote_task, remote_port = await start_server(remote_app)

    proxy = None
    gateway_port = None
    gateway_server = None
    gateway_task = None

    try:
        gateway_spec = AgentSpec(
            name="Gateway",
            welcome_text="hi",
            get_agent_instructions=lambda: "",
            build_agent=dummy_build_agent,
            remote_ws_url=f"ws://127.0.0.1:{remote_port}/ws",
            remote_auth_token_key=remote_token_env,
        )

        gateway_app = server_app.build_app(
            spec=gateway_spec, default_host_workspace_root="/tmp"
        )
        gateway_server, gateway_task, gateway_port = await start_server(gateway_app)

        proxy = RemoteAgent(f"ws://127.0.0.1:{gateway_port}/ws")
        events = {"msg": [], "tool_call": [], "tool_out": []}
        run = proxy.run_streamed([{"role": "user", "content": "ping"}])
        async for event in run.stream_events():
            if event.type == "run_item_stream_event":
                item = event.item
                item_type = getattr(item, "type", "")
                if item_type == "message_output_item":
                    events["msg"].append(item.to_input_item()["content"])
                elif item_type == "tool_call_item":
                    events["tool_call"].append((item.name, item.arguments))
                elif item_type == "tool_call_output_item":
                    events["tool_out"].append(item.output)
            elif event.type == "client_request":
                payload = event.item if isinstance(event.item, dict) else {}
                if payload.get("function") == "ui.request_tool_approval":
                    await proxy._client.other.client_response(
                        request_id=payload.get("request_id"),
                        ok=True,
                        result={
                            "approved": True,
                            "always_approve": False,
                            "error_message": None,
                        },
                        error=None,
                    )

        assert events["msg"] == ["pong"]
        assert events["tool_call"] == [("echo", "{}")]
        assert events["tool_out"] == ["ok"]
    finally:
        if gateway_server is not None:
            gateway_server.should_exit = True
        if gateway_task is not None:
            await gateway_task
        remote_server.should_exit = True
        await remote_task
        if proxy and proxy._client:
            await proxy._client.__aexit__(None, None, None)
        os.environ.pop(remote_token_env, None)


def test_server_proxy_gateway():
    asyncio.run(_proxy_flow())
