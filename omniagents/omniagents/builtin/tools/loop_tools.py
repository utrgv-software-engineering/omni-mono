"""Loop-continuation tool for dynamic recurring loops.

``loop_continue`` lets the agent self-pace a dynamic loop task: each
fire asks the agent to either schedule the next check (this tool) or
let the loop conclude by not calling it. State + timer machinery live
in ``omniagents.core.autonomy.loop``; this tool is a thin wrapper that
finds the service + session at call time and delegates to
``continue_impl``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.run_context import RunContextWrapper

from omniagents.core.tools import (
    RichToolOutput,
    rich_function_tool,
    session_id_from_ctx as _session_id_from_ctx,
)

_log = logging.getLogger(__name__)


def _get_service():
    try:
        from omniagents.core.agents.service import current_service

        return current_service()
    except Exception:
        return None


def _get_session(service, session_id: str):
    if service is None:
        return None
    try:
        return service.get_or_create_session(session_id)
    except Exception:
        return None


@rich_function_tool(running_summary="Continuing loop...")
async def loop_continue(
    ctx: RunContextWrapper[Any],
    task_id: str,
    delay_seconds: float,
    reason: str,
) -> RichToolOutput:
    """Schedule the next wakeup for a dynamic /loop task.

    Use only during a dynamic loop turn, after deciding another check is
    needed. The delay must be between 60 and 3600 seconds. If the loop is
    complete, do not call this tool; summarize completion instead.
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
        from omniagents.core.autonomy.loop import continue_impl
    except Exception as e:
        _log.warning("loop_continue: autonomy loop module unavailable: %s", e)
        return _ack({"ok": False, "message": "loop module not available."})
    result = await continue_impl(
        service,
        session,
        task_id=str(task_id),
        delay_seconds=float(delay_seconds),
        reason=str(reason),
    )
    return _ack(result)


def _ack(payload: dict) -> RichToolOutput:
    output = json.dumps(payload)
    return RichToolOutput(
        output,
        {
            "value": output,
            "summary": payload.get("message")
            or ("ok" if payload.get("ok") else "failed"),
            "truncated": False,
            "metadata": payload,
        },
    )
