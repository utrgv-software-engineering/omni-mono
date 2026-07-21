"""Framework-owned default instructions for worker sub-agents.

Worker instructions are ~90% generic subagent protocol — stay focused,
parallel independent tool calls, absolute paths, private task-list
scoping, blocker/when-stuck protocol, structured output format — so the
framework ships that base and products only layer their delta (via their
own ``instructions``/``instructions_file`` or the template's
``additional_instructions`` / ``agents_md_block`` variables).

The canonical template lives in ``worker_instructions.md`` next to this
module (products can also point an ``instructions_file`` at
:func:`default_worker_instructions_path`). It is a Jinja template
rendered with silent-undefined semantics by the agent runtime, so the
optional variables simply disappear when a product does not supply them.

``WorkerManager`` applies :func:`apply_default_worker_instructions` to
every spawned worker spec (the same seam as ``_default_worker_safety``):
a worker agent with no instructions of its own — or only the YAML
loader's generic "helpful assistant" fallback — gets the framework
default; a product-authored worker prompt is never touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_TEMPLATE_PATH = Path(__file__).with_name("worker_instructions.md")

#: The YAML spec loader's fallback when an agent config declares neither
#: ``instructions`` nor ``instructions_file`` — for a spawned worker that
#: counts as "no instructions".
_LOADER_FALLBACK_INSTRUCTIONS = "You are a helpful assistant."


def default_worker_instructions_path() -> str:
    """Absolute path of the packaged default worker instructions template."""
    return str(_TEMPLATE_PATH)


def _load_template() -> str:
    try:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - packaging defect fallback
        return (
            "You are **{{ worker_name }}**, an autonomous worker agent "
            "executing a focused subtask. Complete the assigned task, then "
            "produce a concise summary of what you did."
        )


#: The framework's default worker system prompt (Jinja template text).
DEFAULT_WORKER_INSTRUCTIONS: str = _load_template()


def apply_default_worker_instructions(spec: Any) -> bool:
    """Default a spawned worker's spec to the framework worker prompt.

    Applies only when the spec carries no instructions of its own: a
    missing/blank ``get_agent_instructions`` result, or exactly the YAML
    loader's generic fallback. Returns True when the default was
    installed. Never raises — defaulting must not block a spawn.
    """
    try:
        getter = getattr(spec, "get_agent_instructions", None)
        current = getter() if callable(getter) else None
        if isinstance(current, str):
            text = current.strip()
            if text and text != _LOADER_FALLBACK_INSTRUCTIONS:
                return False
        elif current is not None:
            return False
        spec.get_agent_instructions = lambda: DEFAULT_WORKER_INSTRUCTIONS
        return True
    except Exception:
        return False
