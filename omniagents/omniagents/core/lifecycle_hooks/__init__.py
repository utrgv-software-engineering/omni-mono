"""Discovery layer for SDK ``RunHooks`` and ``AgentHooks`` instances.

These differ from OmniAgents' own ``runtime_hooks`` (audit/error/token
events): the SDK lifecycle hooks fire during ``Runner.run`` and per-agent
construction (``on_llm_start``, ``on_agent_end``, etc.).
"""

from omniagents.core.lifecycle_hooks.discovery import (
    discover_lifecycle_hooks_in_dir,
)

__all__ = ["discover_lifecycle_hooks_in_dir"]
