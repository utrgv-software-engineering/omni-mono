"""Workflow enforcement at the plan tools: start guard + completion review.

Drives ``task_create`` / ``task_update`` exactly as the model does and
asserts on the durable store, following ``test_task_list_tools.py``. The
judge itself is mocked at ``plan_review.review_completion`` — its own
one-shot behaviour is covered in ``test_plan_review.py``.
"""

import json
from types import SimpleNamespace

import pytest

from omniagents.builtin.tools import task_list_tools as tlt
from omniagents.core.agents import plan_review
from omniagents.core.agents import service as service_module
from omniagents.core.conversation.plans import PlanStore
from omniagents.core.conversation.store import (
    get_conversation_store,
    reset_conversation_store,
)


class _Ctx:
    def __init__(self, context=None):
        self.context = context or {}


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    reset_conversation_store()
    tlt.reset_sessionless_plans()
    plan_review.reset_sessionless_breaker()
    yield
    reset_conversation_store()
    tlt.reset_sessionless_plans()
    plan_review.reset_sessionless_breaker()


@pytest.fixture
def host():
    """A fake in-context AgentService so ``session_from_ctx`` resolves a
    session (breaker state, broadcasts) without a real service stack."""
    session = SimpleNamespace(id="s1")
    broadcasts = []

    async def record_broadcast(sess, method, payload, **kwargs):
        broadcasts.append((method, payload, kwargs))

    service = SimpleNamespace(
        _session_manager=SimpleNamespace(_by_id={"s1": session}),
        _broadcast_session=record_broadcast,
    )
    token = service_module._current_service.set(service)
    try:
        yield SimpleNamespace(service=service, session=session, broadcasts=broadcasts)
    finally:
        service_module._current_service.reset(token)


def store():
    return get_conversation_store(None, None)


def plans():
    return PlanStore(store())


async def _call(tool, *args, **kwargs):
    return await tool._original_func(*args, **kwargs)


def _meta(out):
    return out.ui_metadata if hasattr(out, "ui_metadata") else out[1]


def ctx():
    return _Ctx({"session_id": "s1"})


def step(step_id):
    plan = plans().current("s1", "main")
    return plan.step(step_id) if plan else None


def accept(criteria_met="satisfied", evidence="shown", rationale="shown passing"):
    return plan_review.PlanReviewAssessment(
        criteria_met=criteria_met,
        evidence=evidence,
        outcome="accept",
        rationale=rationale,
    )


def reject(rationale="transcript shows the test run failing"):
    return plan_review.PlanReviewAssessment(
        criteria_met="unsatisfied",
        evidence="shown",
        outcome="reject",
        rationale=rationale,
    )


def install_judge(monkeypatch, outcomes):
    """Fake ``review_completion`` yielding *outcomes* in order; logs calls."""
    calls = []
    queue = list(outcomes)

    async def fake(service, session, request):
        calls.append(request)
        return queue.pop(0) if queue else None

    monkeypatch.setattr(plan_review, "review_completion", fake)
    return calls


def forbid_judge(monkeypatch):
    async def boom(service, session, request):  # pragma: no cover
        raise AssertionError("the judge must not be invoked")

    monkeypatch.setattr(plan_review, "review_completion", boom)


# ---------------------------------------------------------------------
# Start guard — mechanical: open blockers refuse in_progress
# ---------------------------------------------------------------------


class TestStartGuard:
    @pytest.mark.asyncio
    async def test_open_blockers_refuse_start(self):
        c = ctx()
        await _call(tlt.task_create, c, subject="repro", description="d")
        await _call(tlt.task_create, c, subject="fix", description="d")
        await _call(tlt.task_update, c, task_id="2", addBlockedBy=["1"])

        out = await _call(tlt.task_update, c, task_id="2", status="in_progress")
        payload = json.loads(str(out))
        assert payload == {
            "task_id": "2",
            "error": "blocked",
            "openBlockers": ["1"],
        }
        assert _meta(out)["metadata"]["success"] is False
        assert "repro" in _meta(out)["preview"]
        # Nothing written.
        assert step("2").status == "pending"

    @pytest.mark.asyncio
    async def test_override_blockers_proceeds(self):
        c = ctx()
        await _call(tlt.task_create, c, subject="repro", description="d")
        await _call(tlt.task_create, c, subject="fix", description="d")
        await _call(tlt.task_update, c, task_id="2", addBlockedBy=["1"])

        out = await _call(
            tlt.task_update, c, task_id="2", status="in_progress", overrideBlockers=True
        )
        assert json.loads(str(out))["task"]["status"] == "in_progress"
        assert step("2").status == "in_progress"

    @pytest.mark.asyncio
    async def test_completed_blockers_do_not_block(self):
        c = ctx()
        await _call(tlt.task_create, c, subject="repro", description="d")
        await _call(tlt.task_create, c, subject="fix", description="d")
        await _call(tlt.task_update, c, task_id="2", addBlockedBy=["1"])
        await _call(tlt.task_update, c, task_id="1", status="completed")

        out = await _call(tlt.task_update, c, task_id="2", status="in_progress")
        assert json.loads(str(out))["task"]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_only_in_progress_is_guarded(self):
        c = ctx()
        await _call(tlt.task_create, c, subject="repro", description="d")
        await _call(tlt.task_create, c, subject="fix", description="d")
        await _call(tlt.task_update, c, task_id="2", addBlockedBy=["1"])

        out = await _call(
            tlt.task_update, c, task_id="2", status="blocked", subject="fix (waiting)"
        )
        assert json.loads(str(out))["task"]["status"] == "blocked"


