"""``ask_user`` — structured user elicitation from agent code.

Unlike ``escalate`` (a free-text banner routed through the generic
client-function lane), ``ask_user`` goes through the structured
elicitation protocol (``elicitation_requested`` / ``elicitation_response``):
clients render a real question / confirmation / option-select surface,
validate the input, and the first response from any connected tab wins.
Pending prompts survive reconnects and clear when the run ends.

This tool covers the interactive kinds an agent can consume directly
(``question``, ``confirm``, ``select``). Server-side integrations that
need forms or URL actions call
``AgentService.request_elicitation(kind="form" | "url", ...)`` directly.
"""

from __future__ import annotations

from typing import Any, Optional

from agents.run_context import RunContextWrapper

from omniagents.core.tools import function_tool
from omniagents.core.session.helpers import session_from_ctx


@function_tool
async def ask_user(
    ctx: RunContextWrapper[Any],
    message: str,
    kind: str = "question",
    options: Optional[list[str]] = None,
    title: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
) -> dict:
    """Ask the user a structured question and wait for their answer.

    The run pauses until the user responds (in any connected client),
    declines, or the prompt times out. Use for decisions and information
    you genuinely need before proceeding; prefer ``notify`` for
    non-blocking heads-ups.

    Args:
        message: The question or decision, phrased for the user.
        kind: ``"question"`` (free-text answer, default), ``"confirm"``
            (yes/no), or ``"select"`` (choose among ``options``).
        options: The selectable choices; required when ``kind="select"``.
        title: Optional short heading shown above the message.
        timeout_seconds: Give up after this long. Omit to wait until the
            user responds or the run ends.

    Returns:
        ``{"answered": True, ...}`` with ``text`` (question),
        ``confirmed`` (confirm) or ``selected`` (select) on success;
        ``{"answered": False, "reason": ...}`` when the user declined,
        the prompt expired, or no client could render it.
    """
    from omniagents.core.agents.service import current_service

    svc = current_service()
    if svc is None:
        return {"answered": False, "reason": "no agent service running"}
    session = session_from_ctx(ctx)
    if session is None:
        return {"answered": False, "reason": "no session context"}
    if kind not in ("question", "confirm", "select"):
        return {
            "answered": False,
            "reason": f"unsupported kind {kind!r}; " "use question, confirm, or select",
        }
    if kind == "select" and not options:
        return {"answered": False, "reason": "select requires options"}

    try:
        outcome = await svc.request_elicitation(
            session=session,
            kind=kind,
            message=message,
            title=title,
            options=list(options) if options else None,
            run_id=getattr(session, "active_run_id", None),
            source="agent",
            timeout_ms=int(timeout_seconds * 1000) if timeout_seconds else None,
            # The answer becomes this tool's result (model-visible), so
            # journaling it is honest — flag prompts that must stay out
            # of history by calling request_elicitation directly with
            # persist_response=False and consuming the value server-side.
            persist_response=True,
        )
    except Exception as e:
        return {"answered": False, "reason": f"elicitation dispatch failed: {e}"}

    action = outcome.get("action")
    if action != "accept":
        return {
            "answered": False,
            "reason": outcome.get("reason") or f"user response: {action}",
        }
    value = outcome.get("value") or {}
    result: dict[str, Any] = {"answered": True}
    for key in ("text", "confirmed", "selected"):
        if key in value:
            result[key] = value[key]
    return result


__all__ = ["ask_user"]
