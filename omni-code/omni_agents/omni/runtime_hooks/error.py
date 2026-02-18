from __future__ import annotations

from typing import Any

from omniagents import runtime_hook


@runtime_hook("error")
async def on_error(
    *,
    service: Any,
    session: Any,
    run_id: str,
    params: dict,
    event: str,
) -> dict | None:
    err = params.get("error")
    if err is None:
        return None

    is_context = params.get("is_context_length_exceeded") is True
    if not is_context:
        return None

    vars_obj = getattr(session, "variables", None)
    if not isinstance(vars_obj, dict):
        vars_obj = {}
        try:
            session.variables = vars_obj
        except Exception:
            return None

    omni = vars_obj.get("omni_code")
    if not isinstance(omni, dict):
        omni = {}
        vars_obj["omni_code"] = omni

    omni["needs_context_compression"] = True
    omni["context_error_run_id"] = run_id
    omni["context_error"] = "context_length_exceeded"

    if omni.get("auto_compact") is False:
        return None

    if omni.get("auto_compact_in_progress"):
        return None

    omni["auto_compact_in_progress"] = True
    try:
        await service.server_call("compact", args={}, session_id=session.id)
    finally:
        omni["auto_compact_in_progress"] = False

    return {"retry": True}

