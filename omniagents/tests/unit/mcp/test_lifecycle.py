"""Tests for MCP server lifecycle and state management.

This module tests that MCP servers maintain state correctly across multiple
conversation turns within a session, and that AnyIO safety requirements are met.

Tests include:
- State preservation across runs within same session
- Session isolation (different sessions get different servers)
- AnyIO cross-task violation prevention
- Proper cleanup on session archive
"""

import asyncio
import types
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import anyio
import pytest

from omniagents.core.agents import service as service_module
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec, MCPServerConfig


class PlaywrightMockServer:
    """Mock Playwright MCP server that maintains browser state.

    This simulates the real Playwright MCP server which:
    - Spawns a subprocess (npx @playwright/mcp@latest)
    - Launches a browser instance
    - Maintains browser state (current URL, tabs, history, etc.)
    - Expects the subprocess to stay alive across multiple tool calls
    """

    def __init__(self):
        self.browser_state = {"current_url": None, "history": []}
        self.subprocess_spawn_count = (
            0  # Tracks how many times "subprocess" was spawned
        )
        self.cleanup_count = 0

    async def connect(self):
        """Simulates spawning the Playwright subprocess and launching browser."""
        self.subprocess_spawn_count += 1

    async def cleanup(self):
        """Simulates killing the subprocess and browser."""
        self.cleanup_count += 1
        self.browser_state = {"current_url": None, "history": []}

    async def call_tool(self, name: str, args: dict):
        """Simulate Playwright MCP tools."""
        if name == "browser_navigate":
            url = args["url"]
            self.browser_state["current_url"] = url
            self.browser_state["history"].append(url)
            return f"Navigated to {url}"
        elif name == "browser_snapshot":
            return f"Current page: {self.browser_state['current_url']}"
        elif name == "browser_close":
            self.browser_state["current_url"] = None
            return "Browser closed (but subprocess still alive)"
        return "Unknown tool"


