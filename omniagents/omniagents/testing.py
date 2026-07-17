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

from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace


@pytest.fixture(autouse=True)
def _default_permissive_workspace():
    """Install a permissive ``HostWorkspace(root="/")`` for every test.

    The W0 host runtime installs the workspace from the run context in
    production; tests that go directly through tools or server functions
    bypass that pathway, so without a default the workspace falls back
    to ``HostWorkspace(cwd)`` and rejects absolute paths under
    ``/tmp/pytest-*``. ``invoke_tool`` may swap in a tighter
    ``HostWorkspace(root=workspace_root)`` for the duration of a single
    invocation; this fixture is the surrounding default.
    """
    token = set_workspace(HostWorkspace(root="/"))
    try:
        yield
    finally:
        reset_workspace(token)


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

    Mirrors the W0 host-runtime contract: when a ``workspace_root`` is set
    on the run context, install a matching ``HostWorkspace`` on the
    workspace contextvar for the duration of the call so workspace-routed
    tool bodies resolve paths against the agent's configured root.

    Also exposes helper methods:
    - ``invoke_tool.run(coro)``: schedule a coroutine on the shared loop.
    - ``invoke_tool.wait_for_job_exit(job, timeout)``: block (synchronously
      from the test's POV) until the Job's watcher records an exit.
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
        """Block until ``job.exit_code`` is populated by the watcher.

        Replacement for the old ``job.popen.wait(timeout=...)`` pattern,
        which no longer applies now that Job wraps a ``ProcessHandle``."""
        return test_event_loop.run_until_complete(_wait_for_exit(job, timeout))

    def run(coro):
        """Run an arbitrary coroutine on the test's shared event loop."""
        return test_event_loop.run_until_complete(coro)

    _invoke.wait_for_job_exit = wait_for_job_exit
    _invoke.run = run
    return _invoke
