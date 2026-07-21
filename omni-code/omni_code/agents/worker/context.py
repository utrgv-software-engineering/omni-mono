"""
Context factory for the worker agent.

Re-uses the main coding agent's context factory so workers get the same
workspace info, current date/time, AGENTS.md files, etc., and layers
omni-code's coding-specific delta onto the framework's default worker
instructions (``DEFAULT_WORKER_INSTRUCTIONS``) via the template's
``additional_instructions`` hook. The worker spec deliberately declares
no instructions of its own — see agents/worker/agent.yml.
"""

from __future__ import annotations

from typing import Any, Dict

from omniagents import context_factory

from omni_code.agents.omni.context import build_coding_context as _build_coding_context

#: omni-code's coding delta over the framework's generic worker prompt:
#: everything else (focus, parallel tool calls, absolute paths, task-list
#: scoping, when-stuck protocol, summary structure) is framework-owned.
WORKER_CODING_DELTA = """\
- Do not commit changes to git.
- In your final summary's "What was done" section, list every file you \
created or changed by absolute path, with 1-2 sentences per file \
describing the modification."""


@context_factory
def build_worker_context(variables: Dict[str, Any] = None) -> Dict[str, Any]:
    context = _build_coding_context(variables)
    extra = context.get("additional_instructions")
    if isinstance(extra, str) and extra.strip():
        # Preserve caller-supplied additions (e.g. per-scenario eval
        # variables) after the product delta.
        context["additional_instructions"] = (
            WORKER_CODING_DELTA + "\n\n" + extra
        )
    else:
        context["additional_instructions"] = WORKER_CODING_DELTA
    return context