@pytest.mark.asyncio
async def test_stdio_mcp_server_preserves_state_across_runs(monkeypatch):
    """
    CRITICAL TEST: Stateful MCP servers must maintain state across runs.

    Real-world scenario with Playwright MCP:
    - User: "Navigate to google.com"
    - Assistant: [Uses browser_navigate tool] "I've navigated to google.com"
    - User: "What page am I on?"
    - Assistant: [Uses browser_snapshot tool] "You're on google.com"
    - User: "Navigate to github.com"
    - Assistant: [Uses browser_navigate tool] "I've navigated to github.com"
    - User: "Show my browsing history"
    - Assistant: [Checks history] "You visited google.com then github.com"

    This ONLY works if the browser subprocess stays alive across all turns.
    Currently BROKEN: subprocess is killed and restarted between each turn.
    """

    server = PlaywrightMockServer()

    def fake_build_mcp(spec, settings):
        return [server]

    monkeypatch.setattr(
        "omniagents.core.agents.service.build_mcp_servers", fake_build_mcp
    )

    # Mock stream_agent to simulate agent calling MCP tools
    async def mock_stream(agent, prompt, session, channel, **kwargs):
        # Access MCP servers from agent
        mcp_servers = getattr(agent, "mcp_servers", [])
        if not mcp_servers:
            return {"usage": {"input_tokens": 10}, "model": "gpt-4"}

        mcp_server = mcp_servers[0]

        # Simulate agent calling tools based on prompt
        if "navigate" in prompt.lower() and "google" in prompt.lower():
            result = await mcp_server.call_tool(
                "browser_navigate", {"url": "https://google.com"}
            )
            session.append_message({"role": "assistant", "content": result})
        elif "what page" in prompt.lower() or "where am i" in prompt.lower():
            result = await mcp_server.call_tool("browser_snapshot", {})
            session.append_message({"role": "assistant", "content": result})
        elif "navigate" in prompt.lower() and "github" in prompt.lower():
            result = await mcp_server.call_tool(
                "browser_navigate", {"url": "https://github.com"}
            )
            session.append_message({"role": "assistant", "content": result})
        elif "history" in prompt.lower():
            history = mcp_server.browser_state["history"]
            session.append_message(
                {"role": "assistant", "content": f"History: {history}"}
            )

        await asyncio.sleep(0.01)
        return {"usage": {"input_tokens": 10}, "model": "gpt-4"}

    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", mock_stream)

    # Create agent with stdio MCP server (like Playwright)
    spec = AgentSpec(
        name="test_agent",
        welcome_text="Test",
        get_agent_instructions=lambda: "You are a helpful assistant with browser access.",
        build_agent=None,
        mcp_servers=[
            MCPServerConfig(
                name="playwright",
                type="stdio",  # ← This is the problematic type
                params={"command": "npx", "args": ["@playwright/mcp@latest"]},
            )
        ],
    )

    service = AgentService(spec)
    service.channel = object()

    # Turn 1: Navigate to google.com
    r1 = await service.start_run("Navigate to google.com")
    session_id = r1["session_id"]
    sess = service._session_manager.get_or_create(session_id)
    await asyncio.wait_for(sess.current_task, timeout=2)

    print(
        f"After Turn 1: spawn_count={server.subprocess_spawn_count}, current_url={server.browser_state['current_url']}"
    )

    assert (
        server.subprocess_spawn_count == 1
    ), "Should spawn subprocess once on first run"
    assert server.cleanup_count == 0, "Should not cleanup mid-session"
    assert server.browser_state["current_url"] == "https://google.com"

    # Turn 2: Check current page (state must persist!)
    r2 = await service.start_run("What page am I on?", session_id=session_id)
    await asyncio.wait_for(sess.current_task, timeout=2)

    print(
        f"After Turn 2: spawn_count={server.subprocess_spawn_count}, current_url={server.browser_state['current_url']}"
    )

    # THIS IS THE CRITICAL ASSERTION THAT WILL FAIL
    assert server.subprocess_spawn_count == 1, (
        f"FAILURE: Subprocess was respawned! spawn_count={server.subprocess_spawn_count}. "
        f"Expected 1 (reuse), got {server.subprocess_spawn_count} (recreated). "
        f"This means browser state was lost between turns."
    )

    assert (
        server.cleanup_count == 0
    ), f"FAILURE: Server was cleaned up mid-session! cleanup_count={server.cleanup_count}"

    assert (
        server.browser_state["current_url"] == "https://google.com"
    ), f"FAILURE: Browser state lost! current_url={server.browser_state['current_url']}, expected https://google.com"

    # Turn 3: Navigate to github
    r3 = await service.start_run("Navigate to github.com", session_id=session_id)
    await asyncio.wait_for(sess.current_task, timeout=2)

    print(
        f"After Turn 3: spawn_count={server.subprocess_spawn_count}, history={server.browser_state['history']}"
    )

    assert (
        server.subprocess_spawn_count == 1
    ), f"Subprocess respawned again! spawn_count={server.subprocess_spawn_count}"
    assert (
        len(server.browser_state["history"]) == 2
    ), f"History should have 2 entries, got {len(server.browser_state['history'])}"

    # Turn 4: Check history (must persist across all turns)
    r4 = await service.start_run("Show my history", session_id=session_id)
    await asyncio.wait_for(sess.current_task, timeout=2)

    print(f"After Turn 4: history={server.browser_state['history']}")

    assert server.browser_state["history"] == [
        "https://google.com",
        "https://github.com",
    ], f"History lost! Got: {server.browser_state['history']}"

    # ONLY when session is archived should cleanup happen
    await service.archive_session(session_id)

    print(f"After archive: cleanup_count={server.cleanup_count}")
    assert server.cleanup_count == 1, "Should cleanup once when session archived"


