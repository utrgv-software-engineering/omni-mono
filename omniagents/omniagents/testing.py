"""Reusable pytest fixtures for testing OmniAgents tools.

For test suites — the framework's own and downstream products' — that
invoke builtin-style tools directly, outside the AgentService run path.
Import the fixtures into a ``conftest.py`` so pytest collects them with
that conftest's directory scoping (deliberately NOT a ``pytest11``
entry-point plugin: the permissive-workspace fixture is autouse, and
plugin registration would apply it session-wide instead of only where a
conftest opts in)::

    from omniagents.testing import (  # noqa: F401
        _default_permissive_workspace,
        invoke_tool,
        test_event_loop,
    )

Requires ``pytest`` — import this module from test code only.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from agents.tool import ToolContext
from agents.usage import Usage

from omniagents.execution import (
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    OperationExecutionDescriptor,
    RequestContext,
    WorkspaceResource,
    bind_request_context,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace

_TEST_ENVIRONMENT_ID = "test-host-environment"
_TEST_WORKSPACE_ID = "test-workspace"
_TEST_ENVIRONMENT_GENERATION = 1
_TEST_SESSION_ID = "sess-test"


def _direct_invocation_context(workspace: HostWorkspace) -> RequestContext:
    """Build the dispatcher context used by direct test invocations."""
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=_TEST_ENVIRONMENT_ID,
        workspace_id=_TEST_WORKSPACE_ID,
        kind=EnvironmentKind.HOST,
        state=EnvironmentState.READY,
        generation=_TEST_ENVIRONMENT_GENERATION,
        capabilities=frozenset(EnvironmentCapability),
    )
    return RequestContext(
        operation=OperationExecutionDescriptor(
            name="testing.direct_invocation",
            resource_scope=ResourceScope.ENVIRONMENT,
            execution_requirements=(
                ExecutionRequirement.WORKSPACE_READ
                | ExecutionRequirement.WORKSPACE_WRITE
                | ExecutionRequirement.PROCESS
                | ExecutionRequirement.NETWORK
                | ExecutionRequirement.PTY
                | ExecutionRequirement.ENVIRONMENT_LIFECYCLE
            ),
        ),
        principal=None,
        workspace=WorkspaceResource(_TEST_WORKSPACE_ID),
        environment=descriptor,
        environment_lease=EnvironmentLease(descriptor, workspace),
    )


@pytest.fixture(autouse=True)
def _default_permissive_workspace():
    """Install an explicit permissive host environment for every test.

    Direct tool/server-function tests bypass dispatcher routing. Model that
    boundary with a real ``RequestContext`` and ``EnvironmentLease`` instead
    of reviving an implicit host fallback. ``invoke_tool`` may still swap in a
    tighter Workspace adapter for one tool invocation.
    """
    workspace = HostWorkspace(root="/")
    workspace_token = set_workspace(workspace)
    request_token = bind_request_context(_direct_invocation_context(workspace))
    try:
        yield
    finally:
        reset_request_context(request_token)
        reset_workspace(workspace_token)


@pytest.fixture
def test_event_loop():
    """One asyncio event loop shared across every ``invoke_tool`` call in
    a single test.

    Background bash jobs spawn asyncio subprocesses whose I/O machinery
    (drain task, child watcher, watcher task in ``_bash_jobs._watch_job``)
    is bound to whichever loop they were created in. If each
    ``invoke_tool`` call ran its own loop, the asyncio subprocess from
    the first call would be orphaned by the second call's loop and the
    Job's exit code would never get cached. Sharing the loop fixes both:
    long-running spawns survive across multiple invocations, and the
    watcher task keeps observing them.
    """
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        # Cancel any tasks the test left running so close() doesn't warn.
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


@pytest.fixture
def invoke_tool(test_event_loop):
    """Invoke a tool on the shared test event loop.

    Install a matching ``HostWorkspace`` and explicit ``EnvironmentLease`` for
    the duration of each call so workspace-routed tool bodies resolve paths
    against the agent's configured root. The ToolContext also receives the
    environment identity fields that the production dispatcher supplies.

    Also exposes helper methods:
    - ``invoke_tool.run(coro)``: schedule a coroutine on the shared loop.
    - ``invoke_tool.wait_for_job_exit(job, timeout)``: block (synchronously
      from the test's POV) until the Job's watcher records an exit.
    """

    def _invoke(tool, *, workspace_root=None, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        context_data = {
            "environment_id": _TEST_ENVIRONMENT_ID,
            "environment_generation": _TEST_ENVIRONMENT_GENERATION,
            "session_id": _TEST_SESSION_ID,
        }
        if workspace_root is not None:
            context_data["workspace_root"] = workspace_root
        ctx = ToolContext(
            context=context_data,
            usage=Usage(),
            tool_name=tool.name,
            tool_call_id="test-call",
            tool_arguments=payload,
        )
        workspace = (
            HostWorkspace(root=workspace_root)
            if workspace_root
            else HostWorkspace(root="/")
        )
        workspace_token = set_workspace(workspace)
        request_token = bind_request_context(_direct_invocation_context(workspace))
        try:
            return test_event_loop.run_until_complete(
                tool.on_invoke_tool(ctx, json.dumps(payload))
            )
        finally:
            reset_request_context(request_token)
            reset_workspace(workspace_token)

    async def _wait_for_exit(job, timeout):
        deadline = test_event_loop.time() + timeout
        while job.exit_code is None:
            if test_event_loop.time() > deadline:
                raise TimeoutError(f"job {job.job_id} did not exit within {timeout}s")
            await asyncio.sleep(0.05)

    def wait_for_job_exit(job, timeout: float = 5.0):
        """Block until ``job.exit_code`` is populated by the watcher.

        Replacement for the old ``job.popen.wait(timeout=...)`` pattern,
        which no longer applies now that Job wraps a ``ProcessHandle``."""
        return test_event_loop.run_until_complete(_wait_for_exit(job, timeout))

    def run(coro):
        """Run an arbitrary coroutine on the test's shared event loop."""
        return test_event_loop.run_until_complete(coro)

    _invoke.wait_for_job_exit = wait_for_job_exit  # type: ignore[attr-defined]
    _invoke.run = run  # type: ignore[attr-defined]
    return _invoke
