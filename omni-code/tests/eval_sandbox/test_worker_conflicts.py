"""Sandbox evaluation: worker file conflict detection.

These tests run the full orchestrator + worker system inside a Docker
container and verify that concurrent workers don't corrupt shared files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from environments.sandbox_env import SandboxEvalEnvironment, run_prompt_in_sandbox
from omniagents.core.evaluation import build_eval_context, run_measures

from .conftest import skip_no_docker, skip_no_image


@skip_no_docker
@skip_no_image
@pytest.mark.asyncio
@pytest.mark.timeout(300)
class TestWorkerConflicts:
    """Test that workers don't corrupt files when running concurrently."""

    async def _run_and_verify(
        self, sandbox_env: SandboxEvalEnvironment, scenario: dict
    ) -> dict:
        ctx = await sandbox_env.setup(scenario)
        try:
            result = await run_prompt_in_sandbox(
                ws_url=ctx["ws_url"],
                prompt=scenario["prompt"],
                timeout=sandbox_env.timeout_seconds,
                auto_approve=True,
            )

            eval_ctx = build_eval_context(
                history=result.get("history", []),
                metadata={
                    "scenario": scenario,
                    "environment_context": ctx.data,
                },
            )

            # Attach environment for verify() calls
            eval_ctx._environment = sandbox_env
            eval_ctx._environment_context = ctx

            import evaluations.measures  # noqa: F401

            measure_names = scenario.get("measures", [])
            results = run_measures(eval_ctx, measures=measure_names)

            # Also run filesystem verification if specified
            verify_files = scenario.get("expect", {}).get("verify_files")
            verify_result = None
            if verify_files:
                verify_result = await sandbox_env.verify(ctx, verify_files)

            return {
                "scenario": scenario["name"],
                "measures": results,
                "verify": verify_result,
            }
        finally:
            await sandbox_env.teardown(ctx)

    async def test_independent_files_no_corruption(
        self, sandbox_env, sandbox_scenarios
    ):
        scenarios = sandbox_scenarios.get("worker_file_conflicts", [])
        scenario = next(
            (s for s in scenarios if s["name"] == "workers_on_independent_files"),
            None,
        )
        if not scenario:
            pytest.skip("scenario not found")

        result = await self._run_and_verify(sandbox_env, scenario)

        # Check measures
        for name, mr in result["measures"].items():
            assert mr["passed"], f"{name} failed: {mr.get('reason')}"

        # Check filesystem
        if result["verify"]:
            assert result["verify"]["success"], (
                f"file verification failed: {result['verify']['results']}"
            )

    async def test_same_module_conflict_prevented(
        self, sandbox_env, sandbox_scenarios
    ):
        scenarios = sandbox_scenarios.get("worker_file_conflicts", [])
        scenario = next(
            (s for s in scenarios if s["name"] == "concurrent_workers_same_module"),
            None,
        )
        if not scenario:
            pytest.skip("scenario not found")

        result = await self._run_and_verify(sandbox_env, scenario)

        # The key check: either no overlapping workers, or file not corrupted
        for name, mr in result["measures"].items():
            assert mr["passed"], f"{name} failed: {mr.get('reason')}"
