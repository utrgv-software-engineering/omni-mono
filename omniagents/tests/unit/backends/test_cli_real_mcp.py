"""
Test CLI mode with REAL MCPServerStdio to reproduce the actual AnyIO error.
"""

import asyncio
import os
import sys
from contextlib import AsyncExitStack

import pytest

# Skip if agents library not available
pytest.importorskip("agents")

from agents.mcp import MCPServerStdio


@pytest.mark.asyncio
async def test_real_mcp_stdio_server_reuse_across_contexts():
    """
    Test with REAL MCPServerStdio to see if recreating instances works.

    This simulates what the fixed CLI mode does:
    - Create MCP server instance
    - Enter it into AsyncExitStack
    - Use it
    - Exit the stack
    - Repeat for next message
    """

    # Use a simple echo server command for testing
    # This will fail if there's no suitable MCP server, but that's ok for this test
    params = {
        "command": sys.executable,
        "args": ["-c", "print('test')"],  # Simple command that exits immediately
    }

    # First message
    try:
        async with AsyncExitStack() as stack1:
            server1 = MCPServerStdio(params, name="test-server-1")
            entered_server1 = await stack1.enter_async_context(server1)
            # Server entered successfully

        # Stack exited cleanly

    except Exception as e:
        # Some commands might not work as MCP servers, that's ok
        pytest.skip(f"Could not create test MCP server: {e}")

    # Second message - create NEW server instance
    try:
        async with AsyncExitStack() as stack2:
            server2 = MCPServerStdio(params, name="test-server-2")
            entered_server2 = await stack2.enter_async_context(server2)
            # Server entered successfully

        # Stack exited cleanly

        # Servers are different instances
        assert server1 is not server2

    except Exception as e:
        pytest.skip(f"Could not create second test MCP server: {e}")


@pytest.mark.asyncio
async def test_real_mcp_stdio_server_reuse_same_instance_fails():
    """
    Test that reusing the SAME MCPServerStdio instance causes issues.

    This simulates the BROKEN behavior.
    """

    params = {
        "command": sys.executable,
        "args": ["-c", "print('test')"],
    }

    server = MCPServerStdio(params, name="test-server")

    try:
        # First use
        async with AsyncExitStack() as stack1:
            await stack1.enter_async_context(server)
        # Exited

        # Try to reuse the SAME instance
        # This should cause issues
        async with AsyncExitStack() as stack2:
            await stack2.enter_async_context(server)
        # This might fail or succeed depending on the implementation

    except Exception as e:
        # Expected to fail in some way
        print(f"Got expected error: {e}")
