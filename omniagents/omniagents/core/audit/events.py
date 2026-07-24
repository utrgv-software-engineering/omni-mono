"""Structured audit event definitions.

Audit events are emitted via the runtime hook system under the ``"audit"``
event name.  Any registered hook listening for ``"audit"`` will receive
the event payload as keyword arguments.

Usage in a runtime hook::

    @runtime_hook("audit")
    async def my_audit_handler(service, event, **kwargs):
        event_type = kwargs.get("event_type")
        ...
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

# ── Event type constants ─────────────────────────────────────────────

RUN_STARTED = "run_started"
RUN_END = "run_end"
TOOL_CALLED = "tool_called"
TOOL_RESULT = "tool_result"
TOOL_APPROVED = "tool_approved"
TOOL_REJECTED = "tool_rejected"
GUARDRAIL_TRIGGERED = "guardrail_triggered"
INCIDENT = "incident"


def make_audit_event(
    event_type: str,
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    user_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a structured audit event dict.

    The returned dict is passed as ``**kwargs`` to
    :meth:`AgentService.emit_runtime_hook("audit", ...)`.
    """
    return {
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "run_id": run_id,
        "agent_name": agent_name,
        "user_id": user_id,
        "details": details or {},
    }


# ── Helpers ──────────────────────────────────────────────────────────

DEFAULT_AUDIT_MAX_CHARS = 4096


def truncate_for_audit(value: Any, max_chars: int = DEFAULT_AUDIT_MAX_CHARS) -> str:
    """Truncate a value for safe inclusion in audit logs.

    Returns the string representation of *value*, capped at *max_chars*.
    If truncated, appends a suffix indicating the original length.
    """
    text = str(value) if value is not None else ""
    if len(text) > max_chars:
        return text[:max_chars] + f"... [truncated, {len(text)} total chars]"
    return text
