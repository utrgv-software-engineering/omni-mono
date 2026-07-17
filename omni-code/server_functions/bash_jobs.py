"""Server functions for managing background bash jobs from the UI.

These exist so the user can cancel a job (or peek at its logs) without
asking the agent to run ``bash_kill``. They share a process with the bash
tools, so they call the same registry helpers in ``tools/_bash_jobs.py``
— a UI-driven kill is indistinguishable from an agent-driven one as far
as the registry is concerned.

Each function returns ``snapshot``: the fresh, full job snapshot. State-
changing functions (``bash_jobs.kill``, ``bj-kill``) additionally
broadcast the new snapshot to every channel on the session via the
``ui.bash_jobs.update`` client_request, so a kill from one tab refreshes
the panel in any other tab and in the Ink TUI.

Naming:
- ``bash_jobs.list`` / ``bash_jobs.kill`` / ``bash_jobs.tail``: canonical
  API used by the Web UI buttons.
- ``bj-list`` / ``bj-kill`` / ``bj-logs``: Ink-friendly slash-command
  aliases that accept a single positional ``text`` argument and return a
  human-readable ``message`` field.
"""

from __future__ import annotations

import logging
from typing import Optional

from omniagents import server_function

# The background-job machinery lives in the omniagents builtin tools;
# importing the same module the ``execute_bash`` tool uses guarantees the
# UI panel and the agent share one job registry.
from omniagents.builtin.tools._bash_jobs import (  # noqa: E402
    JobSweeper,
    get_registry,
    job_state_dict,
    jobs_snapshot,
    read_job_log,
    session_ids_with_jobs,
    terminate_job,
)


def _job_visible_to_session(job, session_id: Optional[str]) -> bool:
    """Visibility rule shared by every kill/tail/list path: a session sees
    its own jobs plus any jobs that lack a session tag (test fixtures,
    legacy spawns). Refusing to find a job that exists but belongs to
    another session looks the same to callers as a missing job, which is
    intentional — they have no business knowing it exists.
    """
    if session_id is None:
        return True
    return job.session_id is None or job.session_id == session_id


_log = logging.getLogger(__name__)


_BASH_TAIL_DEFAULT_LINES = 200
_BASH_TAIL_MAX_LINES = 5000
_BASH_KILL_DEFAULT_GRACE = 5


# ---------------------------------------------------------------------------
# Broadcast helper — used by every state-changing path so the docked panels
# in all connected clients (Web tabs, Ink TUI) refresh together.
# ---------------------------------------------------------------------------


async def _broadcast_snapshot(service, session) -> None:
    if service is None or session is None:
        return
    try:
        await service.request_client_function(
            "ui.bash_jobs.update",
            {"snapshot": jobs_snapshot(session_id=getattr(session, "id", None))},
            session=session,
            expect_result=False,
        )
    except Exception as e:
        # Broadcast is best-effort. The state change has already been
        # applied to the registry; the worst case is the panel is briefly
        # stale until the next tool_result lands.
        _log.warning("ui.bash_jobs.update broadcast failed: %s", e)


# ---------------------------------------------------------------------------
# Lazy-start sweeper — see _bash_jobs.JobSweeper. Hooked into every
# server-function on_invoke handler so the first call from any UI captures
# a service handle, after which natural exits (jobs that finish without an
# explicit kill) broadcast on their own. The sweeper is single-instance
# per process and never explicitly stopped — it's a cheap daemon task.
# ---------------------------------------------------------------------------


_sweeper: Optional[JobSweeper] = None


def _all_sessions(service) -> list:
    """Best-effort enumeration of live sessions on the running service.

    Falls back to ``[None]`` so a broadcast at least targets the current
    channel if the service is unavailable.
    """
    try:
        sessions = service.active_sessions()
        return sessions if sessions else [None]
    except Exception:
        return [None]


def _ensure_sweeper(service) -> None:
    """Start the global sweeper on first call. Subsequent calls are
    no-ops (the sweeper resolves the live service via
    ``omniagents.core.agents.service.current_service`` on each tick)."""
    global _sweeper

    async def _broadcast_to_session(session_id: Optional[str], snapshot):
        # The sweeper hands us the (session_id, filtered_snapshot) pair for
        # exactly one affected session at a time. Find the live Session
        # object so request_client_function can route to its channels.
        # Untagged jobs (session_id=None) fan out to every live session as
        # a backstop — this only happens for tests / legacy spawns, not
        # real per-session usage.
        sessions = _all_sessions(service)
        if session_id is not None:
            sessions = [s for s in sessions if getattr(s, "id", None) == session_id]
            if not sessions:
                # Session went away (closed tab, GC); nothing to do.
                return
        for sess in sessions:
            try:
                await service.request_client_function(
                    "ui.bash_jobs.update",
                    {"snapshot": snapshot},
                    session=sess,
                    expect_result=False,
                )
            except Exception as e:
                _log.warning(
                    "sweeper broadcast to session=%s failed: %s",
                    getattr(sess, "id", None),
                    e,
                )

    if _sweeper is None:
        _sweeper = JobSweeper(_broadcast_to_session)
    if not _sweeper.running:
        _sweeper.start()


