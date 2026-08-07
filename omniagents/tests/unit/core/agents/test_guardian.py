import asyncio
import json
from types import SimpleNamespace

import pytest

from omniagents.core.agents import guardian


def assessment_json(**overrides):
    data = {
        "risk_level": "low",
        "user_authorization": "high",
        "outcome": "allow",
        "rationale": "Read-only inspection covered by the task.",
    }
    data.update(overrides)
    return json.dumps(data)


class TestParseAssessment:
    def test_parses_strict_json(self):
        parsed = guardian.parse_assessment(assessment_json())
        assert parsed is not None
        assert parsed.allowed
        assert parsed.risk_level == "low"
        assert parsed.user_authorization == "high"

    def test_tolerates_fenced_json(self):
        parsed = guardian.parse_assessment(f"```json\n{assessment_json()}\n```")
        assert parsed is not None and parsed.allowed

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "{}",
            json.dumps({"risk_level": "low"}),
            assessment_json(outcome="maybe"),
            assessment_json(risk_level="extreme"),
            assessment_json(user_authorization="explicit"),
            assessment_json(rationale=""),
            json.dumps(["allow"]),
            None,
        ],
    )
    def test_rejects_every_deviation(self, raw):
        assert guardian.parse_assessment(raw) is None


class TestCircuitBreaker:
    def test_trips_after_consecutive_denials_and_resets_per_run(self):
        session = SimpleNamespace()
        for _ in range(guardian.MAX_CONSECUTIVE_DENIALS - 1):
            guardian.record_denial(session, "run-1")
        assert not guardian.breaker_tripped(session, "run-1")
        guardian.record_denial(session, "run-1")
        assert guardian.breaker_tripped(session, "run-1")
        # A new run starts clean.
        assert not guardian.breaker_tripped(session, "run-2")

    def test_allow_resets_the_consecutive_count(self):
        session = SimpleNamespace()
        guardian.record_denial(session, "run-1")
        guardian.record_denial(session, "run-1")
        guardian.record_allow(session, "run-1")
        guardian.record_denial(session, "run-1")
        assert not guardian.breaker_tripped(session, "run-1")


@pytest.mark.asyncio
class TestReviewApproval:
    async def test_returns_none_on_timeout(self, monkeypatch):
        async def slow_review(service, session, request):
            await asyncio.sleep(60)

        monkeypatch.setattr(guardian, "_review", slow_review)
        result = await guardian.review_approval(
            SimpleNamespace(),
            SimpleNamespace(id="s"),
            guardian.GuardianRequest(kind="tool", tool_name="t", arguments=""),
            timeout=0.05,
        )
        assert result is None

    async def test_returns_none_on_exception(self, monkeypatch):
        async def broken_review(service, session, request):
            raise RuntimeError("model exploded")

        monkeypatch.setattr(guardian, "_review", broken_review)
        result = await guardian.review_approval(
            SimpleNamespace(),
            SimpleNamespace(id="s"),
            guardian.GuardianRequest(kind="tool", tool_name="t", arguments=""),
        )
        assert result is None

    async def test_serializes_reviews_per_session(self, monkeypatch):
        active = 0
        peak = 0

        async def counting_review(service, session, request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return guardian.parse_assessment(assessment_json())

        monkeypatch.setattr(guardian, "_review", counting_review)
        session = SimpleNamespace(id="s")
        request = guardian.GuardianRequest(kind="tool", tool_name="t", arguments="")
        results = await asyncio.gather(
            *(
                guardian.review_approval(SimpleNamespace(), session, request)
                for _ in range(4)
            )
        )
        assert all(result is not None and result.allowed for result in results)
        assert peak == 1


def test_policy_document_ships_and_names_the_contract():
    text = guardian.guardian_policy_text()
    assert "risk_level" in text
    assert "user_authorization" in text
    assert "allow | deny" in text
