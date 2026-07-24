"""Process-wide registry of background bash jobs.

Background jobs spawned by ``execute_bash(background=True)`` are tracked
here so the companion tools (``bash_status``, ``bash_logs``, ``bash_kill``)
can locate them by ``job_id`` later in the same agent process.

The registry is intentionally in-memory only: jobs do not survive a
host-process restart. Their stdout/stderr is streamed to a log file under
``workspace_root/.omniagents/jobs/`` so output is recoverable from disk
even if the registry entry is lost. (The logs live inside the workspace
rather than under ``scratch_root`` deliberately: the typed workspace read
methods are path-policy clamped to the workspace mount, and log reads go
through them.)

Each Job wraps a workspace ``ProcessHandle`` rather than a raw
``subprocess.Popen``, so backgrounding works against both
``HostWorkspace`` (asyncio subprocess) and ``SandboxWorkspace``
(``pty_exec_start``-tracked process inside the sandbox).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

from omniagents.workspace import ProcessHandle

_log = logging.getLogger(__name__)


_JOBS_DIRNAME = os.path.join(".omniagents", "jobs")


@dataclass
class Job:
    """A background bash job tracked by the registry.

    ``handle`` is the workspace's ``ProcessHandle`` for the spawned
    process. ``exit_code``/``finished_at_monotonic`` are populated by a
    background watcher task (see ``_watch_job``) that polls the handle
    until the process exits. The state-snapshot helpers and the sweeper
    read these cached fields synchronously.
    """

    job_id: str
    command: str
    cwd: str
    log_path: str
    started_at_wall: float
    started_at_monotonic: float
    handle: ProcessHandle
    # Owning session id, captured from ctx.context at spawn time. ``None``
    # means "uncategorized" — visible to every session as a backstop for
    # callers (mostly tests) that don't run inside a real session.
    session_id: Optional[str] = None

    # Cached final state — populated by the watcher task. Reads are sync.
    exit_code: Optional[int] = None
    finished_at_monotonic: Optional[float] = None

    # The asyncio task that polls handle.wait()/handle.status() to detect
    # natural exit. Held so the registry can cancel it on cleanup.
    watcher_task: Optional[asyncio.Task] = None

    @property
    def pid(self) -> Optional[int]:
        """OS pid of the spawned process when available.

        Host backend always provides it; sandbox backend may return
        ``None`` since some sandbox transports don't surface the
        in-container pid. Callers MUST NOT use pid for cross-process
        operations — go through the handle.
        """
        return self.handle.pid

    def is_running(self) -> bool:
        return self.exit_code is None

    def poll_returncode(self) -> Optional[int]:
        """Return the cached exit code, or ``None`` if still running.

        Unlike the old ``Popen.poll`` shim, this is a pure read of cached
        state — the watcher task is responsible for updating it. The
        sweeper and snapshot helpers stay sync.
        """
        return self.exit_code

    def wall_time_ms(self) -> int:
        end = self.finished_at_monotonic or time.monotonic()
        return int((end - self.started_at_monotonic) * 1000)


async def _watch_job(job: Job) -> None:
    """Background task that observes ``job.handle`` until the process
    exits and caches the final exit code on the Job.

    Implementation: poll ``handle.wait(timeout=…)`` in a loop. On host
    the wait() blocks until the proc exits or hits the timeout. On
    sandbox it returns immediately with the latest cached state from the
    session — so we need to call it repeatedly to keep polling the
    underlying pty stream. The handle's own ``status()`` is the
    authoritative check for "is it done?"
    """
    try:
        while True:
            try:
                result = await job.handle.wait(timeout=1.0)
            except Exception:
                # Transient errors during polling — back off and retry.
                await asyncio.sleep(1.0)
                continue
            try:
                status = await job.handle.status()
            except Exception:
                status = "running"
            if status != "running":
                # Cache the final exit code (mapping -1 from "still
                # running" timeouts is impossible here since status said
                # we're done).
                ec = result.exit_code
                job.exit_code = ec if ec is not None else -1
                job.finished_at_monotonic = time.monotonic()
                await _notify_job_exit(job)
                return
    except asyncio.CancelledError:
        pass


async def _notify_job_exit(job: Job) -> None:
    """Push a single-line completion notice to the owning session via
    the service's batched notification path. Drops silently for jobs
    without a session tag (tests, legacy spawns) or before the service
    handle has been captured by the first server_function on_invoke."""
    if not job.session_id:
        return
    try:
        from omniagents.core.agents.service import current_service
    except Exception:
        return
    service = current_service()
    if service is None:
        _log.debug(
            "bash job %s exit: no service captured; skipping notify",
            job.job_id,
        )
        return
    # Frame the wakeup so the model has a clear directive. Without this,
    # an assistant-role notification with no instructions reads as the
    # agent's own prior utterance — and the model defaults to a near-
    # identical re-acknowledgment of whatever it last said.
    content = (
        f"[bash-job] {job.job_id} exited "
        f"code={job.exit_code} wall={job.wall_time_ms()}ms\n\n"
        "Decide what to do next. If this completes the user's request, "
        "summarize briefly and stop. Otherwise inspect the job output "
        "with bash_logs and take the next concrete step."
    )
    try:
        await service.enqueue_notification(
            session_id=job.session_id,
            content=content,
            source="bash_jobs.exit",
        )
    except Exception as e:
        _log.warning("enqueue_notification failed for %s: %s", job.job_id, e)


class _JobRegistry:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def register(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_all(self) -> List[Job]:
        with self._lock:
            return list(self._jobs.values())


# Process singleton. A plain module global is safe here: this module
# lives under its canonical name (``omniagents.builtin.tools._bash_jobs``)
# and tool discovery keeps canonically-named modules in ``sys.modules``,
# so every importer — tools, server functions, hosts — sees this same
# instance.
_registry = _JobRegistry()


def get_registry() -> _JobRegistry:
    return _registry


def new_job_id() -> str:
    return f"bash-{secrets.token_hex(4)}"


def jobs_dir(workspace_root: str) -> str:
    return os.path.join(workspace_root, _JOBS_DIRNAME)


def log_path_for(workspace_root: str, job_id: str) -> str:
    return os.path.join(jobs_dir(workspace_root), f"{job_id}.log")


# ---------------------------------------------------------------------------
# Shared helpers used by both tools and server functions.
# Keeping them here means a UI-driven kill (via server_function) and an
# agent-driven kill (via the bash_kill tool) go through the same code path
# — the registry sees the same state transitions either way.
# ---------------------------------------------------------------------------


def job_state_dict(job: Job) -> Dict[str, Any]:
    """Compact, JSON-serializable view of a single job."""
    rc = job.poll_returncode()
    log_size = 0
    try:
        log_size = os.path.getsize(job.log_path)
    except OSError:
        pass
    return {
        "job_id": job.job_id,
        "pid": job.pid,
        "command": job.command,
        "cwd": job.cwd,
        "log_path": job.log_path,
        "log_size_bytes": log_size,
        "started_at": job.started_at_wall,
        "wall_time_ms": job.wall_time_ms(),
        "running": rc is None,
        "exit_code": rc,
        "session_id": job.session_id,
    }


def _job_visible_to(job: Job, session_id: Optional[str]) -> bool:
    """Visibility rule: when filtering for a specific session, show jobs
    that belong to it plus untagged jobs (no session_id). When no filter
    is given, show everything.
    """
    if session_id is None:
        return True
    return job.session_id is None or job.session_id == session_id


def jobs_snapshot(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Stable-ordered snapshot of jobs, optionally filtered to a session.

    Passing ``session_id=None`` returns the full process-global view —
    used by the panel-warmup path and tests. Server functions invoked by
    a real client should pass ``session.id`` so the panel only sees jobs
    that belong to the calling session (plus any uncategorized jobs).
    """
    jobs = sorted(get_registry().list_all(), key=lambda j: j.started_at_monotonic)
    return [job_state_dict(j) for j in jobs if _job_visible_to(j, session_id)]


