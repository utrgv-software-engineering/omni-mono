"""Tests for omniagents.builtin.tools.bash_tool — local execute_bash with background mode plus
the bash_status / bash_logs / bash_kill companion tools.

Foreground behavior is covered by test_bash_tool.py. Background tests use real subprocesses (sleep / echo) since the value of
this feature is precisely that the parent does not block on them.
"""

from __future__ import annotations

import os
import subprocess
import time
import types

import pytest

import omniagents.builtin.tools.bash_tool as bash_tool
from omniagents.builtin.tools import _bash_jobs
from omniagents.builtin.tools._bash_jobs import Job, get_registry
from omniagents.workspace.sandbox import SandboxWorkspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_registry(invoke_tool):
    """Drop any leftover jobs between tests so list_all is deterministic."""
    reg = get_registry()
    reg._jobs.clear()  # type: ignore[attr-defined]
    yield
    # Best-effort: kill anything we forgot about — terminate via the
    # workspace ProcessHandle, on the shared test event loop.
    for job in list(reg._jobs.values()):  # type: ignore[attr-defined]
        if job.exit_code is None:
            try:
                invoke_tool.run(job.handle.terminate(force_after=0.5))
            except Exception:
                pass
        if job.watcher_task is not None and not job.watcher_task.done():
            job.watcher_task.cancel()
    reg._jobs.clear()  # type: ignore[attr-defined]


def _meta(result):
    return result.ui_metadata["metadata"]


def _snapshot(result):
    return result.ui_metadata.get("bash_jobs_snapshot")


# ---------------------------------------------------------------------------
# Background mode
# ---------------------------------------------------------------------------


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestBackgroundExecuteBash:
    @pytest.mark.asyncio
    async def test_vanished_sandbox_pty_completes_and_notifies_once(
        self, monkeypatch, tmp_path
    ):
        class _FakeUpdate:
            process_id = 42
            output = b"sh: illegal option\n"
            exit_code = None

        class _FakeSession:
            def supports_pty(self):
                return True

            async def pty_exec_start(self, *args, **kwargs):
                return _FakeUpdate()

            async def pty_write_stdin(self, **kwargs):
                raise ValueError("unknown pty process_id 42")

        workspace = SandboxWorkspace(session=_FakeSession())
        handle = await workspace.spawn("set -o pipefail", pty=False)
        notifications = []

        async def _capture_notification(job):
            notifications.append(job.exit_code)

        monkeypatch.setattr(_bash_jobs, "_notify_job_exit", _capture_notification)
        job = Job(
            job_id="bash-vanished",
            command="set -o pipefail",
            cwd=str(tmp_path),
            log_path=str(tmp_path / "bash-vanished.log"),
            started_at_wall=time.time(),
            started_at_monotonic=time.monotonic(),
            handle=handle,
            session_id="session-1",
        )

        await _bash_jobs._watch_job(job)

        assert job.exit_code == 1
        assert job.finished_at_monotonic is not None
        assert notifications == [1]

    def test_snapshot_attached_to_spawn_result(self, invoke_tool, tmp_path):
        result = invoke_tool(
            bash_tool.execute_bash,
            command="sleep 0.2",
            background=True,
            workspace_root=str(tmp_path),
        )
        snap = _snapshot(result)
        assert isinstance(snap, list) and len(snap) == 1
        entry = snap[0]
        assert entry["job_id"] == _meta(result)["job_id"]
        assert entry["pid"] == _meta(result)["pid"]
        assert entry["running"] is True
        assert entry["exit_code"] is None

    def test_snapshot_grows_across_spawns_and_marks_exited(self, invoke_tool, tmp_path):
        spawn_a = invoke_tool(
            bash_tool.execute_bash,
            command="echo a",
            background=True,
            workspace_root=str(tmp_path),
        )
        spawn_b = invoke_tool(
            bash_tool.execute_bash,
            command="sleep 0.2",
            background=True,
            workspace_root=str(tmp_path),
        )
        snap_b = _snapshot(spawn_b)
        ids = {entry["job_id"] for entry in snap_b}
        assert _meta(spawn_a)["job_id"] in ids
        assert _meta(spawn_b)["job_id"] in ids

        # Wait for both to finish, then a status call should observe them
        # all as running=False with exit_codes set.
        for entry in snap_b:
            job = get_registry().get(entry["job_id"])
            invoke_tool.wait_for_job_exit(job, timeout=5)
        status_b = invoke_tool(
            bash_tool.bash_status,
            job_id=_meta(spawn_b)["job_id"],
            workspace_root=str(tmp_path),
        )
        snap_after = _snapshot(status_b)
        for entry in snap_after:
            assert entry["running"] is False
            assert entry["exit_code"] == 0

    def test_status_result_marked_hidden(self, invoke_tool, tmp_path):
        spawn = invoke_tool(
            bash_tool.execute_bash,
            command="echo done",
            background=True,
            workspace_root=str(tmp_path),
        )
        status = invoke_tool(
            bash_tool.bash_status,
            job_id=_meta(spawn)["job_id"],
            workspace_root=str(tmp_path),
        )
        assert status.ui_metadata.get("hidden") is True
        # Spawn results stay visible (panel needs the explicit confirmation).
        assert spawn.ui_metadata.get("hidden") is not True

    def test_returns_immediately_with_job_metadata(self, invoke_tool, tmp_path):
        start = time.monotonic()
        result = invoke_tool(
            bash_tool.execute_bash,
            command="sleep 2",
            background=True,
            workspace_root=str(tmp_path),
        )
        elapsed = time.monotonic() - start
        # The whole point: the call returns before the command finishes.
        assert elapsed < 1.5

        meta = _meta(result)
        assert meta["background"] is True
        assert meta["job_id"].startswith("bash-")
        assert meta["pid"] > 0
        assert meta["log_path"].startswith(str(tmp_path))
        assert os.path.exists(meta["log_path"])
        assert meta["cwd"] == str(tmp_path)

        # Registry should have it.
        job = get_registry().get(meta["job_id"])
        assert job is not None
        assert job.is_running()

    def test_log_file_under_workspace_root(self, invoke_tool, tmp_path):
        result = invoke_tool(
            bash_tool.execute_bash,
            command="echo logged-line",
            background=True,
            workspace_root=str(tmp_path),
        )
        log_path = _meta(result)["log_path"]
        assert _wait_until(lambda: os.path.getsize(log_path) > 0, timeout=3.0)
        with open(log_path, "rb") as f:
            assert b"logged-line" in f.read()

    def test_requires_workspace_root(self, invoke_tool):
        # The agents framework swallows tool exceptions and surfaces them as
        # an error string returned by on_invoke_tool. We just need to confirm
        # the missing workspace_root is reported and no job got registered.
        before = len(get_registry().list_all())
        result = invoke_tool(
            bash_tool.execute_bash,
            command="echo nope",
            background=True,
        )
        assert "workspace_root" in str(result).lower()
        assert len(get_registry().list_all()) == before


