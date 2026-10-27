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
    """codex's parse_guardian_assessment."""

    def test_parses_strict_json(self):
        parsed = guardian.parse_assessment(assessment_json())
        assert parsed is not None
        assert parsed.allowed
        assert parsed.risk_level == "low"
        assert parsed.user_authorization == "high"

    def test_extracts_embedded_json(self):
        parsed = guardian.parse_assessment(f"preface {assessment_json()} after")
        assert parsed is not None and parsed.allowed

    def test_a_bare_allow_is_low_risk(self):
        parsed = guardian.parse_assessment('{"outcome":"allow"}')
        assert (parsed.risk_level, parsed.user_authorization) == ("low", "unknown")
        assert parsed.rationale == "Auto-review returned a low-risk allow decision."

    def test_a_bare_deny_is_high_risk(self):
        parsed = guardian.parse_assessment('{"outcome":"deny"}')
        assert parsed.risk_level == "high" and not parsed.allowed

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "{}",
            json.dumps({"risk_level": "low"}),
            assessment_json(outcome="maybe"),
            assessment_json(risk_level="extreme"),
            assessment_json(user_authorization="explicit"),
            json.dumps(["allow"]),
            None,
        ],
    )
    def test_rejects_every_deviation(self, raw):
        assert guardian.parse_assessment(raw) is None


class TestCircuitBreaker:
    """codex's GuardianRejectionCircuitBreaker: too many denials interrupt."""

    def test_interrupts_after_consecutive_denials_once_per_run(self):
        session = SimpleNamespace()
        for _ in range(guardian.MAX_CONSECUTIVE_DENIALS - 1):
            assert not guardian.record_denial(session, "run-1")
        assert guardian.record_denial(session, "run-1")
        assert not guardian.record_denial(session, "run-1")  # once
        # A new run starts clean.
        assert not guardian.record_denial(session, "run-2")

    def test_allow_resets_the_consecutive_count(self):
        session = SimpleNamespace()
        guardian.record_denial(session, "run-1")
        guardian.record_denial(session, "run-1")
        guardian.record_allow(session, "run-1")
        assert not guardian.record_denial(session, "run-1")

    def test_interrupts_after_too_many_recent_denials(self):
        session = SimpleNamespace()
        tripped = False
        for _ in range(guardian.MAX_RECENT_DENIALS):
            tripped = guardian.record_denial(session, "run-1") or tripped
            guardian.record_allow(session, "run-1")
        assert tripped


def _request(**overrides):
    return guardian.GuardianRequest(
        **{"kind": "tool", "tool_name": "t", "arguments": "", **overrides}
    )


@pytest.mark.asyncio
class TestReviewApproval:
    """Fail closed, as codex does: only a valid allow runs the action."""

    async def test_a_timeout_denies_with_codexs_timeout_instructions(self, monkeypatch):
        async def slow_review(service, session, request):
            await asyncio.sleep(60)

        monkeypatch.setattr(guardian, "_review", slow_review)
        decision = await guardian.review_approval(
            SimpleNamespace(), SimpleNamespace(id="s"), _request(), timeout=0.05
        )
        assert decision.status == "timed_out" and not decision.approved
        assert guardian.TIMEOUT_INSTRUCTIONS in decision.message

    async def test_an_error_denies_with_codexs_failure_instructions(self, monkeypatch):
        async def broken_review(service, session, request):
            raise RuntimeError("model exploded")

        monkeypatch.setattr(guardian, "_review", broken_review)
        decision = await guardian.review_approval(
            SimpleNamespace(), SimpleNamespace(id="s"), _request()
        )
        assert decision.status == "failed" and not decision.approved
        assert decision.message.startswith(
            "Automatic approval review failed: model exploded"
        )
        assert guardian.REVIEW_FAILURE_INSTRUCTIONS in decision.message

    async def test_no_valid_assessment_denies(self, monkeypatch):
        async def no_answer(service, session, request):
            return None

        monkeypatch.setattr(guardian, "_review", no_answer)
        decision = await guardian.review_approval(
            SimpleNamespace(), SimpleNamespace(id="s"), _request()
        )
        assert decision.status == "failed"

    async def test_a_denial_carries_codexs_rejection(self, monkeypatch):
        async def deny(service, session, request):
            return guardian.parse_assessment(
                assessment_json(outcome="deny", rationale="Pushes PHI.")
            )

        monkeypatch.setattr(guardian, "_review", deny)
        decision = await guardian.review_approval(
            SimpleNamespace(), SimpleNamespace(id="s"), _request()
        )
        assert decision.status == "denied"
        assert decision.message == (
            "This action was rejected due to unacceptable risk.\n"
            f"Reason: Pushes PHI.\n{guardian.REJECTION_INSTRUCTIONS}"
        )

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
        decisions = await asyncio.gather(
            *(
                guardian.review_approval(SimpleNamespace(), session, _request())
                for _ in range(4)
            )
        )
        assert all(decision.approved for decision in decisions)
        assert peak == 1