# ---------------------------------------------------------------------
# Completion routing — no criteria / reviewer off / guardian
# ---------------------------------------------------------------------


class TestCompletionRouting:
    @pytest.mark.asyncio
    async def test_no_criteria_completes_waived_without_a_judge_call(self, monkeypatch):
        forbid_judge(monkeypatch)
        c = ctx()
        await _call(tlt.task_create, c, subject="a", description="d")
        out = await _call(tlt.task_update, c, task_id="1", status="completed")

        assert json.loads(str(out))["task"]["status"] == "completed"
        assert step("1").review == {
            "outcome": "waived",
            "reviewer": "none",
            "rationale": "",
        }
        snapshot = _meta(out)["tasks_snapshot"]
        assert snapshot[0]["verified"] is None

    @pytest.mark.asyncio
    async def test_reviewer_off_waives_despite_criteria(self, monkeypatch):
        forbid_judge(monkeypatch)
        monkeypatch.setattr(
            "omniagents.core.agents.workflow_policy.effective_workflow_reviewer",
            lambda service, session: "off",
        )
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        out = await _call(tlt.task_update, c, task_id="1", status="completed")

        assert json.loads(str(out))["task"]["status"] == "completed"
        assert step("1").review["outcome"] == "waived"

    @pytest.mark.asyncio
    async def test_guardian_route_invokes_the_judge_with_the_step(self, monkeypatch):
        calls = install_judge(monkeypatch, [accept()])
        c = ctx()
        await _call(
            tlt.task_create,
            c,
            subject="run tests",
            description="run the suite",
            exitCriteria="pytest -q passes",
        )
        await _call(tlt.task_update, c, task_id="1", status="completed")

        assert len(calls) == 1
        request = calls[0]
        assert request.step_id == "1"
        assert request.subject == "run tests"
        assert request.exit_criteria == "pytest -q passes"

    @pytest.mark.asyncio
    async def test_criteria_set_in_the_same_update_are_reviewed(self, monkeypatch):
        calls = install_judge(monkeypatch, [accept()])
        c = ctx()
        await _call(tlt.task_create, c, subject="a", description="d")
        await _call(
            tlt.task_update,
            c,
            task_id="1",
            status="completed",
            exitCriteria="tests pass",
        )
        assert len(calls) == 1
        assert calls[0].exit_criteria == "tests pass"
        assert step("1").exit_criteria == "tests pass"


# ---------------------------------------------------------------------
# Judge outcomes — verified / unverified / reject / unavailable
# ---------------------------------------------------------------------