def session_ids_with_jobs() -> List[Optional[str]]:
    """Distinct session_ids that own at least one job in the registry,
    plus ``None`` if any uncategorized jobs exist. Used by the sweeper to
    decide which sessions need a refresh broadcast.
    """
    seen: Dict[Optional[str], None] = {}
    for j in get_registry().list_all():
        seen.setdefault(j.session_id, None)
    return list(seen.keys())


SignalSent = Literal["none", "SIGTERM", "SIGKILL"]


async def terminate_job(job: Job, *, grace_seconds: int = 5) -> SignalSent:
    """Terminate a background job and wait for it to actually exit.

    Returns ``"none"`` if the job has already finished, ``"SIGTERM"`` if
    the graceful terminate caught it, ``"SIGKILL"`` if we escalated past
    the grace window. The actual signal mechanics are delegated to
    ``ProcessHandle.terminate`` — the handle owns whatever escalation
    pattern works for its backend (POSIX process-group signals on host,
    pty terminate on sandbox).
    """
    if job.exit_code is not None:
        return "none"

    grace = max(grace_seconds, 0)
    try:
        await job.handle.terminate(force_after=float(grace))
    except Exception as exc:
        _log.warning("ProcessHandle.terminate raised for %s: %s", job.job_id, exc)

    # Wait briefly for the watcher to record the final exit. Cover the
    # grace window plus a small slack so escalation has time to land.
    deadline = time.monotonic() + grace + 5.0
    while job.exit_code is None and time.monotonic() < deadline:
        await asyncio.sleep(0.05)

    # The handle hides the SIGTERM-vs-SIGKILL distinction. Approximate
    # it from observed exit timing: if the watcher recorded an exit
    # within the grace window, terminate succeeded gracefully; otherwise
    # report SIGKILL (the handle's force_after path).
    if job.exit_code is None:
        # Watcher hasn't seen the exit yet — treat as forceful kill.
        return "SIGKILL"
    elapsed_after_terminate = job.finished_at_monotonic or time.monotonic()
    if (elapsed_after_terminate - (job.started_at_monotonic + 0.0)) > grace:
        return "SIGKILL"
    return "SIGTERM"