class TestInstructions:
    """codex's policy template, organization policy and output contract."""

    def test_default_is_codexs_template_and_policy(self):
        text = guardian.guardian_instructions()
        assert text.startswith("You are judging one planned coding-agent action.")
        assert "## Environment Profile" in text  # codex's policy.md
        assert "{{ tenant_policy_config }}" not in text
        assert "{{ extra_policy }}" not in text
        assert text.rstrip().endswith("}")  # the output contract closes it
        assert '{"outcome":"allow"}' in text

    def test_the_reviewer_is_told_it_can_only_read_files(self):
        text = guardian.guardian_instructions()
        assert "only read files, with `read_file` and `list_directory`" in text
        assert "only run read-only commands" not in text

    def test_configured_policies_replace_and_extend_the_default(self, monkeypatch):
        monkeypatch.setattr(
            "omniagents.core.agents.approval_policy.guardian_policy_config",
            lambda service: (
                "Trusted: github.com/acme. PHI never leaves the EHR.",
                "Never post comments.",
            ),
        )
        text = guardian.guardian_instructions(SimpleNamespace())
        assert "PHI never leaves the EHR." in text
        assert "Never post comments." in text
        assert "## Environment Profile" not in text


@pytest.mark.asyncio
class TestReviewRequest:
    """codex's review request layout."""

    @staticmethod
    def _service(items, instructions="You are the implementer. Open a PR."):
        async def history(session_id):
            return items

        return SimpleNamespace(
            get_session_history=history,
            spec=SimpleNamespace(get_agent_instructions=lambda: instructions),
        )

    async def test_layout(self):
        items = [
            {"role": "user", "content": "Deliver #12 as a PR ready for review."},
            {
                "type": "function_call",
                "name": "execute_bash",
                "call_id": "c1",
                "arguments": '{"command": "git status"}',
            },
            {"type": "function_call_output", "call_id": "c1", "output": "clean"},
        ]
        text = await guardian.build_review_request(
            self._service(items),
            SimpleNamespace(id="s"),
            _request(
                tool_name="execute_bash",
                arguments='{"command": "git push"}',
                workspace_root="/workspace",
                environment_kind="container",
                environment_has_network=True,
            ),
        )
        assert text.index("<environment_context>") < text.index(
            "The following is the agent history"
        )
        assert "[1] developer: You are the implementer. Open a PR." in text
        assert "[2] user: Deliver #12 as a PR ready for review." in text
        assert '[3] tool execute_bash call: {"command": "git status"}' in text
        assert "[4] tool execute_bash result: clean" in text
        assert text.index(">>> TRANSCRIPT END") < text.index(
            ">>> APPROVAL REQUEST START"
        )
        action = text.split("Planned action JSON:\n", 1)[1].split("\n>>> APPROVAL", 1)[
            0
        ]
        assert json.loads(action) == {
            "tool": "execute_bash",
            "kind": "tool",
            "arguments": {"command": "git push"},
            "cwd": "/workspace",
        }

    async def test_the_task_and_instructions_survive_a_long_run(self):
        items = [{"role": "user", "content": "Deliver #12 as a PR."}]
        for i in range(guardian.MAX_TRANSCRIPT_ENTRIES):
            items.append(
                {
                    "type": "function_call",
                    "name": "x",
                    "call_id": str(i),
                    "arguments": f"step {i}",
                }
            )
            items.append(
                {"type": "function_call_output", "call_id": str(i), "output": "ok"}
            )
        text = await guardian.build_review_request(
            self._service(items), SimpleNamespace(id="s"), _request()
        )
        assert ">>> RETAINED USER INSTRUCTIONS START" in text
        assert "user: Deliver #12 as a PR." in text
        assert "developer: You are the implementer. Open a PR." in text
        assert "step 0" not in text

    async def test_the_agents_latest_rendered_instructions_are_used(self):
        from omniagents.core.agents.rendered_instructions import record_instructions

        record_instructions("rendered-session", "Rendered role with AGENTS.md")
        text = await guardian.build_review_request(
            self._service([]), SimpleNamespace(id="rendered-session"), _request()
        )
        assert "[1] developer: Rendered role with AGENTS.md" in text


