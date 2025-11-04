"""
Test to reproduce the CLI mode stdio MCP server AnyIO error.

This test simulates the exact behavior of CLI mode with stdio MCP servers
and should reproduce the "Attempted to exit cancel scope in a different task" error.
"""

import asyncio
from contextlib import AsyncExitStack

import anyio
import pytest


class StdioMCPServerMock:
    """
    Mock stdio MCP server that uses AnyIO task groups like the real implementation.

    This simulates mcp.client.stdio.stdio_client which creates task groups
    that are tied to the asyncio task context.
    """

    def __init__(self, name: str):
        self.name = name
        self.entered = False
        self.exited = False
        self._task_group_manager = None
        self._task_group = None
        self._entry_task_id = None

    async def __aenter__(self):
        """Enter creates an AnyIO task group in the current task."""
        self.entered = True
        self._entry_task_id = id(asyncio.current_task())

        # This is what the real stdio_client does - creates a task group
        self._task_group_manager = anyio.create_task_group()
        self._task_group = await self._task_group_manager.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Exit must be called from the same task that called __aenter__."""
        current_task_id = id(asyncio.current_task())

        # This is where the real error occurs
        if self._task_group_manager:
            # This will raise if we're in a different task
            await self._task_group_manager.__aexit__(exc_type, exc, tb)

        self.exited = True

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Simulate calling an MCP tool."""
        if not self._task_group:
            raise RuntimeError("Server not entered")

        # Use the task group to run something
        result_holder = {}

        async def tool_task():
            await asyncio.sleep(0.01)
            result_holder["result"] = f"Result from {name}"

        self._task_group.start_soon(tool_task)
        await asyncio.sleep(0.02)  # Wait for task
        return result_holder.get("result", "no result")


@pytest.mark.asyncio
async def test_cli_mode_stdio_mcp_reuse_causes_anyio_error():
    """
    REPRODUCTION TEST: This should fail with the exact AnyIO error.

    The real issue: When you enter an AsyncExitStack in one task and try to
    exit it from a different task, the AnyIO task groups inside the servers
    will raise an error.
    """

    server = StdioMCPServerMock("test-server")
    stack = AsyncExitStack()

    # Enter the stack and server in Task A
    async def task_a():
        await stack.__aenter__()
        active_server = await stack.enter_async_context(server)
        assert server.entered is True
        # Use the server
        result = await active_server.call_tool("fetch", {"url": "test"})
        assert "Result" in result
        # Don't exit the stack here - leave it for task B

    # Run task A
    task_a_handle = asyncio.create_task(task_a())
    await task_a_handle

    # Now try to exit the stack from Task B
    # This should trigger the AnyIO error
    async def task_b():
        # Try to exit the stack that was entered in task_a
        await stack.__aexit__(None, None, None)

    # This should raise RuntimeError from AnyIO
    task_b_handle = asyncio.create_task(task_b())

    with pytest.raises(RuntimeError, match="Attempted to exit cancel scope"):
        await task_b_handle


@pytest.mark.asyncio
async def test_cli_mode_stdio_mcp_rebuild_per_message_works():
    """
    SOLUTION TEST: This should pass when we rebuild servers per message.

    Instead of reusing the same server instance, we create a new one
    for each message, ensuring the AnyIO task group is created and
    destroyed in the same asyncio task.
    """

    # Simulate first message
    async with AsyncExitStack() as stack1:
        server1 = StdioMCPServerMock("server-msg1")
        active_server1 = await stack1.enter_async_context(server1)

        async def first_message():
            result = await active_server1.call_tool("fetch", {"url": "test"})
            assert "Result" in result

        await first_message()

    # Server 1 cleanly exited
    assert server1.exited is True

    # Simulate second message with NEW server instance
    async with AsyncExitStack() as stack2:
        server2 = StdioMCPServerMock("server-msg2")
        active_server2 = await stack2.enter_async_context(server2)

        async def second_message():
            result = await active_server2.call_tool("fetch", {"url": "test2"})
            assert "Result" in result

        # Even if this runs in a different task, it's fine because
        # the server was created and will be destroyed in this context
        task = asyncio.create_task(second_message())
        await task

    # Server 2 cleanly exited
    assert server2.exited is True

    # Both servers are different instances
    assert server1 is not server2
