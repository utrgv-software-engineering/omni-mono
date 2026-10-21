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
            assert not result.isError
            assert [block.text for block in result.content] == [f"message-{number}"]

    for number in (1, 2):
        if separate_tasks:
            await asyncio.wait_for(asyncio.create_task(message(number)), timeout=20)
        else:
            await message(number)
    assert instances[0] is not instances[1]
