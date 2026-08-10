"""Completion-review routing: the pure truth table and the config layering."""

import pytest

from omniagents.core.agents.workflow_policy import (
    effective_workflow_reviewer,
    resolve_completion_route,
)
from omniagents.core.conversation.plans import PlanStep


def step(criteria=""):
    return PlanStep(id="1", subject="a", exit_criteria=criteria)


class TestResolveCompletionRoute:
    @pytest.mark.parametrize(
        ("reviewer", "criteria", "expected"),
        [
            # No criteria: never reviewed, whoever the reviewer is.
            ("guardian", "", "off"),
            ("off", "", "off"),
            ("guardian", "   ", "off"),
            # Criteria present: the reviewer decides.
            ("guardian", "tests pass", "guardian"),
            ("off", "tests pass", "off"),
        ],
    )
    def test_truth_table(self, reviewer, criteria, expected):
        assert resolve_completion_route(reviewer, step(criteria)) == expected

    def test_unknown_reviewer_falls_back_to_guardian(self):
        # Same posture as effective_workflow_reviewer: an unrecognized
        # value never silently disables review.
        assert resolve_completion_route("bogus", step("tests pass")) == "guardian"


class _FakeConfigStore:
    def __init__(self, values):
        self._values = values

    def effective_value(self, key):
        if key in self._values:
            return True, self._values[key]
        return False, None


class _FakeService:
    def __init__(self, values):
        self._store = _FakeConfigStore(values)

    def _layered_config_store(self):
        return self._store


class _Session:
    def __init__(self, workflow_reviewer=None):
        self.workflow_reviewer = workflow_reviewer


class TestEffectiveWorkflowReviewer:
    def test_defaults_to_guardian(self):
        assert effective_workflow_reviewer(None, None) == "guardian"
        assert effective_workflow_reviewer(_FakeService({}), _Session()) == "guardian"

    def test_config_value_applies(self):
        service = _FakeService({"workflow.completion_reviewer": "off"})
        assert effective_workflow_reviewer(service, _Session()) == "off"

    def test_session_override_wins(self):
        service = _FakeService({"workflow.completion_reviewer": "off"})
        assert effective_workflow_reviewer(service, _Session("guardian")) == "guardian"

    def test_invalid_values_fall_through(self):
        service = _FakeService({"workflow.completion_reviewer": "bogus"})
        assert effective_workflow_reviewer(service, _Session("wat")) == "guardian"
