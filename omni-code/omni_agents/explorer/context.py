"""Context + model resolver for the explorer sub-agent.

When the explorer is exposed as a function tool via ``agent_tools:``,
the omniagents loader discovers ``model_config_resolver``-decorated
callables in this directory. Re-exporting the omni agent's resolver
lets the explorer pick up the user's currently-selected model and
provider credentials from omni-code's models.json — same behavior the
hand-rolled tools/explore_tool.py used to provide.
"""

from __future__ import annotations

from omni_agents.omni.context import resolve_default_model

__all__ = ["resolve_default_model"]
