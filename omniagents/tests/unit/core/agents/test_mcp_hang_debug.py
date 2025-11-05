"""
Systematic investigation of MCP agent hang issue.

Tests multiple hypotheses to identify root cause:
1. SafeAgent approval callback blocks event stream
2. MCP connection reuse causes AnyIO violations
3. Approval callback deadlocks waiting for UI response
4. MCP stdio transport has issues
"""

import asyncio
from dataclasses import dataclass, field
import types
from typing import Any

import anyio
import pytest

from omniagents.core.agents import service as service_module
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec


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
        "header_title": "Header",
        "page_title": "Page",
        "welcome_text": "Welcome",
        "client_storage_prefix": "agent",
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

    async def capture(self, session, method, payload, *, queue_if_unseen=False):
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
            "context_window": 4096,
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
            "context_window": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", mock_stream_with_tool_call)

    run_info = await service.start_run("Call MCP tool")
    sess = service._session_manager.get_or_create(run_info["session_id"])

    # If this completes, MCP tools work without SafeAgent
    await asyncio.wait_for(sess.current_task, timeout=2)
    assert server.entered is True


@pytest.mark.asyncio
async def test_hypothesis_2_disable_connection_reuse(monkeypatch):
    """
    Hypothesis 2: MCP connection reuse causes AnyIO violations.

    Force fresh MCP connections per run and verify it works.
    If this passes but reuse version hangs, connection reuse is the issue.
    """
    servers_created = []

    def fake_build_mcp(spec_arg, settings_arg):
        # Create NEW server instance each time
        server = FakeMCPServer()
        servers_created.append(server)
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    # Patch to force new connections by clearing previous state
    original_build = AgentService._build_agent_for_session

    async def force_fresh_connections(self, session, settings):
        # Clear previous MCP state to force fresh connections
        if hasattr(session, "_mcp_exit_stack"):
            stack = getattr(session, "_mcp_exit_stack")
            if stack is not None:
                await stack.aclose()
        setattr(session, "_mcp_exit_stack", None)
        setattr(session, "_mcp_servers_active", [])

        return await original_build(self, session, settings)

    monkeypatch.setattr(
        AgentService, "_build_agent_for_session", force_fresh_connections
    )

    async def mock_stream(*args, **kwargs):
        await asyncio.sleep(0.01)
        return {
            "usage": {"input_tokens": 3},
            "model": "test",
            "context_window": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", mock_stream)

    spec = make_spec()
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()

    # First run
    run_info_1 = await service.start_run("First")
    sess = service._session_manager.get_or_create(run_info_1["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=2)

    assert len(servers_created) == 1
    assert servers_created[0].entered is True
    # Note: Server not exited yet (kept alive for potential reuse)
    assert servers_created[0].exited is False

    # Second run - should create new server
    run_info_2 = await service.start_run("Second", session_id=sess.id)
    await asyncio.wait_for(sess.current_task, timeout=2)

    # Verify new server was created and old one closed
    assert len(servers_created) == 2
    assert servers_created[0].exited is True  # Old server closed
    assert servers_created[1].entered is True


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
            "context_window": 4096,
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
async def test_fix_stdio_connections_not_reused(monkeypatch):
    """
    Test that stdio MCP connections are NOT reused (the fix).

    This test verifies that when an MCP server uses stdio transport,
    the connection is recreated for each run instead of being reused.
    This prevents AnyIO cross-task violations.
    """
    from omniagents.core.agents.specs import MCPServerConfig

    servers_created = []

    def fake_build_mcp(spec_arg, settings_arg):
        # Create NEW AnyIO server each time
        server = AnyIOMCPServer()
        servers_created.append(server)
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    async def mock_stream(*args, **kwargs):
        await asyncio.sleep(0.01)
        return {
            "usage": {"input_tokens": 3},
            "model": "test",
            "context_window": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", mock_stream)

    spec = make_spec()
    # Create a stdio MCP server config
    stdio_config = MCPServerConfig(
        name="test-stdio",
        type="stdio",
        params={"command": "python", "args": ["-m", "test"]},
    )
    spec.mcp_servers = [stdio_config]

    service = AgentService(spec)
    service.channel = object()

    # First run
    run_info_1 = await service.start_run("First")
    sess = service._session_manager.get_or_create(run_info_1["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=2)

    assert len(servers_created) == 1
    assert servers_created[0].entered is True
    # Server stays alive after first run (will be closed on next run)
    assert servers_created[0].exited is False

    # Second run - should create NEW server (not reuse)
    run_info_2 = await service.start_run("Second", session_id=sess.id)
    await asyncio.wait_for(sess.current_task, timeout=2)

    # Verify:
    # 1. Old server was closed when second run started
    assert servers_created[0].exited is True
    # 2. New server was created
    assert len(servers_created) == 2
    assert servers_created[1].entered is True
    # 3. They are different instances (no reuse)
    assert servers_created[0] is not servers_created[1]


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
            "context_window": 4096,
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
