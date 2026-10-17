"""Audit delivery shared by SDK stream tools and nested orchestration calls."""

from omniagents.core.audit.events import (
    make_audit_event,
    truncate_for_audit,
    TOOL_CALLED,
    TOOL_RESULT,
)


async def emit_tool_audit(service, session, method, payload):
    if service is None or method not in ("tool_called", "tool_result"):
        return
    try:
        field = "input" if method == "tool_called" else "output"
        await service.emit_runtime_hook(
            "audit",
            **make_audit_event(
                TOOL_CALLED if method == "tool_called" else TOOL_RESULT,
                session_id=getattr(session, "id", None),
                user_id=getattr(session, "user_id", None),
                run_id=payload.get("run_id"),
                details={
                    "tool": payload.get("tool"),
                    "call_id": payload.get("call_id"),
                    field: truncate_for_audit(payload.get(field, ""), 4096),
                },
            ),
        )
    except Exception:
        # Preserve the existing best-effort audit sink contract. Durable tool
        # claims/completions are independently fail-closed.
        pass
