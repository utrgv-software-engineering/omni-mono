"""End-to-end-ish check on the bash-jobs sweeper -> UI broadcast pipe.

Drives discovery the SAME way ``omniagents.core.config.loader`` does
(tools first, then server_functions) so the function objects under test
have the exact module bindings the agent runtime uses. Then spawns a
real Popen via the agent's ``execute_bash`` tool, kicks the sweeper via
a ``server_call("bash_jobs.list")`` against a real ``AgentService``,
and asserts a fake channel attached to the session receives a
``client_request`` for ``ui.bash_jobs.update`` describing the exited
job.

This was the missing integration test — the sweeper unit tests cover
the polling loop, the registry tests cover the spawn/exit round-trip,
but nothing exercised the discovery-load order. Historically discovery
evicted tool modules from sys.modules after loading, so the later
``from …._bash_jobs import`` in server_functions/bash_jobs.py created a
SECOND ``_JobRegistry``. Discovery now keeps canonically-named modules
cached (normal import semantics), so the module-global registry is a
true process singleton — this test pins that.
"""

from __future__ import annotations

import asyncio
import time
import subprocess
from pathlib import Path

import pytest

import omniagents.builtin.tools.bash_tool as bash_tool
from omniagents.builtin.server_functions import bash_jobs as bj
from omniagents.builtin.tools._bash_jobs import get_registry, Job, log_path_for, new_job_id


@pytest.fixture(autouse=True)
def _isolate_registry_and_sweeper():
    """Reset the registry and tear down any sweeper between tests so the
    in-process singleton doesn't leak state across cases."""
    reg = get_registry()
    reg._jobs.clear()  # type: ignore[attr-defined]
    # Stop any previously-started sweeper so each test gets a fresh one.
    if bj._sweeper is not None:
        try:
            bj._sweeper.stop()
        except Exception:
            pass
    bj._sweeper = None
    yield
    if bj._sweeper is not None:
        try:
            bj._sweeper.stop()
        except Exception:
            pass
    bj._sweeper = None
    for job in list(reg._jobs.values()):  # type: ignore[attr-defined]
        if job.poll_returncode() is None:
            try:
                job.popen.kill()
                job.popen.wait(timeout=2)
            except Exception:
                pass
    reg._jobs.clear()  # type: ignore[attr-defined]


