"""Tests for the bash_jobs.* server functions.

These functions wrap the same registry helpers the bash_kill / bash_logs
tools use, so the goal here is mostly to confirm the server-function
shape (always returns a fresh ``snapshot``, ``ok`` flag, sane error
fields for unknown jobs).
"""

from __future__ import annotations

import asyncio
import time

import omniagents.builtin.tools.bash_tool as bash_tool
import pytest
from omniagents.builtin.server_functions import bash_jobs as bj
from omniagents.builtin.tools._bash_jobs import get_registry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# bash_jobs.list
# ---------------------------------------------------------------------------


def test_list_empty():
    res = asyncio.run(bj.bash_jobs_list())
    assert res == {"snapshot": []}


def test_list_returns_known_jobs(invoke_tool, tmp_path):
    job_id = _spawn_background(invoke_tool, tmp_path, "echo hi")
    res = invoke_tool.run(bj.bash_jobs_list())
    ids = {j["job_id"] for j in res["snapshot"]}
    assert job_id in ids


# ---------------------------------------------------------------------------
# bash_jobs.kill
# ---------------------------------------------------------------------------


def test_kill_running_job(invoke_tool, tmp_path):
    job_id = _spawn_background(invoke_tool, tmp_path, "sleep 30")
    res = invoke_tool.run(bj.bash_jobs_kill(job_id=job_id, grace_seconds=2))
    assert res["ok"] is True
    assert res["signal_sent"] in {"SIGTERM", "SIGKILL"}
    assert res["job"]["running"] is False
    # Snapshot reflects the new state too.
    snap_entry = next(j for j in res["snapshot"] if j["job_id"] == job_id)
    assert snap_entry["running"] is False


def test_kill_already_exited_job(invoke_tool, tmp_path):
    job_id = _spawn_background(invoke_tool, tmp_path, "echo done")
    invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)
    res = invoke_tool.run(bj.bash_jobs_kill(job_id=job_id))
    assert res["ok"] is True
    assert res["signal_sent"] == "none"
    assert res["job"]["exit_code"] == 0


def test_kill_unknown_job():
    res = asyncio.run(bj.bash_jobs_kill(job_id="bash-deadbeef"))
    assert res["ok"] is False
    assert res["error"] == "unknown_job"
    assert res["snapshot"] == []


# ---------------------------------------------------------------------------
# bash_jobs.tail
# ---------------------------------------------------------------------------


def test_tail_returns_recent_log_lines(invoke_tool, tmp_path):
    job_id = _spawn_background(
        invoke_tool, tmp_path, "for i in $(seq 1 10); do echo line-$i; done"
    )
    invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)
    res = invoke_tool.run(bj.bash_jobs_tail(job_id=job_id, lines=3))
    assert res["ok"] is True
    assert res["total_lines"] == 10
    body_lines = res["text"].splitlines()
    assert len(body_lines) == 3
    assert body_lines[-1] == "line-10"


def test_tail_from_start(invoke_tool, tmp_path):
    job_id = _spawn_background(
        invoke_tool, tmp_path, "for i in $(seq 1 5); do echo line-$i; done"
    )
    invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)
    res = invoke_tool.run(bj.bash_jobs_tail(job_id=job_id, lines=2, from_start=True))
    assert res["ok"] is True
    assert res["text"].splitlines() == ["line-1", "line-2"]
    assert res["from_start"] is True


def test_tail_unknown_job():
    res = asyncio.run(bj.bash_jobs_tail(job_id="bash-missing"))
    assert res["ok"] is False
    assert res["error"] == "unknown_job"


def test_tail_clamps_to_max_lines(invoke_tool, tmp_path, monkeypatch):
    """Sanity: silly-large ``lines`` doesn't blow up — clamped to MAX."""
    monkeypatch.setattr(bj, "_BASH_TAIL_MAX_LINES", 5)
    job_id = _spawn_background(
        invoke_tool, tmp_path, "for i in $(seq 1 20); do echo line-$i; done"
    )
    invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)
    res = invoke_tool.run(bj.bash_jobs_tail(job_id=job_id, lines=999_999))
    assert res["ok"] is True
    assert len(res["text"].splitlines()) == 5


# ---------------------------------------------------------------------------
# Broadcast on UI-driven kill (multi-tab / multi-frontend sync)
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, sid="sess-test"):
        self.id = sid


class _FakeService:
    def __init__(self):
        self.calls = []
        # Mimic the real SessionManager._by_id structure so _ensure_sweeper's
        # _all_sessions helper finds something to fan out to.
        self._session_manager = type("M", (), {"_by_id": {}})()

    async def request_client_function(
        self, function, args=None, *, session=None, expect_result=False
    ):
        self.calls.append(
            {
                "function": function,
                "args": args,
                "session": session,
                "expect_result": expect_result,
            }
        )
        return True


