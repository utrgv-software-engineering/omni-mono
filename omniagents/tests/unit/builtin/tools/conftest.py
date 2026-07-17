"""Fixtures for background bash-job tests.

Background jobs spawn real asyncio subprocesses; their I/O machinery is
bound to whichever event loop created them, so every ``invoke_tool``
call in a test must run on one shared loop (see ``test_event_loop``).
"""

import asyncio
import json

import pytest
from agents.tool import ToolContext
from agents.usage import Usage

from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace


@pytest.fixture(autouse=True)
def _default_permissive_workspace():
    """Install a permissive ``HostWorkspace(root="/")`` for every test.

    Production installs the workspace from the run context; tests that go
    directly through tools bypass that pathway, so without a default the
    workspace falls back to ``HostWorkspace(cwd)`` and rejects absolute
    paths under ``/tmp/pytest-*``.
    """
    token = set_workspace(HostWorkspace(root="/"))
    try:
        yield
    finally:
        reset_workspace(token)


@pytest.fixture
def test_event_loop():
    """One asyncio event loop shared across every ``invoke_tool`` call in
    a single test (see module docstring)."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


@pytest.fixture
def invoke_tool(test_event_loop):
    """Invoke a tool on the shared test event loop.

    When a ``workspace_root`` is set on the run context, install a matching
    ``HostWorkspace`` for the duration of the call so workspace-routed tool
    bodies resolve paths against the agent's configured root.

    Helper methods:
    - ``invoke_tool.run(coro)``: schedule a coroutine on the shared loop.
    - ``invoke_tool.wait_for_job_exit(job, timeout)``: block until the
      Job's watcher records an exit.
    """

    def _invoke(tool, *, workspace_root=None, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        context_data = {}
        if workspace_root is not None:
            context_data["workspace_root"] = workspace_root
        ctx = ToolContext(
            context=context_data,
            usage=Usage(),
            tool_name=tool.name,
            tool_call_id="test-call",
            tool_arguments=payload,
        )
        token = set_workspace(
            HostWorkspace(root=workspace_root)
            if workspace_root
            else HostWorkspace(root="/")
        )
        try:
            return test_event_loop.run_until_complete(
                tool.on_invoke_tool(ctx, json.dumps(payload))
            )
        finally:
            reset_workspace(token)

    async def _wait_for_exit(job, timeout):
        deadline = test_event_loop.time() + timeout
        while job.exit_code is None:
            if test_event_loop.time() > deadline:
                raise TimeoutError(f"job {job.job_id} did not exit within {timeout}s")
            await asyncio.sleep(0.05)

    def wait_for_job_exit(job, timeout: float = 5.0):
        """Block until ``job.exit_code`` is populated by the watcher."""
        return test_event_loop.run_until_complete(_wait_for_exit(job, timeout))

    def run(coro):
        """Run an arbitrary coroutine on the test's shared event loop."""
        return test_event_loop.run_until_complete(coro)

    _invoke.wait_for_job_exit = wait_for_job_exit
    _invoke.run = run
    return _invoke
