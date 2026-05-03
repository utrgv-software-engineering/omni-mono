"""Re-export the production model_config_resolver so this test-only
agent uses the same credentials as the production `omni` agent."""

from __future__ import annotations

from omni_agents.omni.context import resolve_default_model  # noqa: F401