@server_function(
    description="List all background bash jobs known to this process.",
    strict=True,
    name_override="bash_jobs.list",
)
async def bash_jobs_list(_session_id: Optional[str] = None) -> dict:
    return {"snapshot": jobs_snapshot(session_id=_session_id)}


@server_function(
    description=(
        "Terminate a background bash job (SIGTERM, escalating to SIGKILL after "
        "the grace period). Idempotent: returns the current state for jobs that "
        "have already exited."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
            "grace_seconds": {"type": ["integer", "null"], "minimum": 0},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
    strict=True,
    name_override="bash_jobs.kill",
)
async def bash_jobs_kill(
    job_id: str,
    grace_seconds: Optional[int] = None,
    _session_id: Optional[str] = None,
) -> dict:
    grace = _BASH_KILL_DEFAULT_GRACE if grace_seconds is None else int(grace_seconds)

    job = get_registry().get(job_id)
    if job is None or not _job_visible_to_session(job, _session_id):
        return {
            "ok": False,
            "error": "unknown_job",
            "job_id": job_id,
            "snapshot": jobs_snapshot(session_id=_session_id),
        }

    signal_sent = await terminate_job(job, grace_seconds=grace)
    state = job_state_dict(job)
    state["signal_sent"] = signal_sent
    return {
        "ok": True,
        "job": state,
        "signal_sent": signal_sent,
        "snapshot": jobs_snapshot(session_id=_session_id),
    }


async def _bash_jobs_kill_on_invoke(service, session, args=None):
    """Run the kill, then broadcast ``ui.bash_jobs.update`` so every
    connected client refreshes its docked panel."""
    _ensure_sweeper(service)
    args = args or {}
    job_id = args.get("job_id")
    grace_seconds = args.get("grace_seconds")
    sid = getattr(session, "id", None)
    result = await bash_jobs_kill(
        job_id=job_id, grace_seconds=grace_seconds, _session_id=sid
    )
    await _broadcast_snapshot(service, session)
    return result


setattr(bash_jobs_kill, "_server_function_on_invoke", _bash_jobs_kill_on_invoke)


async def _bash_jobs_list_on_invoke(service, session, args=None):
    """Read-only, but kicks the sweeper so subsequent natural exits broadcast."""
    _ensure_sweeper(service)
    return await bash_jobs_list(_session_id=getattr(session, "id", None))


setattr(bash_jobs_list, "_server_function_on_invoke", _bash_jobs_list_on_invoke)


@server_function(
    description=(
        "Read recent log output (combined stdout/stderr) from a background bash "
        "job's log file. Returns the tail by default; pass from_start=true for "
        "the head."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
            "lines": {"type": ["integer", "null"], "minimum": 0},
            "from_start": {"type": ["boolean", "null"]},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
    strict=True,
    name_override="bash_jobs.tail",
)
async def bash_jobs_tail(
    job_id: str,
    lines: Optional[int] = None,
    from_start: Optional[bool] = None,
    _session_id: Optional[str] = None,
) -> dict:
    n = (
        _BASH_TAIL_DEFAULT_LINES
        if lines is None
        else min(int(lines), _BASH_TAIL_MAX_LINES)
    )
    head = bool(from_start)

    job = get_registry().get(job_id)
    if job is None or not _job_visible_to_session(job, _session_id):
        return {
            "ok": False,
            "error": "unknown_job",
            "job_id": job_id,
            "snapshot": jobs_snapshot(session_id=_session_id),
        }

    try:
        body, total_lines, log_size_bytes = await read_job_log(
            job, lines=n, from_start=head
        )
    except OSError as e:
        return {
            "ok": False,
            "error": "log_read_error",
            "job_id": job_id,
            "message": str(e),
            "snapshot": jobs_snapshot(session_id=_session_id),
        }

    state = job_state_dict(job)
    return {
        "ok": True,
        "job": state,
        "text": body,
        "total_lines": total_lines,
        "log_size_bytes": log_size_bytes,
        "from_start": head,
        "snapshot": jobs_snapshot(session_id=_session_id),
    }


async def _bash_jobs_tail_on_invoke(service, session, args=None):
    _ensure_sweeper(service)
    args = args or {}
    return await bash_jobs_tail(
        job_id=args.get("job_id"),
        lines=args.get("lines"),
        from_start=args.get("from_start"),
        _session_id=getattr(session, "id", None),
    )


setattr(bash_jobs_tail, "_server_function_on_invoke", _bash_jobs_tail_on_invoke)


# ---------------------------------------------------------------------------
# Ink slash-command aliases — Ink's slash handler turns a single positional
# argument into ``{"text": "..."}``. These wrappers accept that shape and
# return a human-readable ``message`` so the slash result reads naturally
# in the chat log. Web users have buttons; Ink users have these.
# ---------------------------------------------------------------------------


def _format_elapsed(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rem = int(seconds - minutes * 60)
    return f"{minutes}m{rem:02d}s"


def _format_job_line(j: dict) -> str:
    state = "running" if j["running"] else f"exit {j['exit_code']}"
    return f"  {j['job_id']}  {state:>9}  {_format_elapsed(j['wall_time_ms']):>7}  {j['command']}"


@server_function(
    description="List background bash jobs (Ink slash form: /bj-list).",
    strict=True,
    name_override="bj-list",
)
async def bj_list(_session_id: Optional[str] = None) -> dict:
    snap = jobs_snapshot(session_id=_session_id)
    if not snap:
        return {"message": "No background bash jobs in this process."}
    running = [j for j in snap if j["running"]]
    exited = [j for j in snap if not j["running"]]
    lines = [
        f"Bash jobs: {len(running)} running, {len(exited)} exited",
        *(_format_job_line(j) for j in snap),
    ]
    return {"message": "\n".join(lines), "snapshot": snap}


@server_function(
    description=(
        "Kill a background bash job by id (Ink slash form: /bj-kill <job_id>)."
    ),
    strict=True,
    name_override="bj-kill",
)
async def bj_kill(
    text: Optional[str] = None, _session_id: Optional[str] = None
) -> dict:
    job_id = (text or "").strip()
    if not job_id:
        return {"message": "Usage: /bj-kill <job_id>"}
    res = await bash_jobs_kill(job_id=job_id, _session_id=_session_id)
    if not res.get("ok"):
        return {
            "message": f"No such job: {job_id}",
            "ok": False,
            "snapshot": res.get("snapshot", []),
        }
    job = res["job"]
    sig = res.get("signal_sent", "none")
    if sig == "none":
        msg = f"{job_id} already exited (code {job['exit_code']})."
    else:
        msg = f"Killed {job_id} ({sig}, exit {job['exit_code']})."
    return {"message": msg, **res}


async def _bj_kill_on_invoke(service, session, args=None):
    _ensure_sweeper(service)
    args = args or {}
    text = args.get("text") or args.get("job_id")
    result = await bj_kill(text=text, _session_id=getattr(session, "id", None))
    await _broadcast_snapshot(service, session)
    return result


setattr(bj_kill, "_server_function_on_invoke", _bj_kill_on_invoke)


async def _bj_list_on_invoke(service, session, args=None):
    _ensure_sweeper(service)
    return await bj_list(_session_id=getattr(session, "id", None))


setattr(bj_list, "_server_function_on_invoke", _bj_list_on_invoke)


@server_function(
    description=(
        "Tail logs from a background bash job (Ink slash form: /bj-logs <job_id>)."
    ),
    strict=True,
    name_override="bj-logs",
)
async def bj_logs(
    text: Optional[str] = None, _session_id: Optional[str] = None
) -> dict:
    job_id = (text or "").strip()
    if not job_id:
        return {"message": "Usage: /bj-logs <job_id>"}
    res = await bash_jobs_tail(
        job_id=job_id, lines=_BASH_TAIL_DEFAULT_LINES, _session_id=_session_id
    )
    if not res.get("ok"):
        return {
            "message": f"Could not read logs for {job_id}: {res.get('error', 'unknown error')}",
            "ok": False,
        }
    job = res["job"]
    state = "running" if job["running"] else f"exited({job['exit_code']})"
    body = res.get("text") or "(no log output)"
    header = (
        f"{job_id}: {state} · {_format_elapsed(job['wall_time_ms'])} · "
        f"{res['total_lines']} total log lines"
    )
    return {"message": f"{header}\n\n{body}", **res}


async def _bj_logs_on_invoke(service, session, args=None):
    _ensure_sweeper(service)
    args = args or {}
    return await bj_logs(
        text=args.get("text"), _session_id=getattr(session, "id", None)
    )


setattr(bj_logs, "_server_function_on_invoke", _bj_logs_on_invoke)
