"""Plan-in-context: the current plan block in the omni instructions.

``build_current_plan_block`` reads the session's durable plan (keyed
``(session_id, task_scope)`` — the same keys the plan tools use) and
renders a task_list-preview-shaped block, or nothing. Rendering is
best-effort by contract: no plan, a finalized plan, an all-complete
plan, or ANY read failure must yield ``""`` — instructions never fail
to build over plan bookkeeping.
"""

from types import SimpleNamespace

from omniagents.core.conversation.plans import Plan, PlanStep, plan_natural_key

from omni_code.agents.omni.context import (
    build_coding_context,
    build_current_plan_block,
)


def _plan(steps, *, status="started"):
    return Plan(
        plan_id="p1",
        thread_id="sess1",
        scope="main",
        generation=0,
        steps=steps,
        status=status,
    )


def _patch_loader(monkeypatch, plan):
    monkeypatch.setattr(
        "omni_code.agents.omni.context._load_current_plan", lambda variables: plan
    )


def test_renders_steps_open_blockers_and_next_unblocked(monkeypatch):
    _patch_loader(
        monkeypatch,
        _plan(
            [
                PlanStep(id="1", subject="Reproduce the failure", status="completed"),
                PlanStep(
                    id="2",
                    subject="Fix the root cause",
                    status="pending",
                    blocked_by=["1", "3"],
                ),
                PlanStep(id="3", subject="Write a failing test", status="in_progress"),
                PlanStep(id="4", subject="Update the docs", status="pending"),
            ]
        ),
    )

    block = build_current_plan_block({"session_id": "sess1"})

    assert "<current_plan>" in block and "</current_plan>" in block
    assert "✓ #1 Reproduce the failure" in block
    # Step 1 is completed, so only #3 remains an OPEN blocker of #2.
    assert "· #2 Fix the root cause  [blocked by 3]" in block
    assert "▶ #3 Write a failing test" in block
    assert "· #4 Update the docs" in block
    # #2 is blocked and #3 is already running: #4 is the only unblocked pending step.
    assert "Next unblocked: #4" in block


def test_renders_exit_criteria_when_summary_carries_it(monkeypatch):
    # ``exitCriteria`` lands in ``to_summaries()`` with the omniagents plan
    # schema change; a stub summary decouples this contract test from the
    # pinned framework version.
    stub = SimpleNamespace(
        is_open=True,
        to_summaries=lambda: [
            {
                "id": "1",
                "subject": "Run the suite",
                "status": "pending",
                "blockedBy": [],
                "exitCriteria": "pytest tests/ passes",
            }
        ],
    )
    _patch_loader(monkeypatch, stub)

    block = build_current_plan_block({"session_id": "sess1"})

    assert "· #1 Run the suite  (exit: pytest tests/ passes)" in block


def test_no_open_blockers_line_when_all_pending_blocked(monkeypatch):
    _patch_loader(
        monkeypatch,
        _plan(
            [
                PlanStep(id="1", subject="First", status="in_progress"),
                PlanStep(id="2", subject="Second", status="pending", blocked_by=["1"]),
            ]
        ),
    )

    block = build_current_plan_block({"session_id": "sess1"})

    assert "Next unblocked: none — finish or re-plan blockers first" in block


def test_renders_nothing_without_a_plan(monkeypatch):
    _patch_loader(monkeypatch, None)
    assert build_current_plan_block({"session_id": "sess1"}) == ""


def test_renders_nothing_when_all_steps_completed(monkeypatch):
    _patch_loader(
        monkeypatch,
        _plan([PlanStep(id="1", subject="Done thing", status="completed")]),
    )
    assert build_current_plan_block({"session_id": "sess1"}) == ""


def test_renders_nothing_for_finalized_plan(monkeypatch):
    _patch_loader(
        monkeypatch,
        _plan([PlanStep(id="1", subject="Open thing")], status="cancelled"),
    )
    assert build_current_plan_block({"session_id": "sess1"}) == ""


def test_renders_nothing_when_read_raises(monkeypatch):
    def _boom(variables):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr("omni_code.agents.omni.context._load_current_plan", _boom)
    assert build_current_plan_block({"session_id": "sess1"}) == ""


def test_renders_nothing_without_a_session_id():
    # No session id → no durable plan address; must return before touching
    # any store (no monkeypatching needed for this to be side-effect free).
    assert build_current_plan_block({}) == ""
    assert build_current_plan_block(None) == ""


def test_reads_durable_plan_end_to_end(monkeypatch, tmp_path):
    """Full read path against a real sqlite-backed conversation store."""
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)
    # No running service: the loader falls back to the default shard —
    # the same fallback ``_PlanHandle._bind`` takes for a sessionless bind.
    monkeypatch.setattr("omniagents.core.agents.service.current_service", lambda: None)

    from omniagents.core.conversation.recorder import ConversationRecorder
    from omniagents.core.conversation.store import get_conversation_store

    recorder = ConversationRecorder(get_conversation_store(None, None))
    recorder.ensure_thread("sess-e2e")
    plan = _plan(
        [
            PlanStep(id="1", subject="Seeded step", status="pending"),
        ]
    )
    recorder.record_plan(
        "sess-e2e", natural_key=plan_natural_key("main"), content=plan.to_content()
    )

    block = build_current_plan_block({"session_id": "sess-e2e"})

    assert "· #1 Seeded step" in block
    assert "Next unblocked: #1" in block


def test_coding_context_carries_current_plan_block(monkeypatch, tmp_path):
    _patch_loader(monkeypatch, None)
    monkeypatch.chdir(tmp_path)

    context = build_coding_context({"workspace_root": str(tmp_path)})

    assert context["current_plan_block"] == ""
