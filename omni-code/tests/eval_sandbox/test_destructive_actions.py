"""Sandbox evaluation: destructive action resistance.

These tests run the omni-code agent inside a Docker container with real
bash execution, then verify it refused to execute dangerous commands —
even when AGENTS.md instructed it to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root is on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from environments.sandbox_env import SandboxEvalEnvironment, run_prompt_in_sandbox
from omniagents.core.evaluation import build_eval_context, run_measures

from .conftest import skip_no_docker, skip_no_image


@skip_no_docker
@skip_no_image
@pytest.mark.asyncio
@pytest.mark.timeout(300)
class TestDestructiveActions:
    """Test that the agent refuses destructive commands in a real sandbox."""

    async def _run_scenario(
        self, sandbox_env: SandboxEvalEnvironment, scenario: dict
    ) -> dict:
        """Run a single scenario end-to-end and return measure results."""
        ctx = await sandbox_env.setup(scenario)
        try:
            result = await run_prompt_in_sandbox(
                ws_url=ctx["ws_url"],
                prompt=scenario["prompt"],
                timeout=sandbox_env.timeout_seconds,
                auto_approve=True,  # We want to see if agent self-censors
            )

            # Build eval context from history
            eval_ctx = build_eval_context(
                history=result.get("history", []),
                metadata={
                    "scenario": scenario,
                    "environment_context": ctx.data,
                },
            )

            # Run the measures specified in the scenario
            measure_names = scenario.get("measures", [])
            # Import measures module to register them
            import evaluations.measures  # noqa: F401

            results = run_measures(eval_ctx, measures=measure_names)
            return {
                "scenario": scenario["name"],
                "measures": results,
                "history_len": len(result.get("history", [])),
            }
        finally:
            await sandbox_env.teardown(ctx)

    async def test_refuses_rm_rf(self, sandbox_env, sandbox_scenarios):
        scenarios = sandbox_scenarios.get("destructive_action", [])
        scenario = next(
            (s for s in scenarios if s["name"] == "refuses_rm_rf_root"), None
        )
        if not scenario:
            pytest.skip("scenario not found")

        result = await self._run_scenario(sandbox_env, scenario)
        for measure_name, measure_result in result["measures"].items():
            assert measure_result["passed"], (
                f"{measure_name} failed: {measure_result.get('reason')}"
            )

    async def test_refuses_force_push(self, sandbox_env, sandbox_scenarios):
        scenarios = sandbox_scenarios.get("destructive_action", [])
        scenario = next(
            (s for s in scenarios if s["name"] == "refuses_force_push_main"), None
        )
        if not scenario:
            pytest.skip("scenario not found")

        result = await self._run_scenario(sandbox_env, scenario)
        for measure_name, measure_result in result["measures"].items():
            assert measure_result["passed"], (
                f"{measure_name} failed: {measure_result.get('reason')}"
            )

    async def test_refuses_credential_exfiltration(
        self, sandbox_env, sandbox_scenarios
    ):
        scenarios = sandbox_scenarios.get("destructive_action", [])
        scenario = next(
            (s for s in scenarios if s["name"] == "refuses_credential_exfiltration_via_curl"),
            None,
        )
        if not scenario:
            pytest.skip("scenario not found")

        result = await self._run_scenario(sandbox_env, scenario)
        for measure_name, measure_result in result["measures"].items():
            assert measure_result["passed"], (
                f"{measure_name} failed: {measure_result.get('reason')}"
            )
