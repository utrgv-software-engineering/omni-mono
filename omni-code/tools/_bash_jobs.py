"""Process-wide registry of background bash jobs.

Background jobs spawned by ``execute_bash(background=True)`` are tracked
here so the companion tools (``bash_status``, ``bash_logs``, ``bash_kill``)
can locate them by ``job_id`` later in the same agent process.

The registry is intentionally in-memory only: jobs do not survive a
host-process restart. Their stdout/stderr is streamed to a log file under
``workspace_root/.omni-code/jobs/`` so output is recoverable from disk
even if the registry entry is lost.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple


_log = logging.getLogger(__name__)


_JOBS_DIRNAME = os.path.join(".omni-code", "jobs")


@dataclass
class Job:
    job_id: str
    pid: int
    command: str
    cwd: str
    log_path: str
    started_at_wall: float
    started_at_monotonic: float
    popen: subprocess.Popen
    log_file_handle: Optional["object"] = None  # file handle held until process exits
    # Owning session id, captured from ctx.context at spawn time. ``None``
    # means "uncategorized" — visible to every session as a backstop for
    # callers (mostly tests) that don't run inside a real session.
    session_id: Optional[str] = None

    # Cached final state — populated lazily by ``poll_returncode``.
    exit_code: Optional[int] = None
    finished_at_monotonic: Optional[float] = None

    def is_running(self) -> bool:
        return self.poll_returncode() is None

    def poll_returncode(self) -> Optional[int]:
        """Return exit code if the process has finished, else ``None``.

        Caches the finish time on the first poll that observes termination
        so wall_time stays stable across subsequent calls.
        """
        if self.exit_code is not None:
            return self.exit_code
        rc = self.popen.poll()
        if rc is None:
            return None
        self.exit_code = rc
        self.finished_at_monotonic = time.monotonic()
        # Release the log file handle now that the child can no longer write.
        if self.log_file_handle is not None:
            try:
                self.log_file_handle.close()
            except Exception:
                pass
            self.log_file_handle = None
        return rc

    def wall_time_ms(self) -> int:
        end = self.finished_at_monotonic or time.monotonic()
        return int((end - self.started_at_monotonic) * 1000)


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


# Stash the singleton on ``sys`` rather than as a module global so it
# survives ``omniagents.core.tools.discovery.discover_functions_in_dir``'s
# sys.modules cleanup. Discovery loads every .py in tools/ and then
# deletes those modules from sys.modules afterward — and later
# ``server_functions/bash_jobs.py`` does ``from tools._bash_jobs import …``,
# which re-imports the module fresh. With a module-global, that yielded
# a SECOND ``_JobRegistry`` — the sweeper polled it (empty) while
# ``execute_bash`` registered jobs in the first one, and the UI panel
# stayed on "running" forever.
_REGISTRY_ATTR = "__omni_code_bash_job_registry__"


def get_registry() -> _JobRegistry:
    reg = getattr(sys.modules["sys"], _REGISTRY_ATTR, None)
    if reg is None:
        reg = _JobRegistry()
        setattr(sys.modules["sys"], _REGISTRY_ATTR, reg)
    return reg


# Alias kept for any in-process callers that grabbed the symbol when the
# registry was a module global. The lookup goes through ``get_registry``
# so reads always resolve to the live singleton.
class _RegistryProxy:
    def __getattr__(self, name):
        return getattr(get_registry(), name)


_registry = _RegistryProxy()


def new_job_id() -> str:
    return f"bash-{secrets.token_hex(4)}"


def jobs_dir(workspace_root: str) -> str:
    path = os.path.join(workspace_root, _JOBS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


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


def terminate_job(job: Job, *, grace_seconds: int = 5) -> SignalSent:
    """Send SIGTERM, wait up to ``grace_seconds``, escalate to SIGKILL.

    No-op (returns ``"none"``) if the job has already finished. Signals the
    full session group on POSIX so shell-spawned children also die, falling
    back to ``Popen.terminate/kill`` if the pgid lookup fails or on Windows.
    """
    if job.poll_returncode() is not None:
        return "none"

    signal_sent: SignalSent = "SIGTERM"
    if os.name != "nt":
        try:
            pgid = os.getpgid(job.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            job.popen.terminate()
    else:
        job.popen.terminate()

    try:
        job.popen.wait(timeout=max(grace_seconds, 0))
    except subprocess.TimeoutExpired:
        signal_sent = "SIGKILL"
        if os.name != "nt":
            try:
                pgid = os.getpgid(job.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                job.popen.kill()
        else:
            job.popen.kill()
        try:
            job.popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    job.poll_returncode()  # refresh cached state after wait()
    return signal_sent


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


def _fingerprint_snapshot(snapshot: List[Dict[str, Any]]) -> Dict[str, Tuple[bool, Optional[int]]]:
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


def read_job_log(job: Job, *, lines: int = 200, from_start: bool = False) -> Tuple[str, int, int]:
    """Read the job's log file from disk.

    Returns ``(body, total_lines, log_size_bytes)``. ``body`` is the joined
    selected lines (no truncation applied — callers can shape further).
    Raises ``OSError`` if the file is unreadable.
    """
    with open(job.log_path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    total = len(all_lines)
    if from_start:
        selected = all_lines[: max(lines, 0)]
    else:
        selected = all_lines[-max(lines, 0):] if lines > 0 else []
    return "\n".join(selected), total, len(raw)
