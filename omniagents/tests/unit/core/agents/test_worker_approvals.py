"""Worker review and escalation use real service approval/ownership paths."""

import asyncio
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
@pytest.mark.parametrize("decision", ["allow", "deny", "unsure", "auto", "cancel"])
async def test_worker_decisions_and_human_fallback(monkeypatch, kind, decision):
    parent, worker = make_service(), make_service()
    parent_session = session_for(parent, "parent")
    session = session_for(worker, "worker")
    parent_events = capture_broadcasts(parent)
    capture_broadcasts(worker)
    worker._worker_approval_context = WorkerApprovalContext(
        parent, parent_session.id, "reviewer", decision == "auto"
    )
    assessment = (
        None
        if decision in {"unsure", "cancel"}
        else guardian.GuardianAssessment(
            outcome=decision if decision != "auto" else "allow",
            risk_level="low",
            user_authorization="explicit",
            rationale="test decision",
        )
    )
    review = AsyncMock(return_value=assessment)
    monkeypatch.setattr(guardian, "review_approval", review)
    kwargs = dict(
        session=session,
        tool_name="write_file",
        arguments='{"path":"a"}',
        run_id="worker-run",
    )
    if kind == "tool":
        request = worker.request_tool_approval(call_id="provider-id", **kwargs)
    else:
        request = worker.request_mcp_approval(
            request_id="provider-id", server_label="projects", **kwargs
        )
    task = asyncio.create_task(request)
    try:
        async with asyncio.timeout(3):
            if decision in {"unsure", "cancel"}:
                while not parent_events:
                    await asyncio.sleep(0)
                method, payload = parent_events[0]
                assert method == f"{kind}_approval_requested"
                assert payload["session_id"] == "parent"
                assert payload["worker_session_id"] == "worker"
                assert payload["worker_id"] == "reviewer"
                assert not task.done()
                parent._clear_live_state(parent_session)
                replay = getattr(parent_session, f"pending_{kind}_approvals")
                assert replay  # parent's run ending must not erase worker prompts
                if decision == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert not replay
                    assert not getattr(parent, f"_pending_{kind}_approvals")
                    return
                token = payload["call_id" if kind == "tool" else "request_id"]
                owner, _, _ = parent._resolve_rpc_resource_owner(
                    (
                        "tool_approval_response"
                        if kind == "tool"
                        else "mcp_approval_response"
                    ),
                    {"call_id" if kind == "tool" else "request_id": token},
                )
                assert owner == parent_session.user_id
                if kind == "tool":
                    assert await parent.tool_approval_response(
                        token, "reject", rejection_message="No"
                    )
                else:
                    assert await parent.mcp_approval_response(
                        token, "reject", rejection_message="No"
                    )
            result = await task
        assert result["decision"] == (
            "approve" if decision in {"allow", "auto"} else "reject"
        )
        assert review.await_count == (0 if decision == "auto" else 1)
        if decision in {"allow", "deny", "auto"}:
            assert not parent_events
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


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