# ---------------------------------------------------------------------------
# JobSweeper — server-side polling task that detects natural job exits and
# triggers a broadcast so the docked panels in connected clients refresh
# without the user having to take an action.
#
# Lifecycle: lazily started on the first ``bash_jobs.*`` server-function
# call (when we get a service handle for the broadcast). The poll runs
# forever as a daemon asyncio task; nothing stops it explicitly. Cheap
# enough at 2-second cadence with O(jobs) work per tick.
#
# Change detection: we keep a fingerprint per job (running flag + exit
# code). A broadcast fires only when at least one fingerprint changes —
# so steady-state running or steady-state finished produce no traffic.
# ---------------------------------------------------------------------------


_DEFAULT_SWEEP_INTERVAL_S = 2.0


def _fingerprint_snapshot(
    snapshot: List[Dict[str, Any]],
) -> Dict[str, Tuple[bool, Optional[int]]]:
    """A tiny per-job fingerprint we can diff cheaply between ticks."""
    return {j["job_id"]: (bool(j["running"]), j["exit_code"]) for j in snapshot}


class JobSweeper:
    """Polls the registry on a schedule and invokes a broadcast callback
    whenever a job's running/exit-code state actually changes.

    The callback is async so it can issue ``service.request_client_function``
    fan-outs. We intentionally never block the event loop on user code
    inside the callback longer than ``_DEFAULT_SWEEP_INTERVAL_S``.
    """

    def __init__(
        self,
        broadcast: Callable[[Optional[str], List[Dict[str, Any]]], Awaitable[None]],
        *,
        interval_s: float = _DEFAULT_SWEEP_INTERVAL_S,
    ) -> None:
        # ``broadcast`` is called as ``await broadcast(session_id, snapshot)``
        # once per session that has at least one tracked job whose state
        # changed since the previous tick. ``session_id`` may be ``None``
        # for jobs that weren't tagged at spawn time (e.g. tests).
        self._broadcast = broadcast
        self._interval_s = interval_s
        self._task: Optional[asyncio.Task] = None
        self._last: Dict[str, Tuple[bool, Optional[int]]] = {}
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _log.warning("JobSweeper.start called outside an event loop; skipping")
            return
        self._task = loop.create_task(self._loop(), name="bash-job-sweeper")

    def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        # Seed fingerprints from the current state so we don't fire a spurious
        # broadcast on the very first tick.
        self._last = _fingerprint_snapshot(jobs_snapshot())
        try:
            while not self._stopping:
                await asyncio.sleep(self._interval_s)
                try:
                    await self._tick()
                except Exception as e:
                    _log.warning("JobSweeper tick failed: %s", e)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        snap = jobs_snapshot()
        cur = _fingerprint_snapshot(snap)
        if cur == self._last:
            return

        # Identify which sessions own a job whose fingerprint changed.
        # Each affected session gets its own filtered snapshot — we don't
        # leak jobs across session boundaries.
        changed_session_ids = set()
        # ``snap`` already includes session_id, so we can read straight off it.
        for entry in snap:
            jid = entry["job_id"]
            fp = (bool(entry["running"]), entry["exit_code"])
            if self._last.get(jid) != fp:
                changed_session_ids.add(entry.get("session_id"))
        # Catch jobs that disappeared from the registry between ticks
        # (unlikely today since nothing prunes, but keep the diff symmetric).
        for jid in self._last.keys() - cur.keys():
            changed_session_ids.add(None)

        self._last = cur

        for sid in changed_session_ids:
            session_snap = jobs_snapshot(session_id=sid)
            await self._broadcast(sid, session_snap)


