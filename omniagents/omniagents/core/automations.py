"""Automations: what starts an agent.

An automation is ``automations/<name>/automation.md`` in a project: YAML
frontmatter (``description``, ``triggers``, and an optional ``agent``; the
project's entrypoint when absent) followed by the instruction the agent is
started with. A run started by an automation receives the instruction and
the event that fired it, as rendered by :meth:`Automation.run_input`, so a
test that replays an event and a production run see the same input.

The file is a Jinja template with ``project`` in scope
(:mod:`omniagents.core.project.values`), so a trigger names the project's
own account and label rather than a copy of them::

    filter:
      repos: ["*/api-service"]
      mentioned: ["{{ project.github.account }}"]
      labels: ["{{ project.github.routing_label }}"]

A name that is not defined is an error, not an empty string: a trigger
that silently matched nothing would never say so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

__all__ = [
    "Automation",
    "AutomationError",
    "automations_dir",
    "list_automations",
    "load_automation",
]

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


class AutomationError(ValueError):
    """An automation is missing or malformed."""


@dataclass(frozen=True)
class Automation:
    """One automation file."""

    name: str
    path: Path
    instruction: str
    description: str = ""
    agent: Optional[str] = None
    triggers: List[Dict[str, Any]] = field(default_factory=list)
    self_improvement: bool = False

    def run_input(self, event: Any = None, context: Optional[str] = None) -> str:
        """The input a run started by this automation receives: its instruction,
        the event, and the context the event's source wrote for the agent
        (Linear's ``promptContext``), verbatim."""
        text = self.instruction.strip()
        if event is not None:
            payload = json.dumps(event, indent=2, ensure_ascii=False, default=str)
            text = f"{text}\n\n## Event\n\n```json\n{payload}\n```"
        if context:
            text = f"{text}\n\n## Context\n\n{context.strip()}"
        return text


def automations_dir(project_root: Path) -> Path:
    """Where a project's automations live."""
    return Path(project_root) / "automations"


def load_automation(project_root: Path, name: str) -> Automation:
    """Read ``automations/<name>/automation.md``."""
    path = automations_dir(project_root) / name / "automation.md"
    if not path.is_file():
        available = [a.name for a in list_automations(project_root)]
        raise AutomationError(
            f"No automation {name!r} at {path}. Available: {available}"
        )
    match = _FRONTMATTER.match(_render(project_root, path))
    if not match:
        raise AutomationError(f"{path}: expected YAML frontmatter between '---' lines")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise AutomationError(f"{path}: invalid frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise AutomationError(f"{path}: frontmatter must be a mapping")
    triggers = meta.get("triggers") or []
    if not isinstance(triggers, list) or not all(isinstance(t, dict) for t in triggers):
        raise AutomationError(f"{path}: 'triggers' must be a list of mappings")
    agent = meta.get("agent")
    if "self_improvement" in meta and not isinstance(meta["self_improvement"], bool):
        raise AutomationError(f"{path}: 'self_improvement' must be a boolean")
    if agent is not None and (not isinstance(agent, str) or not agent.strip()):
        raise AutomationError(f"{path}: 'agent' must name a project agent")
    instruction = match.group(2).strip()
    if not instruction:
        raise AutomationError(
            f"{path}: the instruction (the text after the frontmatter) is empty"
        )
    return Automation(
        name=name,
        path=path,
        instruction=instruction,
        description=str(meta.get("description") or ""),
        agent=agent.strip() if isinstance(agent, str) else None,
        triggers=list(triggers),
        self_improvement=meta.get("self_improvement", False),
    )


def _render(project_root: Path, path: Path) -> str:
    """The automation file with ``project``'s values in it."""
    import jinja2

    from omniagents.core.project.values import project_values_at

    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, keep_trailing_newline=True
    )
    env.filters["tojson"] = lambda value: json.dumps(value, ensure_ascii=False)
    try:
        template = env.from_string(path.read_text(encoding="utf-8"))
        return template.render(project=project_values_at(project_root))
    except jinja2.TemplateError as exc:
        raise AutomationError(f"{path}: {exc}") from exc


def list_automations(project_root: Path) -> List[Automation]:
    """Every automation in the project, by name. Malformed ones raise."""
    root = automations_dir(project_root)
    if not root.is_dir():
        return []
    return [
        load_automation(project_root, d.name)
        for d in sorted(root.iterdir())
        if (d / "automation.md").is_file()
    ]
