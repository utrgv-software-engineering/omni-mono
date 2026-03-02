"""
Context factory for the worker agent.

Re-uses the main coding agent's context factory so workers get
the same workspace info, current date/time, AGENTS.md files, etc.
"""

from __future__ import annotations

from typing import Any, Dict

from omniagents import context_factory

from omni_agents.omni.context import build_coding_context as _build_coding_context


@context_factory
def build_worker_context(variables: Dict[str, Any] = None) -> Dict[str, Any]:
    return _build_coding_context(variables)
