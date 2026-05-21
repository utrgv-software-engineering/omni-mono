"""Goal-loop control tools.

A single tool, ``goal_complete``, lets the agent signal that the
``/goal`` autopilot loop should stop. Replaces the earlier text-sentinel
mechanism (``<<GOAL_DONE>>``) — using a tool call instead is structured
(no text-scan false positives), auto-approved (the agent doesn't pause
for permission), and hidden from the user transcript (renders as
nothing in frontends that honor ``display_type: "hidden"``).

The tool mutates the per-session GoalState living in
``server_functions/goal.py``. The on-run-end listener in that module
checks ``state.completed`` at the top of every iteration, so calling
``goal_complete`` during a run causes the loop to exit cleanly when
the run ends — no extra polling, no race.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput

from tools.bash_tool import _session_id_from_ctx


_log = logging.getLogger(__name__)


@rich_function_tool(client_status="Marking goal complete...")
async def goal_complete(
    ctx: RunContextWrapper[Any],
    reason: str = "achieved",
    evidence: Optional[str] = None,
) -> RichToolOutput:
    """Signal that the autonomous goal loop should stop.

    Call this once when the goal has been achieved and verified, or
    when you've reached a true blocker (missing credentials, external
    system unavailable, contradictory requirements). The ``/goal``
    autopilot loop detects the call and stops scheduling new turns —
    no further continuation notifications will arrive.

    Args:
        reason: ``"achieved"`` (default) when the goal is done, or
            ``"blocked"`` when you cannot proceed. Informational only.
        evidence: Auditable artifact backing the claim. For
            ``"achieved"`` cite the verification surface (test name +
            result, benchmark number, generated artifact, etc.); for
            ``"blocked"`` describe what's missing and what input would
            unblock progress. Recorded on the GoalState and surfaced in
            the goal panel + /goal.status output.
    """
    # Defer the import so this module loads cleanly even when the goal
    # server function package isn't installed (e.g., minimal agent
    # configurations that include the tool but skip the autopilot loop).
    try:
        from server_functions.goal import _goals
    except Exception as e:
        _log.warning("goal_complete: server_functions.goal unavailable: %s", e)
        return _ack(reason=reason, applied=False, evidence=evidence)

    sid = _session_id_from_ctx(ctx)
    if not sid:
        return _ack(reason=reason, applied=False, evidence=evidence)

    state = _goals.get(sid)
    if state is None:
        # No active goal on this session — agent called the tool
        # without a /goal-start having happened first. Acknowledge so
        # the model doesn't loop trying to re-call.
        return _ack(reason=reason, applied=False, evidence=evidence)

    state.completed = True
    state.completion_reason = reason
    if evidence is not None:
        text = str(evidence).strip()
        state.evidence = text or None
    _log.info(
        "goal_complete fired for session=%s reason=%s evidence=%s",
        sid,
        reason,
        (state.evidence[:120] + "…") if state.evidence and len(state.evidence) > 120 else state.evidence,
    )
    return _ack(reason=reason, applied=True, evidence=state.evidence)


def _ack(*, reason: str, applied: bool, evidence: Optional[str] = None) -> RichToolOutput:
    """Build the tool result. ``display_type: "hidden"`` tells the
    frontend to skip rendering both the tool-call row and the result
    row — the goal panel is the visual feedback channel for this."""
    payload: dict[str, Any] = {"applied": applied, "reason": reason}
    if evidence:
        payload["evidence"] = evidence
    llm_output = json.dumps(payload)
    return RichToolOutput(
        llm_output,
        {
            "value": llm_output,
            "display_type": "hidden",
            "summary": (
                f"Goal marked {reason}" if applied else "goal_complete (no-op)"
            ),
            "truncated": False,
            "metadata": payload,
        },
    )
