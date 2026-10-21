"""Route resolution for plan-step completion reviews.

Sibling of :mod:`omniagents.core.agents.approval_policy`, same layering,
different stake: approvals gate *security* (what a tool may do), this
gates *process* (whether a step's declared exit criteria are actually
met before ``completed`` lands). A step opts in by carrying non-empty
``exit_criteria``; steps without criteria are never reviewed.

* ``OFF`` — no review: the step has no criteria, or the reviewer is
  ``off``. Completion is recorded as ``waived``.
* ``GUARDIAN`` — the one-shot completion reviewer
  (:mod:`omniagents.core.agents.plan_review`) judges the completion
  against the transcript and accepts or rejects it.

There is deliberately no human route (yet): a "user" reviewer needs a
dedicated process prompt — ``request_tool_approval`` cannot be reused
because its pre-route applies the SECURITY approval policy, which
would silently bypass the human in sandboxed sessions. Until that
exists, reviews are always the guardian's job.

The resolver reads the layered configuration on every call
(``workflow.*`` fields are ``reload=hot``) and the session's
per-conversation override (``session.workflow_reviewer``), mirroring
``effective_reviewer`` in ``approval_policy``.
"""

from __future__ import annotations

from typing import Any

WORKFLOW_REVIEWER_GUARDIAN = "guardian"
WORKFLOW_REVIEWER_OFF = "off"
WORKFLOW_REVIEWERS = (
    WORKFLOW_REVIEWER_GUARDIAN,
    WORKFLOW_REVIEWER_OFF,
)

COMPLETION_ROUTE_OFF = "off"
COMPLETION_ROUTE_GUARDIAN = "guardian"


def effective_workflow_reviewer(service: Any, session: Any) -> str:
    """Session override first, then the layered-config process default."""
    from omniagents.core.agents.approval_policy import _config_value

    session_reviewer = getattr(session, "workflow_reviewer", None)
    if session_reviewer in WORKFLOW_REVIEWERS:
        return session_reviewer
    return _config_value(
        service,
        "workflow.completion_reviewer",
        WORKFLOW_REVIEWER_GUARDIAN,
        WORKFLOW_REVIEWERS,
    )


def workflow_guardian_model_ref(service: Any) -> str | None:
    from omniagents.core.agents.approval_policy import _config_value

    value = _config_value(service, "workflow.guardian_model", "", None)
    value = value.strip() if isinstance(value, str) else ""
    return value or None


def resolve_completion_route(reviewer: str, step: Any) -> str:
    """Pure routing decision for one step's ``completed`` attempt.

    Returns ``"off"`` (no review — record ``waived``) or ``"guardian"``
    (one-shot reviewer). A step with no exit criteria is never reviewed
    regardless of the reviewer setting.
    """
    criteria = getattr(step, "exit_criteria", "") or ""
    if not criteria.strip():
        return COMPLETION_ROUTE_OFF
    if reviewer == WORKFLOW_REVIEWER_OFF:
        return COMPLETION_ROUTE_OFF
    return COMPLETION_ROUTE_GUARDIAN


__all__ = [
    "COMPLETION_ROUTE_GUARDIAN",
    "COMPLETION_ROUTE_OFF",
    "WORKFLOW_REVIEWERS",
    "WORKFLOW_REVIEWER_GUARDIAN",
    "WORKFLOW_REVIEWER_OFF",
    "effective_workflow_reviewer",
    "resolve_completion_route",
    "workflow_guardian_model_ref",
]
