"""Exercise actual stdio handshakes and cleanup, not an always-skipped stub."""

import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from agents.mcp import MCPServerStdio


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_tasks", [False, True])
async def test_real_mcp_stdio_fresh_instances_across_contexts(separate_tasks):
    """Each CLI message owns its connection and AnyIO cleanup scope."""
    params = {
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("stdio_echo_server.py"))],
    }
    instances = []

    async def message(number):
        async with AsyncExitStack() as stack:
            server = MCPServerStdio(params, name=f"test-server-{number}")
            instances.append(server)
            await stack.enter_async_context(server)
            assert "echo" in {tool.name for tool in await server.list_tools()}
            result = await server.call_tool("echo", {"text": f"message-{number}"})
            assert not result.is_error
            assert [block.text for block in result.content] == [f"message-{number}"]

    for number in (1, 2):
        if separate_tasks:
            await asyncio.wait_for(asyncio.create_task(message(number)), timeout=20)
        else:
            await message(number)
    assert instances[0] is not instances[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
async def test_elicitation_across_protocol_versions(monkeypatch, mode, action):
    from omniagents.core.agents.mcp_elicitation import (
        elicitation_scope,
        make_mcp_elicitation_handler,
        install_client_session_elicitation,
    )
    import agents.mcp.server as sdk

    install_client_session_elicitation()
    factory = sdk.create_v2_client

    def configured(*args, **kwargs):
        client = factory(*args, **kwargs)
        client.mode = mode
        return client

    monkeypatch.setattr(sdk, "create_v2_client", configured)
    requests = []

    async def respond(**request):
        requests.append(request)
        return {"action": action, "value": {"name": "Ada"}}

    fixture = Path(__file__).parents[2] / "fixtures" / "mcp_elicit_server.py"
    callback = make_mcp_elicitation_handler(respond, server_name="fixture")
    with elicitation_scope(callback):
        async with MCPServerStdio(
            params={"command": sys.executable, "args": [str(fixture)]},
            client_session_timeout_seconds=10,
        ) as server:
            await server.list_tools()
            result = await server.call_tool("ask_name", {})
    assert not result.is_error
    expected = "hello Ada" if action == "accept" else f"no answer ({action})"
    assert result.content[0].text == expected
    assert len(requests) == 1
    assert requests[0]["input_schema"]["required"] == ["name"]
    assert requests[0]["persist_response"] is False
