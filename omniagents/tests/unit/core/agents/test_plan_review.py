import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from omniagents.core.agents import plan_review


@contextmanager
def bound_workspace(root):
    """Bind a HostWorkspace to the contextvar the way a run lease does."""
    from omniagents.workspace import reset_workspace, set_workspace
    from omniagents.workspace.host import HostWorkspace

    ws = HostWorkspace(root=str(root))
    token = set_workspace(ws)
    try:
        yield ws
    finally:
        reset_workspace(token)


def assessment_json(**overrides):
    data = {
        "criteria_met": "satisfied",
        "evidence": "shown",
        "outcome": "accept",
        "rationale": "The transcript shows the test run passing.",
    }
    data.update(overrides)
    return json.dumps(data)


def request():
    return plan_review.PlanReviewRequest(
        step_id="3",
        subject="Run the tests",
        description="Run the suite and confirm green.",
        exit_criteria="pytest -q passes",
        plan_id="itm_plan",
        scope="main",
    )


class TestParseAssessment:
    def test_parses_strict_json(self):
        parsed = plan_review.parse_assessment(assessment_json())
        assert parsed is not None
        assert parsed.accepted
        assert parsed.verified
        assert parsed.criteria_met == "satisfied"
        assert parsed.evidence == "shown"

    def test_tolerates_fenced_json(self):
        parsed = plan_review.parse_assessment(f"```json\n{assessment_json()}\n```")
        assert parsed is not None and parsed.accepted

    def test_indeterminate_accept_is_not_verified(self):
        parsed = plan_review.parse_assessment(
            assessment_json(criteria_met="indeterminate", evidence="asserted")
        )
        assert parsed is not None
        assert parsed.accepted
        assert not parsed.verified

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "{}",
            json.dumps({"criteria_met": "satisfied"}),
            assessment_json(outcome="maybe"),
            assessment_json(criteria_met="unclear"),
            assessment_json(evidence="probable"),
            assessment_json(rationale=""),
            assessment_json(rationale="   "),
            json.dumps(["accept"]),
            None,
            7,
        ],
    )
    def test_rejects_every_deviation(self, raw):
        assert plan_review.parse_assessment(raw) is None


class TestCircuitBreaker:
    def test_accepts_after_two_consecutive_rejections_of_the_same_step(self):
        session = SimpleNamespace()
        plan_review.record_rejection(session, "run-1", "3", "no test run")
        assert not plan_review.breaker_should_accept(session, "run-1", "3")
        plan_review.record_rejection(session, "run-1", "3", "still no test run")
        assert plan_review.breaker_should_accept(session, "run-1", "3")
        assert (
            plan_review.last_rejection_rationale(session, "run-1", "3")
            == "still no test run"
        )

    def test_accept_resets_the_counter(self):
        session = SimpleNamespace()
        plan_review.record_rejection(session, "run-1", "3", "r")
        plan_review.record_rejection(session, "run-1", "3", "r")
        plan_review.record_accept(session, "run-1", "3")
        plan_review.record_rejection(session, "run-1", "3", "r")
        assert not plan_review.breaker_should_accept(session, "run-1", "3")

    def test_a_different_step_resets_the_counter(self):
        session = SimpleNamespace()
        plan_review.record_rejection(session, "run-1", "3", "r")
        plan_review.record_rejection(session, "run-1", "3", "r")
        # Reviewing another step clears the streak — the breaker breaks a
        # disagreement loop over ONE step, not rejections across the plan.
        plan_review.record_rejection(session, "run-1", "4", "other")
        assert not plan_review.breaker_should_accept(session, "run-1", "4")
        assert not plan_review.breaker_should_accept(session, "run-1", "3")

    def test_a_new_run_starts_clean(self):
        session = SimpleNamespace()
        plan_review.record_rejection(session, "run-1", "3", "r")
        plan_review.record_rejection(session, "run-1", "3", "r")
        assert not plan_review.breaker_should_accept(session, "run-2", "3")