@pytest.mark.asyncio
class TestReviewModelResolution:
    """The guardian reviews on the provider the agent itself is configured with."""

    @staticmethod
    def _patch(monkeypatch, *, runtime, built_provider="spec-provider"):
        import omniagents.core.models as models
        import omniagents.core.runtime.ephemeral as ephemeral
        from omniagents.core.agents import run_options

        captured = {}

        async def fake_run_agent_once(spec, prompt, **kwargs):
            captured["spec"] = spec
            captured.update(kwargs)
            return assessment_json()

        async def fake_prompt(service, session, request):
            return "review this"

        monkeypatch.setattr(
            models,
            "resolve_service_model_runtime",
            lambda service, session: ("gpt-x", runtime),
        )
        # The spec's OAuth provider and a store-resolved runtime build through
        # the same function; tell them apart by the config each passes
        monkeypatch.setattr(
            run_options,
            "build_model_provider",
            lambda config: (
                built_provider
                if (config or {}).get("type") == "openai-oauth"
                else "store-provider"
            ),
        )
        monkeypatch.setattr(ephemeral, "run_agent_once", fake_run_agent_once)
        monkeypatch.setattr(guardian, "build_review_request", fake_prompt)
        monkeypatch.setattr(
            "omniagents.core.agents.approval_policy.guardian_model_ref",
            lambda service: None,
        )
        return captured

    @staticmethod
    def _request():
        return guardian.GuardianRequest(
            kind="tool", tool_name="run_command", arguments="{}"
        )

    async def test_falls_back_to_the_spec_provider_and_settings(self, monkeypatch):
        captured = self._patch(monkeypatch, runtime={})
        settings = {
            "store": False,
            "extra_body": {"include": ["reasoning.encrypted_content"]},
        }
        service = SimpleNamespace(
            spec=SimpleNamespace(
                model_provider_config={"type": "openai-oauth"}, model_settings=settings
            )
        )

        assessment = await guardian._review(service, SimpleNamespace(), self._request())

        assert assessment is not None and assessment.allowed
        assert captured["model_provider"] == "spec-provider"
        assert captured["model"] == "gpt-x"
        assert captured["spec"].model_settings == settings
        assert captured["env_overrides"] == {}
        # Read-only file tools; no command tool.
        assert [t.name for t in captured["spec"].available_tools] == [
            "read_file",
            "list_directory",
        ]

    async def test_a_store_resolved_provider_wins(self, monkeypatch):
        captured = self._patch(
            monkeypatch,
            runtime={"provider": "openai", "model": "gpt-x", "api_key": "sk-test"},
        )
        service = SimpleNamespace(
            spec=SimpleNamespace(
                model_provider_config={"type": "openai-oauth"},
                model_settings={"store": False},
            )
        )

        await guardian._review(service, SimpleNamespace(), self._request())

        assert captured["model_provider"] == "store-provider"
        assert captured["spec"].model_settings is None

    async def test_without_a_spec_provider_keeps_the_default(self, monkeypatch):
        captured = self._patch(monkeypatch, runtime={})
        service = SimpleNamespace(
            spec=SimpleNamespace(model_provider_config=None, model_settings=None)
        )

        await guardian._review(service, SimpleNamespace(), self._request())

        assert captured["model_provider"] is None