class _FakeChannel:
    """Records every ``async_call`` so the test can scan for the
    ``client_request`` for ``ui.bash_jobs.update``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def async_call(self, method: str, args):
        self.calls.append((method, args))


def _make_service():
    """Construct a real AgentService and register the bash_jobs.* server
    functions the way the loader would for the omni-code agent."""
    from unittest.mock import Mock
    from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
    from omniagents.core.agents.service import AgentService

    spec = Mock(spec=AgentSpec)
    spec.name = "t"
    spec.model_name = "gpt-4"
    spec.safe_agent_options = SafeAgentOptions()
    spec.use_safe_agent = False
    spec.context = None
    spec.variables = None
    spec.welcome_text = ""
    spec.realtime_mode = False
    spec.project_slug = "test_project"
    spec.agent_slug = "test_agent"
    service = AgentService(spec, default_workspace_root="/tmp")

    for fn in (
        bj.bash_jobs_list,
        bj.bash_jobs_kill,
        bj.bash_jobs_tail,
    ):
        service.register_server_function(
            (
                fn.__name__
                if not hasattr(fn, "_server_function_name")
                else fn._server_function_name
            ),
            fn,
        )
    # The decorator sets ``_server_function_name`` to the override; use
    # the canonical names here so server_call can find them by name.
    service._server_functions["bash_jobs.list"] = {
        "func": bj.bash_jobs_list,
        "description": "",
        "params_schema": None,
        "result_schema": None,
        "on_invoke": getattr(bj.bash_jobs_list, "_server_function_on_invoke", None),
        "strict": None,
    }
    service._server_functions["bash_jobs.kill"] = {
        "func": bj.bash_jobs_kill,
        "description": "",
        "params_schema": None,
        "result_schema": None,
        "on_invoke": getattr(bj.bash_jobs_kill, "_server_function_on_invoke", None),
        "strict": None,
    }
    service._server_functions["bash_jobs.tail"] = {
        "func": bj.bash_jobs_tail,
        "description": "",
        "params_schema": None,
        "result_schema": None,
        "on_invoke": getattr(bj.bash_jobs_tail, "_server_function_on_invoke", None),
        "strict": None,
    }
    return service


async def _spawn_quick_job_async(tmp_path: Path, session_id: str | None) -> str:
    """Spawn a real bg job through the workspace.spawn path that the
    migrated ``execute_bash(background=True)`` uses. Returns the job_id."""
    from omniagents import workspace as _ws
    import asyncio as _asyncio
    import shlex as _shlex
    from omniagents.builtin.tools._bash_jobs import _watch_job

    job_id = new_job_id()
    log_path = log_path_for(str(tmp_path), job_id)
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    # Pre-create the log file so the spawn's append-redirect lands on
    # something that exists, then spawn the command via the workspace.
    Path(log_path).touch()

    wrapped = f"(echo done && sleep 0.2) >> {_shlex.quote(log_path)} 2>&1 < /dev/null"
    handle = await _ws.spawn(wrapped, cwd=str(tmp_path), shell=True, pty=False)

    job = Job(
        job_id=job_id,
        command="echo done && sleep 0.2",
        cwd=str(tmp_path),
        log_path=log_path,
        started_at_wall=time.time(),
        started_at_monotonic=time.monotonic(),
        handle=handle,
        session_id=session_id,
    )
    job.watcher_task = _asyncio.create_task(
        _watch_job(job), name=f"bash-job-watcher-{job_id}"
    )
    get_registry().register(job)
    return job_id


def _spawn_quick_job(tmp_path: Path, session_id: str | None) -> str:
    """Sync wrapper around the async spawn helper for tests that aren't
    already running on a loop. Uses ``asyncio.run`` — fine here because
    this test file's tests don't multi-call across loops."""
    import asyncio as _asyncio

    return _asyncio.run(_spawn_quick_job_async(tmp_path, session_id))


def test_registry_unified_under_real_discovery_flow():
    """Pin the singleton across the loader's discovery cycle.

    Repro of the original "panel stuck on running" bug:
      1. ``discover_functions_in_dir`` loads tools/*.py (binds
         execute_bash to its module's get_registry).
      2. Cleanup deletes omniagents.builtin.tools._bash_jobs from sys.modules.
      3. ``discover_server_functions_in_dir`` loads
         server_functions/bash_jobs.py which does
         ``from omniagents.builtin.tools._bash_jobs import …`` — Python re-creates
         omniagents.builtin.tools._bash_jobs with a fresh ``_registry``.
      4. Result: agent uses registry A, sweeper polls registry B.

    The fix: discovery keeps canonically-named modules in sys.modules,
    so both bindings of ``get_registry`` resolve to the same module
    global no matter which import path reaches it.
    """
    import omniagents.builtin.server_functions as _builtin_sfs
    import omniagents.builtin.tools as _builtin_tools
    from pathlib import Path as _Path

    from omniagents.core.tools.discovery import discover_functions_in_dir
    from omniagents.core.server_functions.discovery import (
        discover_server_functions_in_dir,
    )

    repo_root = _Path(__file__).resolve().parents[1]
    builtin_tools_dir = _Path(_builtin_tools.__file__).parent
    builtin_sf_dir = _Path(_builtin_sfs.__file__).parent

    # Loader runs builtins first, then project dirs — match that order.
    # bash_jobs.* now ships as a builtin server function (capability-gated
    # via the shell pack); the project dir holds only product functions.
    builtin_map = discover_functions_in_dir(str(builtin_tools_dir))
    discover_functions_in_dir(str(repo_root / "tools"))
    sfs_map = discover_server_functions_in_dir(str(builtin_sf_dir))
    discover_server_functions_in_dir(str(repo_root / "server_functions"))

    execute_bash = builtin_map["execute_bash"]
    bash_jobs_list = sfs_map["bash_jobs.list"]["func"]

    # The agent runtime invokes these callables directly; the registry
    # each one reaches via its module globals must be the same instance.
    exec_globals = execute_bash._original_func.__globals__
    sf_globals = bash_jobs_list.__globals__

    exec_registry = exec_globals["get_registry"]()
    sf_registry = sf_globals["get_registry"]()

    assert exec_registry is sf_registry, (
        "execute_bash and bash_jobs.list resolved different _JobRegistry "
        f"instances after discovery (exec id={id(exec_registry)}, "
        f"sf id={id(sf_registry)}). The sweeper polls one while the "
        "agent registers jobs in the other and the UI panel never "
        "reflects natural exits."
    )


@pytest.mark.asyncio
async def test_sweeper_broadcasts_exit_to_session_channel(tmp_path):
    service = _make_service()

    # Build a real Session bound to the service's manager and attach a
    # fake channel — sweeper's _all_sessions(service) reads through
    # ``_session_manager._by_id``.
    session = service._session_manager.get_or_create("sess-X")
    channel = _FakeChannel()
    session.channels.append(channel)

    # 1) Spawn a real bg job tagged to this session. We're already inside
    # the test's async scope so call the async helper directly rather
    # than the sync wrapper.
    job_id = await _spawn_quick_job_async(tmp_path, session_id="sess-X")

    # 2) Kick the sweeper via the Ink-warmup path: server_call(bash_jobs.list).
    await service.server_call("bash_jobs.list", session_id="sess-X")

    # Speed up the sweeper so the test doesn't wait the default 2s tick.
    bj._sweeper._interval_s = 0.05

    try:

        # 3) Wait until the channel receives a ``client_request`` for
        #    ``ui.bash_jobs.update`` whose snapshot shows the job as exited.
        deadline = time.monotonic() + 3.0
        seen_exited = False
        while time.monotonic() < deadline:
            for method, payload in channel.calls:
                if method != "client_request":
                    continue
                if payload.get("function") != "ui.bash_jobs.update":
                    continue
                snap = (payload.get("args") or {}).get("snapshot") or []
                for entry in snap:
                    if entry.get("job_id") == job_id and entry.get("running") is False:
                        seen_exited = True
                        break
                if seen_exited:
                    break
            if seen_exited:
                break
            await asyncio.sleep(0.05)
    finally:
        pass

    assert seen_exited, (
        "sweeper never broadcast ui.bash_jobs.update with the job marked exited. "
        f"channel.calls={channel.calls!r}"
    )
