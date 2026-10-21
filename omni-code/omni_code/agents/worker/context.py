"""
Context factory for the worker agent.

Re-uses the main coding agent's context factory so workers get the same
workspace info, current date/time, AGENTS.md files, etc. The worker's
system prompt lives entirely in agents/worker/instructions.md (the
generic worker protocol plus omni-code's coding delta, one file);
caller-supplied ``additional_instructions`` (e.g. per-scenario eval
variables) pass through untouched and render via the template's
``{% if additional_instructions %}`` hook.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from omniagents import context_factory

from omni_code.agents.omni.context import build_coding_context as _build_coding_context


@context_factory
def build_worker_context(variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _build_coding_context(variables)