# ---------------------------------------------------------------------------
# bash_status
# ---------------------------------------------------------------------------


class TestBashStatus:
    def test_status_running_then_exited(self, invoke_tool, tmp_path):
        spawn = invoke_tool(
            bash_tool.execute_bash,
            command="sleep 0.4",
            background=True,
            workspace_root=str(tmp_path),
        )
        job_id = _meta(spawn)["job_id"]

        running = invoke_tool(
            bash_tool.bash_status,
            job_id=job_id,
            workspace_root=str(tmp_path),
        )
        assert _meta(running)["running"] is True
        assert _meta(running)["exit_code"] is None

        # Wait for the sleep to finish.
        job = get_registry().get(job_id)
        invoke_tool.wait_for_job_exit(job, timeout=5)

        finished = invoke_tool(
            bash_tool.bash_status,
            job_id=job_id,
            workspace_root=str(tmp_path),
        )
        assert _meta(finished)["running"] is False
        assert _meta(finished)["exit_code"] == 0

    def test_status_unknown_job(self, invoke_tool, tmp_path):
        result = invoke_tool(
            bash_tool.bash_status,
            job_id="bash-deadbeef",
            workspace_root=str(tmp_path),
        )
        assert _meta(result)["error_type"] == "unknown_job"

    def test_status_missing_argument(self, invoke_tool, tmp_path):
        result = invoke_tool(
            bash_tool.bash_status,
            workspace_root=str(tmp_path),
        )
        assert _meta(result)["error_type"] == "missing_argument"

    def test_status_list_all(self, invoke_tool, tmp_path):
        spawn1 = invoke_tool(
            bash_tool.execute_bash,
            command="echo a",
            background=True,
            workspace_root=str(tmp_path),
        )
        spawn2 = invoke_tool(
            bash_tool.execute_bash,
            command="echo b",
            background=True,
            workspace_root=str(tmp_path),
        )
        listing = invoke_tool(
            bash_tool.bash_status,
            list_all=True,
            workspace_root=str(tmp_path),
        )
        ids = {j["job_id"] for j in _meta(listing)["jobs"]}
        assert _meta(spawn1)["job_id"] in ids
        assert _meta(spawn2)["job_id"] in ids


