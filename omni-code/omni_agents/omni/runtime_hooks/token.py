from __future__ import annotations

from typing import Any

from omniagents import runtime_hook


@runtime_hook("token")
async def on_token(*, service: Any, session: Any, run_id: str, params: dict, event: str) -> None:
    max_input_tokens = params.get("max_input_tokens")
    delta = params.get("delta") or {}
    total_tokens = delta.get("total_tokens") or 0
    if not max_input_tokens:
        return

    utilization = float(total_tokens) / float(max_input_tokens)
    if utilization < 0.9:
        return

    vars_obj = getattr(session, "variables", None)
    if not isinstance(vars_obj, dict):
        vars_obj = {}
        try:
            session.variables = vars_obj
        except Exception:
            return

    omni = vars_obj.get("omni_code")
    if not isinstance(omni, dict):
        omni = {}
        vars_obj["omni_code"] = omni

    omni["needs_context_compression"] = True
    omni["context_utilization"] = utilization
    omni["context_utilization_run_id"] = run_id

    if omni.get("auto_compact") is False:
        return

    if omni.get("auto_compact_in_progress"):
        return

    omni["auto_compact_in_progress"] = True
    try:
        await service.server_call("compact", args={}, session_id=session.id)
    finally:
        omni["auto_compact_in_progress"] = False