@pytest.mark.asyncio
async def test_stdio_mcp_different_sessions_get_isolated_instances(monkeypatch):
    """Test that different sessions get isolated MCP server instances.

    Session A and Session B should each have their own browser subprocess,
    with completely isolated state.
    """

    servers_created = []

    def fake_build_mcp(spec, settings):
        server = PlaywrightMockServer()
        servers_created.append(server)
        return [server]

    monkeypatch.setattr(
        "omniagents.core.agents.service.build_mcp_servers", fake_build_mcp
    )

    async def mock_stream(agent, prompt, session, channel, **kwargs):
        mcp_servers = getattr(agent, "mcp_servers", [])
        if mcp_servers:
            await mcp_servers[0].call_tool("browser_navigate", {"url": prompt})
        await asyncio.sleep(0.01)
        return {"usage": {"input_tokens": 5}, "model": "gpt-4"}

    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", mock_stream)

    spec = AgentSpec(
        name="test",
        welcome_text="Test",
        get_agent_instructions=lambda: "Test",
        build_agent=None,
        mcp_servers=[
            MCPServerConfig(
                name="playwright",
                type="stdio",
                params={"command": "npx", "args": ["@playwright/mcp@latest"]},
            )
        ],
    )

    service = AgentService(spec)
    service.channel = object()

    # Session A
    r1 = await service.start_run("https://google.com")
    session_a = r1["session_id"]
    sess_a = service._session_manager.get_or_create(session_a)
    await asyncio.wait_for(sess_a.current_task, timeout=2)

    # Session B
    r2 = await service.start_run("https://github.com")
    session_b = r2["session_id"]
    sess_b = service._session_manager.get_or_create(session_b)
    await asyncio.wait_for(sess_b.current_task, timeout=2)

    # Should have created 2 different server instances
    assert (
        len(servers_created) >= 2
    ), f"Expected at least 2 server instances (one per session), got {len(servers_created)}"

    # Each session should have isolated state
    # (This part depends on implementation - current code might reuse or not)
    print(f"Created {len(servers_created)} server instances for 2 sessions")
    print(f"Server 0 history: {servers_created[0].browser_state['history']}")
    if len(servers_created) > 1:
        print(f"Server 1 history: {servers_created[1].browser_state['history']}")


# =============================================================================
# AnyIO Safety Tests
# =============================================================================


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


# =============================================================================
# Hypothesis Tests (from hang debugging investigation)
# =============================================================================


@dataclass
class DummyAgent:
    name: str = "agent"
    tools: list = field(default_factory=list)
    instructions: str = ""
    model: str = "model"
    model_settings: dict | None = None
    mcp_servers: list = field(default_factory=list)

    def set_approval_callback(self, callback):
        self.approval_callback = callback


async def default_build_agent(settings, primary_server, stub_app):
    return DummyAgent()


def make_spec(**overrides):
    base = {
        "name": "Agent",
        "welcome_text": "Welcome",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: ""),
        "build_agent": overrides.pop("build_agent", default_build_agent),
    }
    base.update(overrides)
    return AgentSpec(**base)