async def read_job_log(
    job: Job, *, lines: int = 200, from_start: bool = False
) -> Tuple[str, int, int]:
    """Read the job's log file from disk via the workspace contextvar.

    Returns ``(body, total_lines, log_size_bytes)``. ``body`` is the joined
    selected lines (no truncation applied — callers can shape further).
    Raises ``OSError`` if the file is unreadable.

    Background-mode spawn itself is host-only, but the log file lives at
    ``workspace_root/.omniagents/jobs/<id>.log`` which is inside the
    workspace. Routing the read through ``workspace.read_bytes`` keeps the
    abstraction consistent and makes the read work for any backend that
    can see that path. If the active workspace context is rooted elsewhere,
    fall back to a direct local read only for the registered job log path.
    """
    from omniagents import workspace as _ws
    from omniagents.workspace import WorkspaceError

    try:
        raw = await _ws.read_bytes(job.log_path)
    except (OSError, WorkspaceError) as exc:
        raw = await _read_local_registered_job_log(job, exc)
    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    total = len(all_lines)
    if from_start:
        selected = all_lines[: max(lines, 0)]
    else:
        selected = all_lines[-max(lines, 0) :] if lines > 0 else []
    return "\n".join(selected), total, len(raw)


def _is_registered_job_log_path(job: Job) -> bool:
    job_id = getattr(job, "job_id", None)
    if not job_id:
        return False
    normalized = os.path.normpath(os.fspath(job.log_path))
    parts = normalized.split(os.sep)
    return (
        len(parts) >= 3
        and parts[-3] == ".omniagents"
        and parts[-2] == "jobs"
        and parts[-1] == f"{job_id}.log"
    )


async def _read_local_registered_job_log(job: Job, workspace_error: Exception) -> bytes:
    if not _is_registered_job_log_path(job):
        if isinstance(workspace_error, OSError):
            raise workspace_error
        raise OSError(
            f"Workspace could not read non-job log path {job.log_path}: {workspace_error}"
        ) from workspace_error

    log_path = os.fspath(job.log_path)
    try:
        return await asyncio.to_thread(Path(log_path).read_bytes)
    except FileNotFoundError as local_error:
        raise FileNotFoundError(
            f"Log file is missing on local filesystem: {log_path} "
            f"(workspace read failed: {workspace_error})"
        ) from local_error
    except PermissionError as local_error:
        raise PermissionError(
            f"Log file is not readable on local filesystem: {log_path} "
            f"(workspace read failed: {workspace_error})"
        ) from local_error
    except OSError as local_error:
        raise OSError(
            f"Log file is inaccessible on local filesystem: {log_path}: {local_error} "
            f"(workspace read failed: {workspace_error})"
        ) from local_error