@pytest.mark.asyncio
class TestReviewCompletion:
    async def test_returns_none_without_a_service(self):
        assert (
            await plan_review.review_completion(
                None, SimpleNamespace(id="s"), request()
            )
            is None
        )

    async def test_returns_none_on_timeout(self, monkeypatch):
        async def slow_review(service, session, req):
            await asyncio.sleep(60)

        monkeypatch.setattr(plan_review, "_review", slow_review)
        result = await plan_review.review_completion(
            SimpleNamespace(), SimpleNamespace(id="s"), request(), timeout=0.05
        )
        assert result is None

    async def test_returns_none_on_exception(self, monkeypatch):
        async def broken_review(service, session, req):
            raise RuntimeError("model exploded")

        monkeypatch.setattr(plan_review, "_review", broken_review)
        result = await plan_review.review_completion(
            SimpleNamespace(), SimpleNamespace(id="s"), request()
        )
        assert result is None

    async def test_judge_output_is_strict_parsed(self, monkeypatch):
        """End-to-end through _review with run_agent_once mocked."""
        outputs = iter([assessment_json(), "utter nonsense"])

        monkeypatch.setattr(
            plan_review,
            "_resolve_judge_model",
            lambda service, session: ("judge-model", None, None),
        )

        async def fake_run_agent_once(spec, prompt, **kwargs):
            assert kwargs["max_turns"] == 1
            assert "pytest -q passes" in prompt
            return next(outputs)

        import omniagents.core.runtime.ephemeral as ephemeral

        monkeypatch.setattr(ephemeral, "run_agent_once", fake_run_agent_once)

        parsed = await plan_review.review_completion(
            SimpleNamespace(get_session_history=None),
            SimpleNamespace(id="s"),
            request(),
        )
        assert parsed is not None and parsed.verified

        malformed = await plan_review.review_completion(
            SimpleNamespace(get_session_history=None),
            SimpleNamespace(id="s"),
            request(),
        )
        assert malformed is None

    async def test_prompt_carries_both_criteria_when_original_present(
        self, monkeypatch
    ):
        """A mid-step-edited step shows the judge the current AND the
        original criteria, clearly labeled; an unedited one only the
        current."""
        prompts = []

        monkeypatch.setattr(
            plan_review,
            "_resolve_judge_model",
            lambda service, session: ("judge-model", None, None),
        )

        async def fake_run_agent_once(spec, prompt, **kwargs):
            prompts.append(prompt)
            return assessment_json()

        import omniagents.core.runtime.ephemeral as ephemeral

        monkeypatch.setattr(ephemeral, "run_agent_once", fake_run_agent_once)

        edited = plan_review.PlanReviewRequest(
            step_id="3",
            subject="Run the tests",
            description="Run the suite and confirm green.",
            exit_criteria="pytest -q passes on the happy path",
            original_exit_criteria="pytest -q passes",
            plan_id="itm_plan",
            scope="main",
        )
        parsed = await plan_review.review_completion(
            SimpleNamespace(get_session_history=None), SimpleNamespace(id="s"), edited
        )
        assert parsed is not None
        prompt = prompts[0]
        assert "pytest -q passes on the happy path" in prompt
        assert "original_exit_criteria_before_mid_step_edit" in prompt
        assert '"pytest -q passes"' in prompt
        assert "edited while the step was in progress" in prompt

        await plan_review.review_completion(
            SimpleNamespace(get_session_history=None),
            SimpleNamespace(id="s"),
            request(),
        )
        assert "original_exit_criteria_before_mid_step_edit" not in prompts[1]

    async def test_serializes_reviews_per_session(self, monkeypatch):
        active = 0
        peak = 0

        async def counting_review(service, session, req):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return plan_review.parse_assessment(assessment_json())

        monkeypatch.setattr(plan_review, "_review", counting_review)
        session = SimpleNamespace(id="s")
        results = await asyncio.gather(
            *(
                plan_review.review_completion(SimpleNamespace(), session, request())
                for _ in range(4)
            )
        )
        assert all(result is not None and result.accepted for result in results)
        assert peak == 1


