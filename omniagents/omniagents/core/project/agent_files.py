"""Editing a project agent's files: its ``agent.yml`` and its instructions.

Every editor goes through here (Studio's agent editor, an optimizer
applying improved instructions), so there is one answer to "what does
saving an agent do":

- **Only what was edited changes.** ``agent.yml`` is edited as a
  round-trip document, so comments, key order, quoting and indentation
  outside the edited keys stay as they were.
- **Nothing invalid is written.** A candidate is loaded exactly as a run
  loads it (:func:`~omniagents.core.project.runtime.build_spec_from_project`)
  before it replaces the file. Loading tolerates a misconfigured agent by
  skipping what it cannot resolve; an edit may not introduce such a
  problem (an unknown tool, capability, guardrail or agent), so a saved
  agent runs with everything it names. Problems the file already had
  before the edit do not block saving it.
- **Instructions are saved where they live**: inline ``instructions:`` when
  the file has them (they take precedence when loading), otherwise the
  ``instructions_file``.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import LiteralScalarString

from omniagents.core.project.runtime import (
    ProjectContext,
    build_spec_from_project,
    load_project,
)
from omniagents.core.utils.yaml_edit import (
    dump_document,
    load_document,
    merge_into,
    replace_file,
)

#: Keys an edit cannot set through ``changes``: the name is the agent's
#: directory key, and instructions are saved through ``instructions=``.
_RESERVED_KEYS = frozenset({"name", "instructions", "instructions_file"})


class AgentEditError(ValueError):
    """An edit was refused; ``problems`` says why, one message each."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class AgentSource:
    """An agent's files as they are on disk."""

    key: str
    path: Path
    #: ``agent.yml``, verbatim.
    yaml_text: str
    instructions: str
    #: The instructions file, or ``None`` when the instructions are inline.
    instructions_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "path": str(self.path),
            "yaml": self.yaml_text,
            "instructions": self.instructions,
            "instructions_path": (
                str(self.instructions_path) if self.instructions_path else None
            ),
        }


def _project(project_ref: str | Path) -> ProjectContext:
    return load_project(str(project_ref))


def _agent_path(project: ProjectContext, key: str) -> Path:
    path = project.agents_map.get(key)
    if path is None:
        raise FileNotFoundError(f"Agent not found in project: {key}")
    return Path(path)


def _document(text: str):
    try:
        return load_document(text)
    except (TypeError, ValueError, YAMLError) as exc:
        raise AgentEditError([f"agent.yml is not valid: {exc}"]) from exc


def _instructions_location(agent_path: Path, data: Mapping[str, Any]) -> Path | None:
    """The file holding the instructions, or ``None`` when they are inline
    (inline ``instructions:`` win over ``instructions_file`` when loading)."""
    if data.get("instructions"):
        return None
    relative = data.get("instructions_file")
    if isinstance(relative, str) and relative.strip():
        return (agent_path.parent / relative).resolve()
    return None


def read_agent(project_ref: str | Path, key: str) -> AgentSource:
    """The agent's ``agent.yml`` and instructions."""
    path = _agent_path(_project(project_ref), key)
    text = path.read_text(encoding="utf-8")
    _, data = _document(text)
    instructions_path = _instructions_location(path, data)
    if instructions_path is not None:
        instructions = (
            instructions_path.read_text(encoding="utf-8")
            if instructions_path.is_file()
            else ""
        )
    else:
        instructions = str(data.get("instructions") or "")
    return AgentSource(
        key=key,
        path=path,
        yaml_text=text,
        instructions=instructions,
        instructions_path=instructions_path,
    )


def _literal(text: str) -> Any:
    return LiteralScalarString(text) if "\n" in text else text


def _check_instructions(text: str) -> list[str]:
    from jinja2 import Environment, TemplateSyntaxError

    try:
        Environment().parse(text)
    except TemplateSyntaxError as exc:
        return [f"Instructions are not a valid template (line {exc.lineno}): {exc}"]
    return []


def _load_problems(project: ProjectContext, key: str, candidate: Path) -> list[str]:
    """What loading ``candidate`` as agent ``key`` reports, with the
    candidate's path written as the agent's own."""
    from omniagents.core.config.loader import collect_config_problems

    original = str(_agent_path(project, key))
    candidate_project = replace(
        project, agents_map={**project.agents_map, key: candidate}
    )
    with collect_config_problems() as problems:
        try:
            build_spec_from_project(candidate_project, key)
        # Loading imports the project's code; any failure refuses the edit.
        except Exception as exc:  # noqa: BLE001
            problems.append(str(exc))
    return [problem.replace(str(candidate), original) for problem in problems]


