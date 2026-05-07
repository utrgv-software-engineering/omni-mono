"""Predicates: user-defined callables that gate tool/handoff/agent-tool visibility.

The SDK accepts ``is_enabled: bool | Callable[[RunContextWrapper, AgentBase], MaybeAwaitable[bool]]``
on FunctionTool, Handoff, and the ``Agent.as_tool()`` factory. This package lets users
declare such predicates in YAML by name and have them auto-discovered and resolved.

Usage::

    # in <agent_dir>/predicates/my_module.py
    from omniagents.core.predicates import is_enabled_predicate

    @is_enabled_predicate
    def is_premium(ctx, agent):
        return getattr(ctx.context, "tier", "free") == "premium"

    # in agent.yml
    tools:
      - hosted: web_search
        is_enabled: is_premium
"""

from .discovery import (
    is_enabled_predicate,
    discover_predicates_in_dir,
    resolve_predicate,
)

__all__ = [
    "is_enabled_predicate",
    "discover_predicates_in_dir",
    "resolve_predicate",
]
