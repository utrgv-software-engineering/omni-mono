"""Env vars from the eval environment must be applied before spec load.

Spec loading reads the process environment (notably ``XDG_CONFIG_HOME``
for ``allow_user_mcp_servers``' mcp.json discovery). An environment that
stages per-scenario config and declares it via ``get_env_vars()`` needs
those values in ``os.environ`` at spec-load time — historically they were
applied only after the spec existed, which forced products to mutate
``os.environ`` inside ``setup()`` under a cross-scenario lock.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

import omniagents.core.eval.multi_turn as multi_turn
from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment

VAR = "OMNIAGENTS_TEST_EVAL_ENV_ORDERING"


class _StagingEnvironment(EvalEnvironment):
    """Minimal environment that declares an env var like a config stager."""

    def __init__(self) -> None:
        self.teardown_called = False

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        return EnvironmentContext(session_id="s", data={"staged": True})

    async def teardown(self, context: EnvironmentContext) -> None:
        self.teardown_called = True

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        return {VAR: "staged-value"}


def test_env_vars_applied_before_spec_load(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    seen_at_spec_load: Dict[str, Any] = {}

    def fake_registries(*args: Any, **kwargs: Any):
        return {}, {}, {}

    def fake_user_mcp(*args: Any, **kwargs: Any):
        # This is the spec-load-time env read the ordering exists for.
        seen_at_spec_load["at_user_mcp"] = os.environ.get(VAR)
        return None

    def fake_load_spec(*args: Any, **kwargs: Any):
        seen_at_spec_load["at_spec_load"] = os.environ.get(VAR)
        raise RuntimeError("stop after recording")

    monkeypatch.setattr(multi_turn, "_build_registries_for_eval", fake_registries)
    monkeypatch.setattr(multi_turn, "_load_user_mcp_for_eval", fake_user_mcp)
    monkeypatch.setattr(multi_turn, "load_agent_spec_from_yaml", fake_load_spec)

    env = _StagingEnvironment()
    result = asyncio.run(
        multi_turn.run_scenario(
            "agent.yml",
            {"name": "ordering", "prompt": "hi"},
            env,
        )
    )

    # The env var was visible during user-MCP discovery and spec load...
    assert seen_at_spec_load == {
        "at_user_mcp": "staged-value",
        "at_spec_load": "staged-value",
    }
    # ...the scenario surfaced the (deliberate) failure...
    assert result["error"]["type"] == "run_failed"
    # ...and the runner restored the environment and tore down.
    assert os.environ.get(VAR) is None
    assert env.teardown_called
