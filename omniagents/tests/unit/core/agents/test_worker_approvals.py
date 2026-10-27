"""Worker review uses real service approval/ownership paths."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.core.agents import guardian
from omniagents.core.agents.approval_policy import ApprovalRoute, resolve_approval_route
from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.core.agents.worker_approvals import WorkerApprovalContext
from omniagents.rpc.agents.workers import _default_worker_safety
from .test_approval_routing_service import (
    make_service,
    session_for,
    capture_broadcasts,
    bind_environment,
)


@pytest.mark.parametrize(
    "worker_auto,parent_auto,enforced,expected",
    [
        (False, False, False, False),
        (True, False, False, True),
        (False, True, False, True),
        (True, True, True, False),
    ],
)
def test_explicit_auto_approve_only(worker_auto, parent_auto, enforced, expected):
    opts = SafeAgentOptions(skip_approvals=worker_auto)
    spec = AgentSpec(
        name="worker",
        welcome_text="",
        get_agent_instructions=lambda: "",
        safe_agent_options=opts,
        build_agent=None,
    )
    parent = SimpleNamespace(
        spec=SimpleNamespace(
            safe_agent_options=SafeAgentOptions(skip_approvals=parent_auto),
            security_config={"safety_mode": "enforced"} if enforced else {},
        )
    )
    assert _default_worker_safety(spec, parent) is expected
    assert spec.use_safe_agent
    assert spec.safe_agent_options.skip_approvals is expected
    assert opts.skip_approvals is worker_auto  # no shared-spec mutation


@pytest.mark.asyncio
@pytest.mark.parametrize("contained", [False, True])
async def test_worker_guardian_even_with_manual_parent_or_offline_container(contained):
    parent, worker = make_service(), make_service()
    session = session_for(worker)
    session.approvals_reviewer = "user"
    worker._worker_approval_context = WorkerApprovalContext(parent, "parent", "w")
    if contained:
        await bind_environment(worker, session.id, network=False)
    route, _ = await resolve_approval_route(worker, session)
    assert route is ApprovalRoute.GUARDIAN


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "mcp"])
@pytest.mark.parametrize("decision", ["allow", "deny", "failed", "auto"])
async def test_worker_decisions_never_reach_a_person(monkeypatch, kind, decision):
    """A worker's approvals are decided by its policy or the guardian, which
    fails closed: nothing is passed up to the parent's person."""
    parent, worker = make_service(), make_service()
    parent_session = session_for(parent, "parent")
    session = session_for(worker, "worker")
    parent_events = capture_broadcasts(parent)
    capture_broadcasts(worker)
    worker._worker_approval_context = WorkerApprovalContext(
        parent, parent_session.id, "reviewer", decision == "auto"
    )
    if decision == "failed":
        result_decision = guardian.GuardianDecision(status="failed", message="failed")
    else:
        assessment = guardian.GuardianAssessment(
            outcome="allow" if decision in {"allow", "auto"} else "deny",
            risk_level="low",
            user_authorization="high",
            rationale="test decision",
        )
        result_decision = guardian.GuardianDecision(
            status="approved" if assessment.allowed else "denied",
            message=None if assessment.allowed else "denied",
            assessment=assessment,
        )
    review = AsyncMock(return_value=result_decision)
    monkeypatch.setattr(guardian, "review_approval", review)
    kwargs = dict(
        session=session,
        tool_name="write_file",
        arguments='{"path":"a"}',
        run_id="worker-run",
    )
    if kind == "tool":
        result = await worker.request_tool_approval(call_id="provider-id", **kwargs)
    else:
        result = await worker.request_mcp_approval(
            request_id="provider-id", server_label="projects", **kwargs
        )
    assert result["decision"] == (
        "approve" if decision in {"allow", "auto"} else "reject"
    )
    assert review.await_count == (0 if decision == "auto" else 1)
    assert not parent_events


def test_worker_uses_parent_guardian_model_configuration():
    from omniagents.core.agents.approval_policy import guardian_model_ref

    values = {"approvals.guardian_model": "provider/reviewer"}
    store = SimpleNamespace(
        effective_value=lambda key: (key in values, values.get(key))
    )
    parent = SimpleNamespace(_layered_config_store=lambda: store)
    worker = SimpleNamespace(
        _worker_approval_context=WorkerApprovalContext(parent, "p", "w")
    )
    assert guardian_model_ref(worker) == "provider/reviewer"
    values["approvals.guardian_model"] = "provider/new-reviewer"
    assert guardian_model_ref(worker) == "provider/new-reviewer"