def test_kill_invoke_handler_broadcasts_snapshot(invoke_tool, tmp_path):
    """The custom on_invoke handler for bash_jobs.kill should broadcast
    ui.bash_jobs.update so other connected clients (other web tabs, Ink)
    refresh their docked panel."""
    job_id = _spawn_background(invoke_tool, tmp_path, "sleep 30")
    service = _FakeService()
    sess = _FakeSession()
    handler = getattr(bj.bash_jobs_kill, "_server_function_on_invoke")

    res = invoke_tool.run(
        handler(service, sess, {"job_id": job_id, "grace_seconds": 1})
    )

    assert res["ok"] is True
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["function"] == "ui.bash_jobs.update"
    snap = call["args"]["snapshot"]
    assert any(j["job_id"] == job_id and j["running"] is False for j in snap)


def test_kill_invoke_handler_unknown_job_still_broadcasts():
    """An unknown-job kill returns ok=False but still broadcasts the
    (empty) snapshot — so a stale UI gets corrected."""
    service = _FakeService()
    sess = _FakeSession()
    handler = getattr(bj.bash_jobs_kill, "_server_function_on_invoke")
    res = asyncio.run(handler(service, sess, {"job_id": "bash-missing"}))
    assert res["ok"] is False
    assert len(service.calls) == 1
    assert service.calls[0]["function"] == "ui.bash_jobs.update"


def test_broadcast_failure_is_swallowed(invoke_tool, tmp_path):
    """If the broadcast raises, the kill still succeeds (best-effort UI)."""

    class _AngryService:
        async def request_client_function(self, *a, **kw):
            raise RuntimeError("ws disconnected")

    job_id = _spawn_background(invoke_tool, tmp_path, "sleep 10")
    handler = getattr(bj.bash_jobs_kill, "_server_function_on_invoke")
    res = invoke_tool.run(
        handler(_AngryService(), _FakeSession(), {"job_id": job_id, "grace_seconds": 1})
    )
    # Broadcast failed but the kill went through.
    assert res["ok"] is True
    assert res["job"]["running"] is False


# ---------------------------------------------------------------------------
# Ink slash-command wrappers (bj-list / bj-kill / bj-logs)
# ---------------------------------------------------------------------------


def test_bj_list_empty():
    res = asyncio.run(bj.bj_list())
    assert res["message"].lower().startswith("no background")


def test_bj_list_running_and_exited(invoke_tool, tmp_path):
    a = _spawn_background(invoke_tool, tmp_path, "echo a")
    invoke_tool.wait_for_job_exit(get_registry().get(a), timeout=5)
    b = _spawn_background(invoke_tool, tmp_path, "sleep 30")
    res = invoke_tool.run(bj.bj_list())
    assert "1 running" in res["message"]
    assert "1 exited" in res["message"]
    assert a in res["message"]
    assert b in res["message"]


def test_bj_kill_no_arg():
    res = asyncio.run(bj.bj_kill(text=""))
    assert "Usage" in res["message"]


def test_bj_kill_running_job(invoke_tool, tmp_path):
    job_id = _spawn_background(invoke_tool, tmp_path, "sleep 30")
    res = invoke_tool.run(
        bj.bj_kill(text=f"  {job_id}  ")
    )  # Leading/trailing whitespace
    assert res["message"].startswith(f"Killed {job_id}")
    assert res["job"]["running"] is False


def test_bj_kill_unknown_job():
    res = asyncio.run(bj.bj_kill(text="bash-missing"))
    assert res["ok"] is False
    assert "No such job" in res["message"]


def test_bj_kill_invoke_handler_broadcasts(invoke_tool, tmp_path):
    """The slash form should also broadcast — so /bj-kill from one Ink
    instance refreshes every other connected client too."""
    job_id = _spawn_background(invoke_tool, tmp_path, "sleep 30")
    service = _FakeService()
    handler = getattr(bj.bj_kill, "_server_function_on_invoke")
    res = invoke_tool.run(handler(service, _FakeSession(), {"text": job_id}))
    assert res["job"]["running"] is False
    assert any(c["function"] == "ui.bash_jobs.update" for c in service.calls)


def test_bj_logs_no_arg():
    res = asyncio.run(bj.bj_logs(text=""))
    assert "Usage" in res["message"]


def test_bj_logs_returns_tail(invoke_tool, tmp_path):
    job_id = _spawn_background(
        invoke_tool, tmp_path, "for i in $(seq 1 5); do echo line-$i; done"
    )
    invoke_tool.wait_for_job_exit(get_registry().get(job_id), timeout=5)
    res = invoke_tool.run(bj.bj_logs(text=job_id))
    assert "exited(0)" in res["message"]
    assert "line-5" in res["message"]


def test_bj_logs_unknown_job():
    res = asyncio.run(bj.bj_logs(text="bash-missing"))
    assert res["ok"] is False
    assert "Could not read logs" in res["message"]
