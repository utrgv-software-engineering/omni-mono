"""Tests for tools._bash_jobs.JobSweeper.

The sweeper detects natural job exits (a job finishes without an explicit
``bash_kill``) and fires a broadcast callback so docked panels in
connected clients refresh. The unit tests here exercise the polling loop
in isolation: a fast tick interval, fake broadcast that records calls,
and real ``subprocess.Popen`` jobs to drive the running→exited
transition.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import tools.bash_tool as bash_tool
from tools._bash_jobs import JobSweeper, get_registry


@pytest.fixture(autouse=True)
def _isolate_registry(invoke_tool):
    reg = get_registry()
    reg._jobs.clear()  # type: ignore[attr-defined]
    yield
    for job in list(reg._jobs.values()):  # type: ignore[attr-defined]
        if job.exit_code is None:
            try:
                invoke_tool.run(job.handle.terminate(force_after=0.5))
            except Exception:
                pass
        if job.watcher_task is not None and not job.watcher_task.done():
            job.watcher_task.cancel()
    reg._jobs.clear()  # type: ignore[attr-defined]


def _spawn_background(invoke_tool, tmp_path, command: str) -> str:
    spawn = invoke_tool(
        bash_tool.execute_bash,
        command=command,
        background=True,
        workspace_root=str(tmp_path),
    )
    return spawn.ui_metadata["metadata"]["job_id"]


class _Recorder:
    def __init__(self):
        # Each recorded call is (session_id, snapshot).
        self.calls = []

    async def __call__(self, session_id, snapshot):
        self.calls.append((session_id, snapshot))

    @property
    def snapshots(self):
        return [snap for (_sid, snap) in self.calls]


def test_sweeper_fires_broadcast_when_job_exits(invoke_tool, tmp_path):
    # Spawn outside the async scope — invoke_tool itself runs an event loop,
    # and nesting one inside our scenario() would explode at asyncio.run.
    job_id = _spawn_background(invoke_tool, tmp_path, "sleep 0.3")

    async def scenario():
        rec = _Recorder()
        sweeper = JobSweeper(rec, interval_s=0.05)
        sweeper.start()
        try:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if any(
                    not entry["running"] and entry["job_id"] == job_id
                    for snap in rec.snapshots
                    for entry in snap
                ):
                    break
                await asyncio.sleep(0.05)
        finally:
            sweeper.stop()
            await asyncio.sleep(0.1)

        assert rec.snapshots, "sweeper never broadcast"
        last = rec.snapshots[-1]
        entry = next(j for j in last if j["job_id"] == job_id)
        assert entry["running"] is False
        assert entry["exit_code"] == 0

    invoke_tool.run(scenario())


def test_sweeper_no_broadcast_when_steady_state(invoke_tool, tmp_path):
    """If nothing changes between ticks, no broadcast — even with running jobs."""
    _spawn_background(invoke_tool, tmp_path, "sleep 30")

    async def scenario():
        rec = _Recorder()
        sweeper = JobSweeper(rec, interval_s=0.05)
        sweeper.start()
        try:
            await asyncio.sleep(0.4)
        finally:
            sweeper.stop()
            await asyncio.sleep(0.1)

        # Sweeper seeds the fingerprint on first iteration; no transitions
        # follow because the job stays running. So zero broadcasts expected.
        assert rec.calls == []

    invoke_tool.run(scenario())


def test_sweeper_stop_cancels_task(invoke_tool, tmp_path):
    async def scenario():
        rec = _Recorder()
        sweeper = JobSweeper(rec, interval_s=0.05)
        sweeper.start()
        assert sweeper.running
        sweeper.stop()
        await asyncio.sleep(0.1)
        assert not sweeper.running

    invoke_tool.run(scenario())


def test_sweeper_broadcasts_per_session(invoke_tool, tmp_path):
    """Two jobs tagged for different sessions should each get their own
    targeted broadcast — never a single global blast that leaks job
    listings across session boundaries."""
    # Spawn two jobs; manually re-tag the second with a different session_id
    # to simulate two concurrent sessions sharing the same registry.
    job_a = _spawn_background(invoke_tool, tmp_path, "sleep 0.2")
    job_b = _spawn_background(invoke_tool, tmp_path, "sleep 0.2")
    get_registry().get(job_a).session_id = "sess-A"
    get_registry().get(job_b).session_id = "sess-B"

    async def scenario():
        rec = _Recorder()
        sweeper = JobSweeper(rec, interval_s=0.05)
        sweeper.start()
        try:
            await asyncio.sleep(1.0)
        finally:
            sweeper.stop()
            await asyncio.sleep(0.1)

        # Each session's broadcasts must contain only that session's job.
        sess_a_calls = [snap for (sid, snap) in rec.calls if sid == "sess-A"]
        sess_b_calls = [snap for (sid, snap) in rec.calls if sid == "sess-B"]
        assert sess_a_calls, "sess-A never received a broadcast"
        assert sess_b_calls, "sess-B never received a broadcast"
        for snap in sess_a_calls:
            ids = {j["job_id"] for j in snap}
            assert job_a in ids
            assert job_b not in ids, "sess-A leaked sess-B's job"
        for snap in sess_b_calls:
            ids = {j["job_id"] for j in snap}
            assert job_b in ids
            assert job_a not in ids, "sess-B leaked sess-A's job"

    invoke_tool.run(scenario())


def test_sweeper_idempotent_start(invoke_tool):
    async def scenario():
        rec = _Recorder()
        sweeper = JobSweeper(rec, interval_s=0.05)
        sweeper.start()
        first_task = sweeper._task
        sweeper.start()  # second call should be a no-op
        assert sweeper._task is first_task
        sweeper.stop()
        await asyncio.sleep(0.1)

    invoke_tool.run(scenario())


def test_sweeper_swallows_broadcast_exceptions(invoke_tool, tmp_path):
    """Broadcast failures must not kill the sweeper task."""

    class _Angry:
        def __init__(self):
            self.calls = 0

        async def __call__(self, session_id, snapshot):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("nope")

    job_id = _spawn_background(invoke_tool, tmp_path, "sleep 0.2")

    async def scenario():
        angry = _Angry()
        sweeper = JobSweeper(angry, interval_s=0.05)
        sweeper.start()
        try:
            # Wait long enough that the first transition + retry tick happen.
            await asyncio.sleep(0.8)
        finally:
            sweeper.stop()
            await asyncio.sleep(0.1)

        # Sweeper should have at least attempted the broadcast (call count > 0)
        # and still be operational (not crashed).
        assert angry.calls >= 1

    invoke_tool.run(scenario())
    # And the registry's view of the job is still consistent.
    job = get_registry().get(job_id)
    assert job is not None
    assert job.poll_returncode() == 0