class TestJudgeOutcomes:
    @pytest.mark.asyncio
    async def test_accept_verified(self, monkeypatch, host):
        install_judge(monkeypatch, [accept()])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        out = await _call(tlt.task_update, c, task_id="1", status="completed")

        assert json.loads(str(out))["task"]["status"] == "completed"
        assert step("1").review == {
            "outcome": "verified",
            "reviewer": "guardian",
            "rationale": "shown passing",
        }
        assert _meta(out)["tasks_snapshot"][0]["verified"] is True
        # Clean verifications broadcast too — the transcript shows every
        # reviewed outcome; only waived completions stay silent.
        assert len(host.broadcasts) == 1
        event, payload, _kwargs = host.broadcasts[0]
        assert event == "plan_completion_reviewed"
        assert payload["outcome"] == "accept_verified"
        assert payload["reviewer"] == "guardian"

    @pytest.mark.asyncio
    async def test_accept_indeterminate_is_unverified(self, monkeypatch, host):
        install_judge(
            monkeypatch,
            [accept(criteria_met="indeterminate", evidence="asserted", rationale="r")],
        )
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        out = await _call(tlt.task_update, c, task_id="1", status="completed")

        assert step("1").review == {
            "outcome": "unverified",
            "reviewer": "guardian",
            "rationale": "r",
        }
        assert _meta(out)["tasks_snapshot"][0]["verified"] is False
        assert [m for m, _, _ in host.broadcasts] == ["plan_completion_reviewed"]
        assert host.broadcasts[0][1]["outcome"] == "accept_unverified"

    @pytest.mark.asyncio
    async def test_reject_leaves_the_step_untouched(self, monkeypatch, host):
        install_judge(monkeypatch, [reject("no test run in transcript")])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        await _call(tlt.task_update, c, task_id="1", status="in_progress")
        out = await _call(tlt.task_update, c, task_id="1", status="completed")

        payload = json.loads(str(out))
        assert payload == {
            "task_id": "1",
            "error": "exit_criteria_not_met",
            "rationale": "no test run in transcript",
        }
        assert _meta(out)["metadata"]["success"] is False
        stored = step("1")
        assert stored.status == "in_progress"
        assert stored.review is None
        # The error payload's snapshot shows the stored state, not the
        # refused completion.
        assert _meta(out)["tasks_snapshot"][0]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_reject_broadcast_carries_the_contract_payload(
        self, monkeypatch, host
    ):
        install_judge(monkeypatch, [reject("no test run in transcript")])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="run tests", description="d", exitCriteria="x"
        )
        await _call(tlt.task_update, c, task_id="1", status="completed")

        method, payload, kwargs = host.broadcasts[0]
        assert method == "plan_completion_reviewed"
        assert kwargs == {"queue_if_unseen": True}
        run_id = payload.pop("run_id", None)
        assert payload == {
            "session_id": "s1",
            "task_id": "1",
            "subject": "run tests",
            "outcome": "reject",
            "reviewer": "guardian",
            "rationale": "no test run in transcript",
        }
        assert run_id is None or isinstance(run_id, str)

    @pytest.mark.asyncio
    async def test_judge_unavailable_accepts_unverified(self, monkeypatch, host):
        install_judge(monkeypatch, [None])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        out = await _call(tlt.task_update, c, task_id="1", status="completed")

        assert json.loads(str(out))["task"]["status"] == "completed"
        assert step("1").review == {
            "outcome": "unverified",
            "reviewer": "none",
            "rationale": "review unavailable",
        }
        assert host.broadcasts[0][1]["outcome"] == "accept_unverified"
        assert host.broadcasts[0][1]["reviewer"] == "none"


# ---------------------------------------------------------------------
# Circuit breaker — two rejections of one step, then accept unverified
# ---------------------------------------------------------------------


class TestBreaker:
    @pytest.mark.asyncio
    async def test_third_attempt_accepts_unverified(self, monkeypatch, host):
        calls = install_judge(monkeypatch, [reject("first"), reject("second")])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        for _ in range(2):
            out = await _call(tlt.task_update, c, task_id="1", status="completed")
            assert json.loads(str(out))["error"] == "exit_criteria_not_met"

        out = await _call(tlt.task_update, c, task_id="1", status="completed")
        assert json.loads(str(out))["task"]["status"] == "completed"
        assert len(calls) == 2  # the third attempt never reached the judge
        assert step("1").review == {
            "outcome": "unverified",
            "reviewer": "guardian",
            "rationale": "accepted after repeated disagreement: second",
        }
        assert [p["outcome"] for _, p, _ in host.broadcasts] == [
            "reject",
            "reject",
            "escalated",
        ]

    @pytest.mark.asyncio
    async def test_accept_resets_the_streak(self, monkeypatch, host):
        calls = install_judge(
            monkeypatch, [reject("r1"), accept(), reject("r2"), reject("r3")]
        )
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        assert "error" in json.loads(
            str(await _call(tlt.task_update, c, task_id="1", status="completed"))
        )
        assert (
            json.loads(
                str(await _call(tlt.task_update, c, task_id="1", status="completed"))
            )["task"]["status"]
            == "completed"
        )
        # Reopen and try again: had the accept not reset the streak, the
        # next rejection would already trip the breaker.
        await _call(tlt.task_update, c, task_id="1", status="in_progress")
        assert "error" in json.loads(
            str(await _call(tlt.task_update, c, task_id="1", status="completed"))
        )
        out = await _call(tlt.task_update, c, task_id="1", status="completed")
        assert json.loads(str(out))["error"] == "exit_criteria_not_met"
        assert len(calls) == 4

    @pytest.mark.asyncio
    async def test_a_different_step_resets_the_streak(self, monkeypatch, host):
        calls = install_judge(
            monkeypatch, [reject("a1"), reject("a2"), accept(), reject("a3")]
        )
        c = ctx()
        await _call(tlt.task_create, c, subject="a", description="d", exitCriteria="ca")
        await _call(tlt.task_create, c, subject="b", description="d", exitCriteria="cb")
        for _ in range(2):
            await _call(tlt.task_update, c, task_id="1", status="completed")
        # Reviewing step 2 re-keys the breaker state.
        await _call(tlt.task_update, c, task_id="2", status="completed")
        # Step 1's next attempt is judged again, not auto-accepted.
        out = await _call(tlt.task_update, c, task_id="1", status="completed")
        assert json.loads(str(out))["error"] == "exit_criteria_not_met"
        assert len(calls) == 4