class FakeMCPServer:
    """Simulates an MCP server that tracks enter/exit calls."""

    def __init__(self, tool_called_event=None):
        self.entered = False
        self.exited = False
        self.tool_called_event = tool_called_event

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Simulate a tool call."""
        if self.tool_called_event:
            self.tool_called_event.set()
        await asyncio.sleep(0.01)  # Simulate some work
        return f"Result from {name}"


def attach_capture(service):
    """Attach event capture to service for observing broadcasts."""
    events = []

    async def capture(
        self, session, method, payload, *, queue_if_unseen=False, track_live=False
    ):
        events.append({"method": method, "payload": payload, "queue": queue_if_unseen})
        return True

    service._broadcast_session = types.MethodType(capture, service)
    return events


@pytest.mark.asyncio
async def test_minimal_reproduction_sequential_runs_with_mcp(monkeypatch):
    """
    Minimal reproduction: Run agent twice with MCP server connection reuse.

    This test should reproduce the hang if connection reuse is the issue.
    """
    server = FakeMCPServer()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    run_count = {"value": 0}

    async def mock_stream(*args, **kwargs):
        """Mock stream_agent that simulates successful runs."""
        run_count["value"] += 1
        # Simulate some async work
        await asyncio.sleep(0.01)
        return {
            "usage": {"input_tokens": 5},
            "model": "test-model",
            "max_input_tokens": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", mock_stream)

    spec = make_spec()
    spec.mcp_servers = [object()]  # Signal that MCP servers are configured
    service = AgentService(spec)
    service.channel = object()
    attach_capture(service)

    # First run
    run_info_1 = await service.start_run("First prompt")
    sess = service._session_manager.get_or_create(run_info_1["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=2)

    # Verify MCP server was entered and is still active (connection reuse)
    assert server.entered is True
    assert server.exited is False
    assert run_count["value"] == 1

    # Second run - this should trigger the hang if connection reuse is the issue
    run_info_2 = await service.start_run("Second prompt", session_id=sess.id)
    # This wait_for will timeout if there's a hang
    await asyncio.wait_for(sess.current_task, timeout=2)

    # If we reach here, the run completed
    assert run_count["value"] == 2
    assert server.exited is False  # Still not exited (connection reused)


@pytest.mark.asyncio
async def test_hypothesis_1_mcp_without_safeagent(monkeypatch):
    """
    Hypothesis 1: SafeAgent approval callback blocks event stream.

    Test MCP tool call WITHOUT SafeAgent wrapper to see if it hangs.
    If this passes but the full version hangs, SafeAgent is the issue.
    """
    server = FakeMCPServer()
    tool_called = asyncio.Event()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    # Mock build_agent to NOT use SafeAgent
    async def build_agent_without_safe(settings, primary_server, stub_app):
        agent = DummyAgent()
        # Don't set approval callback - agent will run tools without approval
        return agent

    spec = make_spec(build_agent=build_agent_without_safe)
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()

    # Mock stream_agent to simulate MCP tool call
    async def mock_stream_with_tool_call(*args, **kwargs):
        # Simulate agent calling an MCP tool
        agent = args[0]
        if hasattr(agent, "mcp_servers") and agent.mcp_servers:
            mcp_server = agent.mcp_servers[0]
            if hasattr(mcp_server, "call_tool"):
                await mcp_server.call_tool("test_tool", {})
        await asyncio.sleep(0.01)
        return {
            "usage": {"input_tokens": 3},
            "model": "test",
            "max_input_tokens": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", mock_stream_with_tool_call)

    run_info = await service.start_run("Call MCP tool")
    sess = service._session_manager.get_or_create(run_info["session_id"])

    # If this completes, MCP tools work without SafeAgent
    await asyncio.wait_for(sess.current_task, timeout=2)
    assert server.entered is True


@pytest.mark.asyncio
async def test_hypothesis_3_approval_callback_auto_approve(monkeypatch):
    """
    Hypothesis 3: Approval callback deadlocks waiting for UI response.

    Mock approval callback to auto-approve and test with SafeAgent.
    If this passes but real approval version hangs, approval flow is the issue.
    """
    server = FakeMCPServer()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    # Track approval callback invocations
    approvals_requested = []

    # Mock build_agent to use SafeAgent with auto-approval
    async def build_agent_with_auto_approval(settings, primary_server, stub_app):
        agent = DummyAgent()

        # Create auto-approval callback
        async def auto_approve_callback(tool_name, args, metadata):
            approvals_requested.append({"tool": tool_name, "args": args})
            # Auto-approve immediately
            return (True, False, None)  # (approved, always_approve, error_msg)

        agent.set_approval_callback(auto_approve_callback)
        return agent

    spec = make_spec(build_agent=build_agent_with_auto_approval, use_safe_agent=True)
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()

    # Mock stream_agent to simulate requesting approval
    async def mock_stream_with_approval(*args, **kwargs):
        agent = args[0]
        # Simulate SafeAgent requesting approval
        if hasattr(agent, "approval_callback"):
            await agent.approval_callback("test_tool", {"arg": "value"}, {})
        await asyncio.sleep(0.01)
        return {
            "usage": {"input_tokens": 3},
            "model": "test",
            "max_input_tokens": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", mock_stream_with_approval)

    run_info = await service.start_run("Test approval")
    sess = service._session_manager.get_or_create(run_info["session_id"])

    # Should complete if auto-approval works
    await asyncio.wait_for(sess.current_task, timeout=2)

    # Note: In this test setup, the approval callback is set but not actually invoked
    # because mock_stream_with_approval directly calls the callback rather than
    # going through the real agent tool execution flow.
    # The test verifies that having an approval callback doesn't cause hangs.


# Note: Hypothesis 4 (stdio vs HTTP transport) would require actual MCP server instances
# which is beyond the scope of unit tests. We'll test this manually if needed.


class AnyIOMCPServer:
    """
    Realistic MCP server that uses AnyIO task groups like real stdio transport.

    This simulates the behavior of mcp.client.stdio.stdio_client which uses
    anyio.create_task_group() internally.
    """

    def __init__(self):
        self.entered = False
        self.exited = False
        self._task_group = None
        self._task_group_context = None
        self._entry_task_id = None

    async def __aenter__(self):
        """Enter creates an AnyIO task group in the current task."""
        self.entered = True
        # Record which asyncio task we're in
        self._entry_task_id = id(asyncio.current_task())

        # Create AnyIO task group - this ties it to the current asyncio task
        self._task_group_context = anyio.create_task_group()
        self._task_group = await self._task_group_context.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Exit must be called from the same task that called __aenter__."""
        current_task_id = id(asyncio.current_task())

        # This is where the error would occur - if we exit from a different task
        if current_task_id != self._entry_task_id:
            # AnyIO would raise: "Attempted to exit cancel scope in a different task"
            # For testing, we'll raise a similar error
            raise RuntimeError(
                f"AnyIO violation: Task group entered in task {self._entry_task_id} "
                f"but exited in task {current_task_id}"
            )

        if self._task_group_context:
            await self._task_group_context.__aexit__(exc_type, exc, tb)
        self.exited = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Simulate calling a tool using the task group."""
        if not self._task_group:
            raise RuntimeError("Server not entered")

        result_holder = {}

        async def tool_task():
            await asyncio.sleep(0.01)
            result_holder["result"] = f"Result from {name}"

        # Start tool task in the AnyIO task group
        self._task_group.start_soon(tool_task)
        await asyncio.sleep(0.02)  # Wait for task to complete
        return result_holder.get("result", "no result")


@pytest.mark.asyncio
async def test_anyio_mcp_server_cross_task_violation():
    """
    Demonstrate that AnyIO MCP servers can't be safely reused across tasks.

    This test should fail/error showing the cross-task violation.
    """
    server = AnyIOMCPServer()

    # Task 1: Enter the server (as a separate asyncio task)
    async def task1():
        await server.__aenter__()
        await asyncio.sleep(0.01)

    # Create actual asyncio task (not just awaiting the coroutine)
    t1 = asyncio.create_task(task1())
    await t1
    assert server.entered is True

    # Task 2: Try to exit the server (as a different asyncio task)
    async def task2():
        # This will raise because we're in a different asyncio task
        await server.__aexit__(None, None, None)

    # Create separate asyncio task
    t2 = asyncio.create_task(task2())

    with pytest.raises(RuntimeError, match="AnyIO violation"):
        await t2


@pytest.mark.asyncio
async def test_anyio_mcp_with_connection_reuse_reproduces_hang(monkeypatch):
    """
    CRITICAL TEST: Reproduce the hang with AnyIO-based MCP server and connection reuse.

    This test simulates what happens in production:
    1. First run: Creates MCP server with AnyIO task group in Task A
    2. Connection reuse: Server instance is preserved in session
    3. Second run: Tries to use same server instance in Task B
    4. Result: Cross-task violation or hang

    This should reproduce the actual bug.
    """
    server = AnyIOMCPServer()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    run_count = {"value": 0}
    tool_called_count = {"value": 0}

    async def mock_stream(*args, **kwargs):
        """Mock stream that calls MCP tool."""
        run_count["value"] += 1
        agent = args[0]

        # Simulate calling an MCP tool on second run
        if (
            run_count["value"] == 2
            and hasattr(agent, "mcp_servers")
            and agent.mcp_servers
        ):
            mcp_server = agent.mcp_servers[0]
            if hasattr(mcp_server, "call_tool"):
                # This will try to use the task group from the first run's task
                # Should trigger cross-task violation
                await mcp_server.call_tool("test_tool", {})
                tool_called_count["value"] += 1

        await asyncio.sleep(0.01)
        return {
            "usage": {"input_tokens": 5},
            "model": "test-model",
            "max_input_tokens": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", mock_stream)

    spec = make_spec()
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()

    # First run - creates server with task group in this task
    run_info_1 = await service.start_run("First prompt")
    sess = service._session_manager.get_or_create(run_info_1["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=2)

    assert server.entered is True
    assert server.exited is False  # Connection reused
    assert run_count["value"] == 1

    # Second run - different asyncio task, same server instance
    # This should trigger the bug
    run_info_2 = await service.start_run("Second prompt", session_id=sess.id)

    # This should raise an error or timeout due to the cross-task violation
    try:
        await asyncio.wait_for(sess.current_task, timeout=2)
        # If we get here, check if tool was actually called
        if tool_called_count["value"] > 0:
            pytest.fail(
                "Tool call succeeded when it should have failed due to cross-task violation"
            )
    except (RuntimeError, asyncio.TimeoutError) as e:
        # Expected: either RuntimeError from AnyIO or timeout from hang
        print(f"Expected error occurred: {e}")
        assert True