# ---------------------------------------------------------------------------
# bash_logs
# ---------------------------------------------------------------------------


class TestBashLogs:
    def test_tail_returns_recent_lines(self, invoke_tool, tmp_path):
        spawn = invoke_tool(
            bash_tool.execute_bash,
            command="for i in $(seq 1 10); do echo line-$i; done",
            background=True,
            workspace_root=str(tmp_path),
        )
        job_id = _meta(spawn)["job_id"]
        invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)

        result = invoke_tool(
            bash_tool.bash_logs,
            job_id=job_id,
            lines=3,
            workspace_root=str(tmp_path),
        )
        assert _meta(result)["returned_lines"] == 3
        assert _meta(result)["total_lines"] == 10
        assert "line-10" in result.llm_output
        assert "line-1\n" not in result.ui_metadata["value"]

    def test_from_start(self, invoke_tool, tmp_path):
        spawn = invoke_tool(
            bash_tool.execute_bash,
            command="for i in $(seq 1 5); do echo line-$i; done",
            background=True,
            workspace_root=str(tmp_path),
        )
        job_id = _meta(spawn)["job_id"]
        invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)

        result = invoke_tool(
            bash_tool.bash_logs,
            job_id=job_id,
            lines=2,
            from_start=True,
            workspace_root=str(tmp_path),
        )
        body = result.ui_metadata["value"]
        assert body.splitlines() == ["line-1", "line-2"]
        assert _meta(result)["from_start"] is True

    def test_unknown_job(self, invoke_tool, tmp_path):
        result = invoke_tool(
            bash_tool.bash_logs,
            job_id="bash-missing",
            workspace_root=str(tmp_path),
        )
        assert _meta(result)["error_type"] == "unknown_job"


# ---------------------------------------------------------------------------
# bash_kill
# ---------------------------------------------------------------------------


class TestBashKill:
    def test_kill_terminates_running_job(self, invoke_tool, tmp_path):
        spawn = invoke_tool(
            bash_tool.execute_bash,
            command="sleep 30",
            background=True,
            workspace_root=str(tmp_path),
        )
        job_id = _meta(spawn)["job_id"]

        result = invoke_tool(
            bash_tool.bash_kill,
            job_id=job_id,
            grace_seconds=2,
            workspace_root=str(tmp_path),
        )
        meta = _meta(result)
        assert meta["signal_sent"] in {"SIGTERM", "SIGKILL"}
        assert meta["running"] is False
        # Process group SIGTERM sets returncode to a negative signal value
        # on POSIX; we just assert it's terminated, not the exact code.
        assert meta["exit_code"] is not None
        # Successful kill is a no-op for chat — the panel transition is the
        # user-visible signal. Hidden so we don't double-render.
        assert result.ui_metadata.get("hidden") is True

    def test_kill_already_exited(self, invoke_tool, tmp_path):
        spawn = invoke_tool(
            bash_tool.execute_bash,
            command="echo done",
            background=True,
            workspace_root=str(tmp_path),
        )
        job_id = _meta(spawn)["job_id"]
        invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)

        result = invoke_tool(
            bash_tool.bash_kill,
            job_id=job_id,
            workspace_root=str(tmp_path),
        )
        meta = _meta(result)
        assert meta["signal_sent"] == "none"
        assert meta["exit_code"] == 0
        # Same logic — already-exited is a no-op kill; panel already shows it.
        assert result.ui_metadata.get("hidden") is True

    def test_kill_unknown_job(self, invoke_tool, tmp_path):
        result = invoke_tool(
            bash_tool.bash_kill,
            job_id="bash-missing",
            workspace_root=str(tmp_path),
        )
        assert _meta(result)["error_type"] == "unknown_job"
        # Errors stay visible so the user/agent sees what went wrong.
        assert result.ui_metadata.get("hidden") is not True
