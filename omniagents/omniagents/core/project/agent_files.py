"""Editing a project agent's files: its ``agent.yml`` and its instructions.

Every editor goes through here (Studio's agent editor, an optimizer
applying improved instructions), so there is one answer to "what does
saving an agent do":

- **Only what was edited changes.** ``agent.yml`` is edited as a
  round-trip document, so comments, key order, quoting and indentation
  outside the edited keys stay as they were.
- **Nothing invalid is written.** A candidate is put in place and loaded
  exactly as a run loads it (:func:`~omniagents.core.project.runtime.build_spec_from_project`);
  if loading reports a problem the agent did not already have (an unknown
  tool, capability, guardrail or agent, a file that does not parse), the
  previous file is put back and the edit refused. Problems the file already
  had do not block saving it, so a broken agent can be fixed here.
- **Files are found by the project's layout** (``agents/<key>/agent.yml``),
  not by loading the project, so an agent that stops the project loading
  can still be opened.
- **Instructions are saved where they live**: inline ``instructions:`` when
  the file has them (they take precedence when loading), otherwise the
  ``instructions_file``.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.core.utils.yaml_edit import (
    dump_document,
    load_document,
    merge_into,
    replace_file,
)
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import LiteralScalarString

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
    #: Why ``agent.yml`` does not parse, when it does not (it can still be
    #: replaced with :func:`write_agent_yaml`).
    error: str | None = None
    #: ``agent.yml``'s settings as plain data, for an editor's form.
    settings: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "path": str(self.path),
            "yaml": self.yaml_text,
            "instructions": self.instructions,
            "instructions_path": (
                str(self.instructions_path) if self.instructions_path else None
            ),
            "error": self.error,
            "settings": self.settings,
        }


def _manifest(project_ref: str | Path) -> Path:
    path = Path(project_ref).expanduser().resolve()
    manifest = path if path.is_file() else path / "project.yml"
    if not manifest.is_file():
        raise FileNotFoundError(f"No project.yml at {path}")
    return manifest


def _project_settings(manifest: Path) -> dict[str, Any]:
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def agent_paths(project_ref: str | Path) -> dict[str, Path]:
    """Each agent's ``agent.yml`` by key, from the project's layout
    (``agents/<key>/agent.yml`` under each ``paths.agents`` directory) and
    its ``agents.include``/``exclude``, without parsing any of them."""
    manifest = _manifest(project_ref)
    settings = _project_settings(manifest)
    configured = (settings.get("paths") or {}).get("agents", "agents")
    roots = configured if isinstance(configured, list) else [configured]
    section = settings.get("agents") if isinstance(settings.get("agents"), dict) else {}

    def globs(value: Any) -> list[str]:
        if value in (None, ""):
            return []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    include, exclude = globs(section.get("include")), globs(section.get("exclude"))
    found: dict[str, Path] = {}
    for root in roots:
        folder = (manifest.parent / str(root)).resolve()
        for path in sorted(folder.rglob("agent.yml")) if folder.is_dir() else []:
            key = path.parent.name
            if path.parent == folder:
                continue
            if include and not any(fnmatch.fnmatch(key, g) for g in include):
                continue
            if exclude and any(fnmatch.fnmatch(key, g) for g in exclude):
                continue
            found.setdefault(key, path.resolve())
    return found


def _agent_path(project_ref: str | Path, key: str) -> Path:
    path = agent_paths(project_ref).get(key)
    if path is None:
        raise FileNotFoundError(f"Agent not found in project: {key}")
    return path


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
    path = _agent_path(project_ref, key)
    text = path.read_text(encoding="utf-8")
    try:
        _, data = _document(text)
    except AgentEditError as exc:
        return AgentSource(key, path, text, "", None, error=str(exc))
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
        settings=json.loads(json.dumps(data, default=str)),
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


def _load_problems(manifest: Path, key: str) -> list[str]:
    """What loading the project and agent ``key`` from the files as they are
    now reports."""
    from omniagents.core.config.loader import collect_config_problems

    with collect_config_problems() as problems:
        try:
            build_spec_from_project(load_project(str(manifest)), key)
        # Loading imports the project's code; any failure refuses the edit.
        except Exception as exc:  # noqa: BLE001
            problems.append(str(exc))
    return problems


def _save(
    manifest: Path, key: str, yaml_text: str, instructions: str | None
) -> AgentSource:
    """Write ``yaml_text`` as the agent's ``agent.yml`` (and ``instructions``
    to its instructions file) unless that introduces a loading problem."""
    path = _agent_path(manifest, key)
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
    problems: list[str] = []
    if data.get("name") != key:
        problems.append(f"name must be {key!r}, the agent's directory")
    problems.extend(_check_instructions(rendered))
    if problems:
        raise AgentEditError(problems)

    baseline = _load_problems(manifest, key)
    original = path.read_text(encoding="utf-8")
    replace_file(path, yaml_text)
    introduced = [p for p in _load_problems(manifest, key) if p not in baseline]
    if introduced:
        replace_file(path, original)
        raise AgentEditError(introduced)
    if instructions is not None and instructions_path is not None:
        replace_file(instructions_path, instructions)
    return read_agent(manifest, key)


def update_agent(
    project_ref: str | Path,
    key: str,
    *,
    changes: Mapping[str, Any] | None = None,
    instructions: str | None = None,
) -> AgentSource:
    """Apply ``changes`` (setting → value; ``None`` removes the setting) and
    new ``instructions`` to the agent, keeping everything else as it is."""
    manifest = _manifest(project_ref)
    path = _agent_path(manifest, key)
    doc_yaml, data = _document(path.read_text(encoding="utf-8"))
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
    return _save(manifest, key, dump_document(doc_yaml, data), instructions)


def write_agent_yaml(project_ref: str | Path, key: str, yaml_text: str) -> AgentSource:
    """Replace the agent's ``agent.yml`` with ``yaml_text`` (an editor
    showing the whole file)."""
    return _save(_manifest(project_ref), key, yaml_text, None)


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

    manifest = _manifest(project_ref)
    if not _AGENT_KEY.fullmatch(key):
        raise AgentEditError(
            [
                f"{key!r} is not a valid agent key: use a-z, 0-9 and _, starting with a letter"
            ]
        )
    if key in agent_paths(manifest) or (manifest.parent / "agents" / key).exists():
        raise AgentEditError([f"An agent named {key!r} already exists"])
    scaffold_agent(str(manifest.parent), key, model=model)
    changes = {
        name: value
        for name, value in (("label", label), ("description", description))
        if value
    }
    if changes:
        return update_agent(manifest, key, changes=changes)
    return read_agent(manifest, key)


def delete_agent(project_ref: str | Path, key: str) -> str:
    """Remove ``agents/<key>/``. When it was the project's entrypoint, another
    agent becomes it (an ordinary agent before a headless helper such as
    the worker or the compactor). Returns the entrypoint afterwards."""
    manifest = _manifest(project_ref)
    path = _agent_path(manifest, key)
    remaining = sorted(k for k in agent_paths(manifest) if k != key)
    if not remaining:
        raise AgentEditError(["The project's only agent cannot be deleted"])
    doc_yaml, data = _document(manifest.read_text(encoding="utf-8"))
    agents = data.get("agents")
    entrypoint = agents.get("entrypoint") if isinstance(agents, Mapping) else None
    if entrypoint == key:
        helpers = {"recapper", "compactor", "session_summarizer", "worker"}
        entrypoint = next((k for k in remaining if k not in helpers), remaining[0])
        agents["entrypoint"] = entrypoint
        replace_file(manifest, dump_document(doc_yaml, data))
    shutil.rmtree(path.parent)
    return entrypoint or remaining[0]
