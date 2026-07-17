"""Self-scheduled wakeup tools.

A pair of tools — ``schedule_wakeup`` and ``cancel_wakeup`` — that let
the agent install a recurring (or one-shot) notification on its own
session. Use case: "keep an eye on this every few hours" — the agent
schedules a wakeup, ends its turn, and is woken on the cadence until
the bound expires or it calls ``cancel_wakeup``.

State + tick loop live in ``server_functions.wakeup``; these tools are
thin wrappers that find the service + session at call time and delegate
to ``schedule_impl`` / ``cancel_impl``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput

from tools.bash_tool import _session_id_from_ctx


_log = logging.getLogger(__name__)


def _parse_end_at(end_at: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    """Parse an ISO-8601 string into a unix timestamp.

    Returns ``(unix_ts, None)`` on success or ``(None, error_message)`` on
    failure. ``None`` input yields ``(None, None)`` (no bound, no error).
    Naive datetimes are interpreted in the local timezone — matches what
    users typically type in chat ("9am tomorrow") more closely than UTC
    would.
    """
    if end_at is None:
        return None, None
    if not isinstance(end_at, str) or not end_at.strip():
        return None, "end_at must be an ISO-8601 timestamp string."
    raw = end_at.strip()
    # ``fromisoformat`` accepts "...Z" suffix from Python 3.11+. Strip it
    # explicitly to support earlier Python versions and to be defensive.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        return None, f"end_at parse failed: {e}"
    if dt.tzinfo is None:
        dt = dt.astimezone()  # attach local tz
    return dt.timestamp(), None


def _get_service():
    try:
        from omniagents.core.agents.service import current_service
    except Exception:
        return None
    try:
        return current_service()
    except Exception:
        return None


def _get_session(service, session_id: str):
    if service is None:
        return None
    sm = getattr(service, "_session_manager", None)
    if sm is None:
        return None
    try:
        return sm.get_or_create(session_id)
    except Exception:
        return None


@rich_function_tool(running_summary="Scheduling wakeup...")
async def schedule_wakeup(
    ctx: RunContextWrapper[Any],
    interval_seconds: float,
    message: str,
    recurring: bool = True,
    end_at: Optional[str] = None,
    end_after: Optional[int] = None,
) -> RichToolOutput:
    """Schedule a self-wakeup notification on this session.

    The session wakes every ``interval_seconds`` (when the agent is idle)
    with a notification containing ``message`` plus framing. Use this
    when the user asks you to keep an eye on something over time — for
    example "check the deploy every 10 minutes until it lands" or "look
    at the queue depth every few hours". After scheduling, end your turn;
    the framework will wake you naturally on the cadence.

    Single-schedule per session: calling ``schedule_wakeup`` replaces any
    prior schedule on this session. Refuses while a ``/goal`` loop is
    active.

    Bounds: pass ``end_at`` (ISO-8601 string, e.g. ``"2026-05-19T09:00:00"``)
    and/or ``end_after`` (integer fire count). Whichever fires first wins.
    Omit both for unbounded recurring. Set ``recurring=False`` for a
    one-shot fire after ``interval_seconds``. On bound exhaustion the
    framework sends a final "Watch concluded" notification so you can
    summarize what you observed.

    To stop early, call ``cancel_wakeup``.

    Args:
        interval_seconds: Cadence between wakeups, in seconds. Must be
            positive. For "every 4 hours" pass ``14400``; for "every
            30 minutes" pass ``1800``.
        message: A short description of what you're watching, e.g.
            "queue depth on the inbound topic" or "deploy status of PR
            #4823". Surfaced to you on each wakeup so future-you knows
            what to look at.
        recurring: When True (default), keep firing until a bound or
            cancellation. When False, fire exactly once after
            ``interval_seconds`` then auto-conclude.
        end_at: Optional ISO-8601 timestamp. Naive timestamps are
            interpreted in the local timezone. Schedule self-cancels at
            or after this moment.
        end_after: Optional integer. Schedule self-cancels after this
            many fires.
    """
    sid = _session_id_from_ctx(ctx)
    if not sid:
        return _ack({"ok": False, "message": "No session context."})

    service = _get_service()
    if service is None:
        return _ack({"ok": False, "message": "Service unavailable."})
    session = _get_session(service, sid)
    if session is None:
        return _ack({"ok": False, "message": "Session unavailable."})

    end_at_unix, parse_err = _parse_end_at(end_at)
    if parse_err is not None:
        return _ack({"ok": False, "message": parse_err})

    # Defer import so this module loads cleanly even when the wakeup
    # server function package isn't present (mirrors how
    # goal_complete handles its server-functions dependency).
    try:
        from server_functions.wakeup import schedule_impl
    except Exception as e:
        _log.warning("schedule_wakeup: server_functions.wakeup unavailable: %s", e)
        return _ack({"ok": False, "message": "wakeup module not available."})

    result = await schedule_impl(
        service,
        session,
        interval_seconds=float(interval_seconds),
        message=str(message),
        recurring=bool(recurring),
        end_at=end_at_unix,
        end_after=end_after,
    )
    return _ack(result)


@rich_function_tool(running_summary="Cancelling wakeup...")
async def cancel_wakeup(
    ctx: RunContextWrapper[Any],
) -> RichToolOutput:
    """Cancel the recurring wakeup schedule on this session, if any.

    Use this when the user's underlying request is satisfied and no
    further wakeups are needed (e.g. the deploy finally landed, the
    queue drained, the file appeared). Safe to call even when no
    schedule is active — returns ``ok=False`` with a clear message.
    """
    sid = _session_id_from_ctx(ctx)
    if not sid:
        return _ack({"ok": False, "message": "No session context."})

    service = _get_service()
    if service is None:
        return _ack({"ok": False, "message": "Service unavailable."})
    session = _get_session(service, sid)
    if session is None:
        return _ack({"ok": False, "message": "Session unavailable."})

    try:
        from server_functions.wakeup import cancel_impl
    except Exception as e:
        _log.warning("cancel_wakeup: server_functions.wakeup unavailable: %s", e)
        return _ack({"ok": False, "message": "wakeup module not available."})

    result = await cancel_impl(service, session)
    return _ack(result)


def _ack(payload: dict) -> RichToolOutput:
    """Build the tool result. Visible to the model + summary line for
    the UI. Unlike ``goal_complete``, we DO render to the transcript so
    the user can see when a watch is scheduled or cancelled."""
    llm_output = json.dumps(payload)
    summary = payload.get("message") or ("ok" if payload.get("ok") else "failed")
    return RichToolOutput(
        llm_output,
        {
            "value": llm_output,
            "summary": summary,
            "truncated": False,
            "metadata": payload,
        },
    )