@pytest.mark.asyncio
class TestReviewerToolset:
    """The bounded agentic run: read-only tools bound via the workspace
    contextvar the doer's run leased, tool-less degradation without one."""

    def _install_judge_capture(self, monkeypatch, outputs):
        captured = []

        monkeypatch.setattr(
            plan_review,
            "_resolve_judge_model",
            lambda service, session: ("judge-model", None, None),
        )

        results = iter(outputs)

        async def fake_run_agent_once(spec, prompt, **kwargs):
            captured.append((spec, kwargs))
            return next(results)

        import omniagents.core.runtime.ephemeral as ephemeral

        monkeypatch.setattr(ephemeral, "run_agent_once", fake_run_agent_once)
        return captured

    async def test_bound_workspace_carries_readonly_toolset(
        self, monkeypatch, tmp_path
    ):
        captured = self._install_judge_capture(monkeypatch, [assessment_json()])

        with bound_workspace(tmp_path) as ws:
            parsed = await plan_review.review_completion(
                SimpleNamespace(get_session_history=None),
                SimpleNamespace(id="s"),
                request(),
            )
        assert parsed is not None and parsed.verified

        spec, kwargs = captured[0]
        assert kwargs["max_turns"] == plan_review.PLAN_REVIEW_MAX_TURNS
        assert kwargs["workspace_root"] == ws.root
        names = [tool.name for tool in spec.available_tools]
        assert names == ["read_file", "list_directory"]

    async def test_toolset_has_no_write_edit_execute_or_web(self):
        names = {tool.name for tool in plan_review.reviewer_tools()}
        forbidden = {
            "write_file",
            "edit_file",
            "apply_patch",
            "execute_bash",
            "run_python",
            "web_fetch",
            "web_search",
            "download_file",
        }
        assert names.isdisjoint(forbidden)

    async def test_without_bound_workspace_runs_tool_less(self, monkeypatch):
        captured = self._install_judge_capture(monkeypatch, [assessment_json()])

        parsed = await plan_review.review_completion(
            SimpleNamespace(get_session_history=None),
            SimpleNamespace(id="s"),
            request(),
        )
        assert parsed is not None and parsed.accepted

        spec, kwargs = captured[0]
        assert spec.available_tools == []
        assert kwargs["max_turns"] == 1
        assert kwargs["workspace_root"] is None

    async def test_verdict_after_tool_use_still_strict_parses(
        self, monkeypatch, tmp_path
    ):
        """The final text after an agentic (tool-using) run keeps the bare
        JSON contract: a fenced verdict (the one tolerated wrap) parses,
        prose around the verdict is still a deviation → None."""
        captured = self._install_judge_capture(
            monkeypatch,
            [
                f"```json\n{assessment_json()}\n```",
                f"I inspected the workspace. Verdict: {assessment_json()}",
            ],
        )

        with bound_workspace(tmp_path):
            parsed = await plan_review.review_completion(
                SimpleNamespace(get_session_history=None),
                SimpleNamespace(id="s"),
                request(),
            )
            assert parsed is not None and parsed.accepted

            malformed = await plan_review.review_completion(
                SimpleNamespace(get_session_history=None),
                SimpleNamespace(id="s"),
                request(),
            )
            assert malformed is None
        assert len(captured) == 2


def test_review_run_budgets():
    assert plan_review.PLAN_REVIEW_TIMEOUT_SECONDS == 120.0
    assert plan_review.PLAN_REVIEW_MAX_TURNS == 8


def test_policy_document_covers_readonly_workspace_inspection():
    text = plan_review.plan_review_policy_text()
    assert "read_file" in text
    assert "list_directory" in text
    assert "zero tool calls" in text
    assert "never attempt to run commands" in text
    assert "DOER's own run" in text


def test_policy_document_ships_and_names_the_contract():
    text = plan_review.plan_review_policy_text()
    assert "criteria_met" in text
    assert "evidence" in text
    assert "accept | reject" in text
    assert "process check, not a security gate" in text
    assert "When in doubt, accept" in text


def test_policy_document_covers_mid_step_criteria_edits():
    text = plan_review.plan_review_policy_text()
    assert "Criteria edited mid-step" in text
    assert "original_exit_criteria_before_mid_step_edit" in text
    assert "criteria-fitting" in text