# ---------------------------------------------------------------------
# Criteria-edit ratchet — mid-step edits stamp the FIRST original
# ---------------------------------------------------------------------


class TestCriteriaEditRatchet:
    @pytest.mark.asyncio
    async def test_first_in_progress_edit_stamps_and_fires_no_review(self, monkeypatch):
        forbid_judge(monkeypatch)
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        await _call(tlt.task_update, c, task_id="1", status="in_progress")

        out = await _call(
            tlt.task_update, c, task_id="1", exitCriteria="tests pass on happy path"
        )
        task = json.loads(str(out))["task"]
        assert task["exitCriteria"] == "tests pass on happy path"
        assert task["criteriaEdited"] is True
        assert task["originalExitCriteria"] == "tests pass"
        stored = step("1")
        assert stored.exit_criteria == "tests pass on happy path"
        assert stored.original_exit_criteria == "tests pass"
        assert _meta(out)["tasks_snapshot"][0]["criteriaEdited"] is True

    @pytest.mark.asyncio
    async def test_later_edits_keep_the_first_original(self, monkeypatch):
        forbid_judge(monkeypatch)
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        await _call(tlt.task_update, c, task_id="1", status="in_progress")
        await _call(tlt.task_update, c, task_id="1", exitCriteria="second version")
        await _call(tlt.task_update, c, task_id="1", exitCriteria="third version")

        stored = step("1")
        assert stored.exit_criteria == "third version"
        assert stored.original_exit_criteria == "tests pass"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("free_status", ["pending", "blocked"])
    async def test_edits_before_the_step_starts_do_not_stamp(
        self, monkeypatch, free_status
    ):
        forbid_judge(monkeypatch)
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        if free_status == "blocked":
            await _call(tlt.task_update, c, task_id="1", status="blocked")

        out = await _call(tlt.task_update, c, task_id="1", exitCriteria="looser bar")
        task = json.loads(str(out))["task"]
        assert task["criteriaEdited"] is False
        assert task["originalExitCriteria"] == ""
        assert step("1").original_exit_criteria == ""

    @pytest.mark.asyncio
    async def test_setting_the_same_value_is_not_an_edit(self, monkeypatch):
        forbid_judge(monkeypatch)
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        await _call(tlt.task_update, c, task_id="1", status="in_progress")
        await _call(tlt.task_update, c, task_id="1", exitCriteria="tests pass")

        assert step("1").original_exit_criteria == ""

    @pytest.mark.asyncio
    async def test_edit_in_the_completing_call_stamps_and_reaches_the_judge(
        self, monkeypatch
    ):
        calls = install_judge(monkeypatch, [accept()])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        await _call(tlt.task_update, c, task_id="1", status="in_progress")

        out = await _call(
            tlt.task_update,
            c,
            task_id="1",
            status="completed",
            exitCriteria="tests pass on happy path",
        )
        assert json.loads(str(out))["task"]["status"] == "completed"
        # The review request carries BOTH the edited criteria and the
        # original stamped by this very call.
        assert len(calls) == 1
        assert calls[0].exit_criteria == "tests pass on happy path"
        assert calls[0].original_exit_criteria == "tests pass"
        stored = step("1")
        assert stored.exit_criteria == "tests pass on happy path"
        assert stored.original_exit_criteria == "tests pass"

    @pytest.mark.asyncio
    async def test_unedited_completion_carries_no_original(self, monkeypatch):
        calls = install_judge(monkeypatch, [accept()])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        await _call(tlt.task_update, c, task_id="1", status="completed")

        assert calls[0].original_exit_criteria == ""

    @pytest.mark.asyncio
    async def test_rejected_completion_does_not_persist_the_stamp(
        self, monkeypatch, host
    ):
        """A denial writes nothing — including the ratchet stamp; the edit
        it arrived with never landed either."""
        install_judge(monkeypatch, [reject("no test run in transcript")])
        c = ctx()
        await _call(
            tlt.task_create, c, subject="a", description="d", exitCriteria="tests pass"
        )
        await _call(tlt.task_update, c, task_id="1", status="in_progress")
        out = await _call(
            tlt.task_update,
            c,
            task_id="1",
            status="completed",
            exitCriteria="looser bar",
        )
        assert json.loads(str(out))["error"] == "exit_criteria_not_met"
        stored = step("1")
        assert stored.exit_criteria == "tests pass"
        assert stored.original_exit_criteria == ""