def _validate(project: ProjectContext, key: str, yaml_text: str, instructions: str):
    """Refuse ``yaml_text`` unless it loads without problems the agent's
    current file does not already have."""
    path = _agent_path(project, key)
    _, data = _document(yaml_text)
    problems: list[str] = []
    if data.get("name") != key:
        problems.append(f"name must be {key!r}, the agent's directory")
    problems.extend(_check_instructions(instructions))
    if problems:
        raise AgentEditError(problems)

    baseline = _load_problems(project, key, path)
    fd, name = tempfile.mkstemp(
        prefix=".agent.", suffix=".candidate.yml", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(yaml_text)
        introduced = [
            problem
            for problem in _load_problems(project, key, Path(name))
            if problem not in baseline
        ]
    finally:
        os.unlink(name)
    if introduced:
        raise AgentEditError(introduced)


def _save(
    project: ProjectContext,
    key: str,
    yaml_text: str,
    instructions: str | None,
) -> AgentSource:
    path = _agent_path(project, key)
    _, data = _document(yaml_text)
    instructions_path = _instructions_location(path, data)
    rendered = instructions
    if rendered is None:
        if instructions_path is None:
            rendered = str(data.get("instructions") or "")
        elif instructions_path.is_file():
            rendered = instructions_path.read_text(encoding="utf-8")
        else:
            rendered = ""
    _validate(project, key, yaml_text, rendered)
    if instructions is not None and instructions_path is not None:
        replace_file(instructions_path, instructions)
    replace_file(path, yaml_text)
    return read_agent(project.root / "project.yml", key)


def update_agent(
    project_ref: str | Path,
    key: str,
    *,
    changes: Mapping[str, Any] | None = None,
    instructions: str | None = None,
) -> AgentSource:
    """Apply ``changes`` (setting → value; ``None`` removes the setting) and
    new ``instructions`` to the agent, keeping everything else as it is."""
    project = _project(project_ref)
    path = _agent_path(project, key)
    yaml, data = _document(path.read_text(encoding="utf-8"))
    changes = dict(changes or {})
    reserved = sorted(_RESERVED_KEYS & set(changes))
    if reserved:
        raise AgentEditError(
            [f"{name} cannot be changed as a setting" for name in reserved]
        )
    for name, value in changes.items():
        if value is None:
            data.pop(name, None)
        elif name in data:
            data[name] = merge_into(data[name], value)
        else:
            data[name] = value
    if instructions is not None and _instructions_location(path, data) is None:
        data["instructions"] = _literal(instructions)
        data.pop("instructions_file", None)
    return _save(project, key, dump_document(yaml, data), instructions)


def write_agent_yaml(project_ref: str | Path, key: str, yaml_text: str) -> AgentSource:
    """Replace the agent's ``agent.yml`` with ``yaml_text`` (an editor
    showing the whole file)."""
    return _save(_project(project_ref), key, yaml_text, None)


_AGENT_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")


def create_agent(
    project_ref: str | Path,
    key: str,
    *,
    model: str,
    label: str | None = None,
    description: str | None = None,
) -> AgentSource:
    """Add ``agents/<key>/`` from the project template: an ``agent.yml``
    and an ``instructions.md``."""
    from omniagents.core.project.scaffold import scaffold_agent

    project = _project(project_ref)
    if not _AGENT_KEY.fullmatch(key):
        raise AgentEditError(
            [
                f"{key!r} is not a valid agent key: use a-z, 0-9 and _, starting with a letter"
            ]
        )
    if key in project.agents_map or (project.root / "agents" / key).exists():
        raise AgentEditError([f"An agent named {key!r} already exists"])
    scaffold_agent(str(project.root), key, model=model)
    changes = {
        name: value
        for name, value in (("label", label), ("description", description))
        if value
    }
    if changes:
        return update_agent(project.root / "project.yml", key, changes=changes)
    return read_agent(project.root / "project.yml", key)


def delete_agent(project_ref: str | Path, key: str) -> str:
    """Remove ``agents/<key>/``. When it was the project's entrypoint, another
    agent becomes it (an ordinary agent before a headless helper such as
    the worker or the compactor). Returns the entrypoint afterwards."""
    project = _project(project_ref)
    path = _agent_path(project, key)
    remaining = sorted(k for k in project.agents_map if k != key)
    if not remaining:
        raise AgentEditError(["The project's only agent cannot be deleted"])
    manifest = project.root / "project.yml"
    yaml, data = _document(manifest.read_text(encoding="utf-8"))
    agents = data.get("agents")
    entrypoint = agents.get("entrypoint") if isinstance(agents, Mapping) else None
    if entrypoint == key:
        helpers = {"recapper", "compactor", "session_summarizer", "worker"}
        entrypoint = next((k for k in remaining if k not in helpers), remaining[0])
        agents["entrypoint"] = entrypoint
        replace_file(manifest, dump_document(yaml, data))
    shutil.rmtree(path.parent)
    return entrypoint or project.entrypoint or remaining[0]
