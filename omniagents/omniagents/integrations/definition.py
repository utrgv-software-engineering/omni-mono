"""The factory's definition as people edit it, and moving the factory to a newer commit of it.

A factory runs from a checkout of its repository. What people edit from the
Workplace (agents' instructions, skills, automations) is read again on each
run, so publishing an edit is moving the checkout to the commit that holds
it: the next run uses it, and no restart is needed. Everything else
(agent settings, tools, dependencies, sandbox profiles) is built when the
server starts, and changes only by a release.

- :func:`definition_tree` lists what can be edited, from the checkout.
- :func:`check_changes` says what is wrong with edits before they are saved.
- :func:`advance` fast-forwards the checkout to a commit that changes only
  what is read on each run.
"""

from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import yaml

__all__ = [
    "DefinitionError",
    "advance",
    "agent_changes",
    "agent_settings",
    "delete_agent_changes",
    "check_changes",
    "definition_tree",
    "live_commit",
    "new_agent_changes",
    "origin",
    "read_each_run",
    "skill_source",
]

#: An automation's folder name, and a skill's (the skill validator checks the rest).
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
#: An agent's key: its folder's name, and its ``name:``.
_AGENT_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")


class DefinitionError(Exception):
    """An edit or an advance the factory refuses; ``status`` is the HTTP answer."""

    def __init__(self, status: int, message: str, paths: Sequence[str] = ()):
        super().__init__(message)
        self.status = status
        self.paths = list(paths)


def read_each_run(path: str) -> bool:
    """Whether the factory reads ``path`` (relative to its root) again for
    each run, so changing it needs no restart: automations, anything in a
    ``skills`` folder, and Markdown outside evaluations (instructions and
    their fragments)."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if parts[0] == "automations":
        return True
    if "skills" in parts[:-1]:
        return True
    return path.endswith(".md") and parts[0] != "evaluations"


# -- what can be edited --------------------------------------------------------


def _relative(root: Path, path: Path) -> Optional[str]:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _yaml(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _skills(root: Path, folder: Path) -> List[Dict[str, str]]:
    """The skills in a ``skills`` folder: each subfolder with a ``SKILL.md``,
    with its one-line description."""
    from omniagents.core.skills.errors import ParseError
    from omniagents.core.skills.parser import parse_frontmatter

    out = []
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            skill_md = child / "SKILL.md"
            relative = _relative(root, skill_md)
            if child.is_dir() and skill_md.is_file() and relative:
                try:
                    meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                    description = str(meta.get("description") or "")
                except (OSError, ParseError):
                    description = ""
                out.append(
                    {"name": child.name, "path": relative, "description": description}
                )
    return out


def _definition(
    root: Path, key: str, agent_yml: Path, kind: str
) -> Tuple[Dict[str, Any], Optional[str]]:
    """One agent definition (an agent, or a base agents extend) and what it extends."""
    data = _yaml(agent_yml)
    folder = agent_yml.parent
    instructions = data.get("instructions_file")
    instructions_path = (
        _relative(root, folder / str(instructions)) if instructions else None
    )
    skills_folder = _relative(root, folder / "skills")
    extends = data.get("extends")
    return (
        {
            "key": key,
            "kind": kind,
            "name": data.get("label")
            or str(data.get("name") or key).replace("_", " ").replace("-", " ").title(),
            "description": str(data.get("description") or ""),
            "instructions": instructions_path,
            # Inline instructions live in agent.yml, which changes by release.
            "inline_instructions": bool(data.get("instructions")),
            "skills_folder": skills_folder,
            "skills": _skills(root, folder / "skills"),
            "extends": str(extends).strip() if isinstance(extends, str) else None,
        },
        str(extends).strip() if isinstance(extends, str) and extends.strip() else None,
    )


def _project_skill_folders(root: Path) -> List[str]:
    project = _yaml(root / "project.yml")
    configured = (project.get("paths") or {}).get("skills", "skills")
    folders = configured if isinstance(configured, list) else [configured]
    return [f for f in (_relative(root, root / str(folder)) for folder in folders) if f]


def definition_tree(root: Path) -> Dict[str, Any]:
    """What people can edit in the factory at ``root``: each agent with its
    instructions and every skill it has, the bases agents extend, the
    factory's own skills, and its automations.

    An agent's ``effective_skills`` are in the order the framework searches
    them (its own folder, the bases it extends, the factory's), the first of
    a name winning; each says who has it (``scope``) and which it replaces.
    """
    from omniagents.core.project.agent_files import agent_paths

    root = Path(root).resolve()
    project = _yaml(root / "project.yml")
    agents_section = project.get("agents")
    section: Dict[str, Any] = agents_section if isinstance(agents_section, dict) else {}
    chat = [
        str(a)
        for a in [section.get("chat_entrypoint"), *(section.get("chat_agents") or [])]
        if a
    ]
    agents: List[Dict[str, Any]] = []
    bases: Dict[str, Dict[str, Any]] = {}
    chains: Dict[str, List[str]] = {}
    for key, agent_yml in agent_paths(root).items():
        entry, extends = _definition(root, key, agent_yml, "agent")
        entry["chat"] = key in chat
        agents.append(entry)
        chain: List[str] = []
        while extends and extends not in chain:
            if extends not in bases:
                if not (root / extends / "agent.yml").is_file():
                    break
                bases[extends], _next = _definition(
                    root, extends, root / extends / "agent.yml", "base"
                )
            chain.append(extends)
            extends = bases[extends]["extends"]
        chains[key] = chain
    for base in bases.values():
        base["agents"] = sorted(
            k for k, chain in chains.items() if base["key"] in chain
        )
    folders = _project_skill_folders(root)
    skills: List[Dict[str, Any]] = [
        {"folder": f, "skills": _skills(root, root / f)} for f in folders
    ]
    for agent in agents:
        sources: List[Tuple[Dict[str, Any], List[Dict[str, str]]]] = [
            ({"kind": "agent", "agent": agent["key"]}, agent["skills"])
        ]
        for key in chains[agent["key"]]:
            sources.append(
                (
                    {"kind": "base", "base": key, "name": bases[key]["name"]},
                    bases[key]["skills"],
                )
            )
        for folder in skills:
            sources.append(({"kind": "factory"}, folder["skills"]))
        effective: Dict[str, Dict[str, Any]] = {}
        for scope, found in sources:
            for skill in found:
                if skill["name"] in effective:
                    effective[skill["name"]]["replaces"].append(
                        {"path": skill["path"], "scope": scope}
                    )
                else:
                    effective[skill["name"]] = {**skill, "scope": scope, "replaces": []}
        agent["extends_chain"] = chains[agent["key"]]
        agent["effective_skills"] = list(effective.values())
    automations = []
    automations_root = root / "automations"
    if automations_root.is_dir():
        for child in sorted(automations_root.iterdir()):
            if (child / "automation.md").is_file():
                automations.append(
                    {
                        "name": child.name,
                        "path": f"automations/{child.name}/automation.md",
                    }
                )
    return {
        "agents": agents,
        "bases": [bases[k] for k in sorted(bases)],
        "skills": skills,
        "automations": automations,
    }


def skill_source(root: Path, path: str) -> Dict[str, Any]:
    """Where the skill folder ``path`` a run used comes from: an agent's own
    (``{"kind": "agent", "agent"}``), a base's, the factory's, the
    installation's global folder, or a repository the run worked in."""
    from omniagents.core.config.dirs import product_config_dir
    from omniagents.core.project.agent_files import agent_paths
    from omniagents.core.product import product_info

    root = Path(root).resolve()
    folder = PurePosixPath(path).parent  # the skills folder holding it
    try:
        relative = Path(str(folder)).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        relative = None
    if relative is not None:
        for key, agent_yml in agent_paths(root).items():
            if _relative(root, agent_yml.parent / "skills") == relative:
                return {"kind": "agent", "agent": key}
        if relative in _project_skill_folders(root):
            return {"kind": "factory"}
        if (root / relative).parent.joinpath("agent.yml").is_file():
            return {"kind": "base", "base": str(PurePosixPath(relative).parent)}
    try:
        Path(str(folder)).resolve().relative_to(product_config_dir().resolve())
        return {"kind": "global"}
    except (OSError, ValueError):
        pass
    parts = [p for p in folder.parts if p not in ("/", "")]
    if parts and parts[-1] == "skills":
        parts = parts[:-1]
    if parts and parts[-1] == f".{product_info().slug}":
        parts = parts[:-1]
    return {"kind": "repository", "repository": parts[-1] if parts else ""}


def _editable(tree: Dict[str, Any], path: str) -> Optional[str]:
    """What ``path`` is ("instructions", "skill", a skill's other "skill file"
    or "automation"), or ``None`` when it is not something people edit here."""
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    definitions = tree["agents"] + tree["bases"]
    if any(d["instructions"] == path for d in definitions):
        return "instructions"
    parts = pure.parts
    if (
        len(parts) == 3
        and parts[0] == "automations"
        and parts[2] == "automation.md"
        and _NAME.match(parts[1])
    ):
        return "automation"
    folders = {d["skills_folder"] for d in definitions} | {
        s["folder"] for s in tree["skills"]
    }
    for depth in range(1, len(parts) - 1):
        folder = PurePosixPath(*parts[:depth]).as_posix()
        if folder in folders and _NAME.match(parts[depth]):
            return (
                "skill"
                if len(parts) == depth + 2 and pure.name == "SKILL.md"
                else "skill file"
            )
    return None


def check_changes(
    root: Path, changes: Sequence[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """Each change's problems, by path (``{}`` when all can be saved). A
    change is ``{"path", "content"}``; ``content`` ``None`` deletes the file."""
    from omniagents.core.automations import AutomationError, parse_automation
    from omniagents.core.project.agent_files import check_instructions
    from omniagents.core.skills.errors import ParseError
    from omniagents.core.skills.parser import parse_frontmatter
    from omniagents.core.skills.validator import validate_metadata

    root = Path(root).resolve()
    tree = definition_tree(root)
    problems: Dict[str, List[str]] = {}
    # Agents' and templates' settings, an agent added or deleted, and the
    # project's agents section are checked by loading the project with the
    # change, as the factory will when it moves to it.
    new_agents = {
        PurePosixPath(str(c.get("path"))).parent.as_posix()
        for c in changes
        if c.get("content") is not None and _new_agent(root, str(c.get("path") or ""))
    }
    folders = new_agents | _agent_folders(root)
    loads: List[str] = []
    for change in changes:
        path = str(change.get("path") or "")
        content = change.get("content")
        if content is None and _in_deleted_agent(path, changes):
            loads.append(path)  # the agent goes, everything in its folder with it
            continue
        if path.endswith(".py"):
            problems[path] = ["Code changes with a release."]
            continue
        in_folder = any(path.startswith(folder + "/") for folder in folders)
        if in_folder and not _editable(tree, path):
            if content is not None and not isinstance(content, str):
                problems[path] = ["The content must be text."]
            loads.append(path)
            continue
        if path == "project.yml" or _capability_file(root, path):
            if path == "project.yml" and (
                not isinstance(content, str) or not _agents_section_only(root, content)
            ):
                problems[path] = [
                    "Only the project's agents section changes here; other project settings change with a release."
                ]
            loads.append(path)
            continue
        kind = _editable(tree, path)
        found: List[str] = []
        if kind is None:
            found.append(
                "Only agents' instructions, skills and automations are edited here."
            )
        elif content is None:
            if kind == "instructions":
                found.append("An agent's instructions can be emptied, not deleted.")
        elif not isinstance(content, str):
            found.append("The content must be text.")
        elif kind == "instructions":
            found.extend(check_instructions(content))
        elif kind == "skill":
            try:
                metadata, _ = parse_frontmatter(content)
            except ParseError as exc:
                found.append(str(exc))
            else:
                found.extend(
                    validate_metadata(metadata, Path(PurePosixPath(path).parent.name))
                )
        elif kind == "automation":
            try:
                parse_automation(root, PurePosixPath(path).parts[1], content)
            except AutomationError as exc:
                found.append(str(exc).replace(str(root) + "/", ""))
        if found:
            problems[path] = found
    if loads and not problems:
        found = _load_with(root, changes)
        if found:
            problems[loads[0]] = found
    return problems


def _agent_roots(root: Path) -> List[str]:
    project = _yaml(root / "project.yml")
    configured = (project.get("paths") or {}).get("agents", "agents")
    roots = configured if isinstance(configured, list) else [configured]
    return [r for r in (_relative(root, root / str(f)) for f in roots) if r]


def _new_agent(root: Path, path: str) -> bool:
    """Whether ``path`` is the ``agent.yml`` of an agent the project doesn't have."""
    pure = PurePosixPath(path)
    if pure.name != "agent.yml" or len(pure.parts) < 3 or ".." in pure.parts:
        return False
    if PurePosixPath(*pure.parts[:-2]).as_posix() not in _agent_roots(root):
        return False
    return (
        _AGENT_KEY.fullmatch(pure.parent.name) is not None
        and not (root / path).exists()
    )


def _in_deleted_agent(path: str, changes: Sequence[Dict[str, Any]]) -> bool:
    """Whether ``path`` is in a folder whose ``agent.yml`` ``changes`` delete:
    the agent goes, its code with it."""
    return any(
        c.get("content") is None
        and PurePosixPath(str(c.get("path"))).name == "agent.yml"
        and path.startswith(PurePosixPath(str(c.get("path"))).parent.as_posix() + "/")
        for c in changes
    )


def _agent_folders(root: Path) -> set:
    """Every agent's folder, and every template's (a base agents extend)."""
    tree = definition_tree(root)
    return {
        PurePosixPath(d["skills_folder"]).parent.as_posix()
        for d in tree["agents"] + tree["bases"]
        if d["skills_folder"]
    }


def _capability_file(root: Path, path: str) -> bool:
    project = _yaml(root / "project.yml")
    configured = (project.get("paths") or {}).get("capabilities") or []
    folders = configured if isinstance(configured, list) else [configured]
    pure = PurePosixPath(path)
    return pure.suffix in (".yml", ".yaml") and any(
        _relative(root, root / str(f)) == pure.parent.as_posix() for f in folders
    )


def _without_agents(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return {k: v for k, v in data.items() if k != "agents"}


def _agents_section_only(root: Path, content: str) -> bool:
    """Whether ``content`` changes project.yml only in its ``agents`` section
    (which agents there are, which are in Chat, the default ones)."""
    try:
        after = yaml.safe_load(content)
    except yaml.YAMLError:
        return False
    return _without_agents(after) == _without_agents(_yaml(root / "project.yml"))


def _reloads(root: Path, path: str, before: Any = None, after: Any = None) -> bool:
    """Whether a change to ``path`` applies by reloading the running
    factory's agents: an agent's or a template's settings (``agent.yml``),
    a capability, or project.yml's ``agents`` section (``before`` and
    ``after`` are its two versions)."""
    if PurePosixPath(path).name == "agent.yml" or _capability_file(root, path):
        return True
    if path == "project.yml":
        return before is not None and _without_agents(before) == _without_agents(after)
    return False


def _load_with(root: Path, changes: Sequence[Dict[str, Any]]) -> List[str]:
    """What loading the project, and every agent in it, reports once
    ``changes`` are made (in a copy; ``root`` is untouched)."""
    import shutil
    import tempfile

    from omniagents.core.config.loader import collect_config_problems
    from omniagents.core.project.runtime import build_spec_from_project, load_project

    with tempfile.TemporaryDirectory(prefix="definition-check-") as tmp:
        copy = Path(tmp) / root.name
        shutil.copytree(
            root, copy, ignore=shutil.ignore_patterns(".git", "__pycache__")
        )
        for change in changes:
            target = copy / str(change["path"])
            if change.get("content") is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(change["content"]), encoding="utf-8")
        with collect_config_problems() as problems:
            try:
                project = load_project(str(copy / "project.yml"))
                for key in project.agents_map:
                    build_spec_from_project(project, key)
            # Loading imports the project's code; any failure refuses the change.
            except Exception as exc:  # noqa: BLE001
                problems.append(str(exc).replace(str(copy) + "/", ""))
        # Loading the copy made it the process's project: put the real one back.
        load_project(str(root / "project.yml"))
    return list(problems)


def new_agent_changes(
    root: Path,
    key: str,
    *,
    description: str,
    template: Optional[str] = None,
    chat: bool = False,
) -> List[Dict[str, Any]]:
    """The files that add agent ``key`` (its ``agent.yml`` and instructions,
    built on ``template`` when given) and, with ``chat``, list it in Chat."""
    from omniagents.core.utils.yaml_edit import dump_document, load_document

    root = Path(root).resolve()
    if not _AGENT_KEY.fullmatch(key):
        raise DefinitionError(
            422, "A name is a-z, 0-9 and _, starting with a letter (up to 64)"
        )
    tree = definition_tree(root)
    if any(a["key"] == key for a in tree["agents"]):
        raise DefinitionError(409, f"There is already an agent named {key}")
    if template is not None and template not in {b["key"] for b in tree["bases"]}:
        raise DefinitionError(422, f"There is no template {template}")
    folder = f"{_agent_roots(root)[0]}/{key}"
    definition = {"name": key, "description": description.strip()}
    if template:
        definition["extends"] = template
    definition["instructions_file"] = "instructions.md"
    title = key.replace("_", " ").title()
    changes: List[Dict[str, Any]] = [
        {
            "path": f"{folder}/agent.yml",
            "content": yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        },
        {
            "path": f"{folder}/instructions.md",
            "content": f"# {title}\n\n{description.strip()}\n",
        },
    ]
    if chat:
        text = (root / "project.yml").read_text(encoding="utf-8")
        document_yaml, document = load_document(text)
        agents = document.setdefault("agents", {})
        listed = agents.get("chat_agents")
        if listed is None:
            current = agents.get("chat_entrypoint") or agents.get("entrypoint")
            agents["chat_agents"] = [a for a in [current, key] if a]
        elif key not in listed:
            listed.append(key)
        changes.append(
            {"path": "project.yml", "content": dump_document(document_yaml, document)}
        )
    return changes


# -- an agent's settings ----------------------------------------------------------


def _models(root: Path) -> List[Dict[str, str]]:
    """The models the project's catalog offers (``paths.model_catalog``)."""
    project = _yaml(root / "project.yml")
    catalog = (project.get("paths") or {}).get("model_catalog")
    data = _yaml(root / str(catalog)) if catalog else {}
    out: List[Dict[str, str]] = []
    for provider in (data.get("providers") or {}).values():
        for key, model in ((provider or {}).get("models") or {}).items():
            label = (model or {}).get("label") if isinstance(model, dict) else None
            out.append({"id": str(key), "label": str(label or key)})
    return out


def agent_settings(root: Path, key: str) -> Dict[str, Any]:
    """Agent ``key``'s settings as fields, what it inherits from its
    template, and the choices each field has."""
    root = Path(root).resolve()
    tree = definition_tree(root)
    agent = next((a for a in tree["agents"] if a["key"] == key), None)
    if agent is None:
        raise DefinitionError(404, f"No agent {key}")
    from omniagents.core.project.agent_files import agent_paths
    from omniagents.core.project.runtime import load_project

    path = agent_paths(root)[key]
    data = _yaml(path)
    inherited: Dict[str, Any] = {"capabilities": [], "model": None}
    for base_key in agent["extends_chain"]:
        base = _yaml(root / base_key / "agent.yml")
        inherited["capabilities"] += [
            c if isinstance(c, str) else c.get("name")
            for c in base.get("capabilities") or []
        ]
        inherited["model"] = inherited["model"] or base.get("model")
    project = _yaml(root / "project.yml")
    agents_section = project.get("agents")
    section: Dict[str, Any] = agents_section if isinstance(agents_section, dict) else {}
    chat = [str(a) for a in section.get("chat_agents") or []]
    chat_default = section.get("chat_entrypoint") or section.get("entrypoint")
    registry = load_project(str(root / "project.yml")).capability_registry
    return {
        "key": key,
        "path": _relative(root, path),
        "label": data.get("label") or "",
        "description": str(data.get("description") or ""),
        "model": data.get("model") or None,
        "capabilities": [
            c if isinstance(c, str) else c.get("name")
            for c in data.get("capabilities") or []
        ],
        "calls": [
            t.get("agent")
            for t in data.get("agent_tools") or []
            if isinstance(t, dict) and t.get("agent")
        ],
        "chat": key in chat or key == chat_default,
        "chat_default": key == chat_default,
        "automation_default": key == section.get("entrypoint"),
        "inherited": inherited,
        "called_by": sorted(
            a["key"]
            for a in tree["agents"]
            if a["key"] != key
            and key
            in [
                t.get("agent")
                for t in _yaml(agent_paths(root)[a["key"]]).get("agent_tools") or []
                if isinstance(t, dict)
            ]
        ),
        "options": {
            "models": _models(root),
            "capabilities": [
                {
                    "name": name,
                    "description": (
                        getattr(capability, "instructions_text", None) or ""
                    )
                    .strip()
                    .split("\n")[0],
                }
                for name, capability in sorted(registry.items())
            ],
            "agents": [
                {"key": a["key"], "name": a["name"]}
                for a in tree["agents"]
                if a["key"] != key
            ],
        },
    }


def _project_agents_change(
    root: Path, edit: Callable[[Any], None]
) -> Optional[Dict[str, Any]]:
    """project.yml with ``edit`` made to its ``agents`` section (comments
    kept), or ``None`` when that changes nothing."""
    from omniagents.core.utils.yaml_edit import dump_document, load_document

    text = (root / "project.yml").read_text(encoding="utf-8")
    document_yaml, document = load_document(text)
    if document.get("agents") is None:
        document["agents"] = {}
    edit(document["agents"])
    after = dump_document(document_yaml, document)
    return None if after == text else {"path": "project.yml", "content": after}


def agent_changes(
    root: Path, key: str, settings: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """The files that give agent ``key`` ``settings`` (any of label,
    description, model, capabilities, calls, chat, chat_default,
    automation_default), keeping everything else in them."""
    from omniagents.core.project.agent_files import agent_paths
    from omniagents.core.utils.yaml_edit import dump_document, load_document

    root = Path(root).resolve()
    current = agent_settings(root, key)
    path = agent_paths(root)[key]
    text = path.read_text(encoding="utf-8")
    document_yaml, data = load_document(text)

    def put(name: str, value: Any) -> None:
        if value in (None, "", []):
            data.pop(name, None)
        else:
            data[name] = value

    if "label" in settings:
        put("label", str(settings["label"] or "").strip())
    if "description" in settings:
        put("description", str(settings["description"] or "").strip())
    if "model" in settings:
        put("model", settings["model"] or None)
    if "capabilities" in settings:
        inherited = set(current["inherited"]["capabilities"])
        put("capabilities", [c for c in settings["capabilities"] if c not in inherited])
    if "calls" in settings:
        existing = {
            t.get("agent"): t
            for t in data.get("agent_tools") or []
            if isinstance(t, dict)
        }
        put(
            "agent_tools",
            [
                existing.get(a) or {"agent": a, "tool_name": a, "mode": "conversation"}
                for a in settings["calls"]
            ],
        )
    changes: List[Dict[str, Any]] = []
    after = dump_document(document_yaml, data)
    if after != text:
        changes.append({"path": _relative(root, path), "content": after})

    def agents_section(section: Any) -> None:
        if "chat" in settings or "chat_default" in settings:
            chat = list(section.get("chat_agents") or [])
            wanted = settings.get("chat", current["chat"]) or settings.get(
                "chat_default"
            )
            if wanted and key not in chat:
                chat.append(key)
            if not wanted and key in chat:
                chat.remove(key)
            section["chat_agents"] = chat
        if settings.get("chat_default"):
            section["chat_entrypoint"] = key
        if settings.get("automation_default"):
            section["entrypoint"] = key

    project = _project_agents_change(root, agents_section)
    if project:
        changes.append(project)
    if settings.get("chat") is False and current["chat_default"]:
        raise DefinitionError(
            409, "This is Chat's default agent: make another agent the default first"
        )
    return changes


def delete_agent_changes(root: Path, key: str) -> List[Dict[str, Any]]:
    """The files that delete agent ``key``: its folder, and its place in Chat."""
    root = Path(root).resolve()
    current = agent_settings(root, key)
    if current["chat_default"]:
        raise DefinitionError(
            409, "This is Chat's default agent: make another agent the default first"
        )
    if current["automation_default"]:
        raise DefinitionError(
            409,
            "Automations run this agent by default: make another agent the default first",
        )
    if current["called_by"]:
        raise DefinitionError(
            409,
            "Other agents call this one: "
            + ", ".join(current["called_by"])
            + ". Remove it from them first",
        )
    folder = (root / str(current["path"])).parent
    changes: List[Dict[str, Any]] = [
        {"path": _relative(root, f), "content": None}
        for f in sorted(folder.rglob("*"))
        if f.is_file() and "__pycache__" not in f.parts
    ]

    def agents_section(section: Any) -> None:
        chat = list(section.get("chat_agents") or [])
        if key in chat:
            chat.remove(key)
            section["chat_agents"] = chat

    project = _project_agents_change(root, agents_section)
    if project:
        changes.append(project)
    return changes


# -- moving the checkout ---------------------------------------------------------


def _git(
    root: Path, *args: str, token: Optional[str] = None, strip: bool = True
) -> str:
    auth: List[str] = []
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        auth = ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
    done = subprocess.run(
        ["git", *auth, *args], cwd=root, capture_output=True, text=True
    )
    if done.returncode != 0:
        raise DefinitionError(409, f"git {args[0]}: {done.stderr.strip()}")
    return done.stdout.strip() if strip else done.stdout


def origin(root: Path) -> Optional[str]:
    """Where the checkout at ``root`` comes from (its ``origin``'s URL, with
    no credentials), or ``None``."""
    from urllib.parse import urlsplit, urlunsplit

    root = Path(root).resolve()
    if not (root / ".git").exists():
        return None
    try:
        url = _git(root, "remote", "get-url", "origin")
    except DefinitionError:
        return None
    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit(parts._replace(netloc=netloc))
    return url


def live_commit(root: Path) -> Optional[str]:
    """The commit the factory at ``root`` runs, or ``None`` when it does not
    run from a checkout of its own repository."""
    root = Path(root).resolve()
    if not (root / ".git").exists():
        return None
    try:
        return _git(root, "rev-parse", "HEAD")
    except DefinitionError:
        return None


def uncommitted(root: Path) -> List[str]:
    """Files changed in the checkout and not committed."""
    out = _git(Path(root).resolve(), "status", "--porcelain", "-z", strip=False)
    # "XY path" entries; a rename's entry is followed by its old path.
    entries = out.split("\0")
    paths, skip = [], False
    for entry in entries:
        if skip or not entry:
            skip = False
            continue
        paths.append(entry[3:])
        skip = entry[0] in "RC"
    return paths


def advance(root: Path, commit: str, *, token: Optional[str] = None) -> Dict[str, Any]:
    """Fast-forward the checkout at ``root`` to ``commit`` (fetched from its
    ``origin``) when everything between them is read on each run, or adds
    agents or changes which are in Chat (``reload``: the server reloads its
    agents). Refuses (:class:`DefinitionError`) a checkout with uncommitted
    changes, a commit that is not ahead of the running one, and one that
    changes what only a release changes."""
    root = Path(root).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise DefinitionError(422, "Expected a full commit id")
    current = live_commit(root)
    if current is None:
        raise DefinitionError(409, "This factory does not run from a checkout")
    if commit == current:
        return {"commit": commit, "reload": False}
    dirty = uncommitted(root)
    if dirty:
        raise DefinitionError(
            409,
            "The running factory has changes that are not committed",
            dirty,
        )
    _git(root, "fetch", "--quiet", "origin", token=token)
    try:
        _git(root, "cat-file", "-e", f"{commit}^{{commit}}")
    except DefinitionError:
        raise DefinitionError(409, "The factory's repository has no such commit")
    try:
        _git(root, "merge-base", "--is-ancestor", current, commit)
    except DefinitionError:
        raise DefinitionError(
            409, "That commit does not follow the one the factory runs"
        )
    changed = [
        line.split("\t", 1)
        for line in _git(
            root, "diff", "--name-status", "--no-renames", current, commit
        ).splitlines()
        if "\t" in line
    ]
    reload = False
    needs_release = []
    deleted = [
        {"path": path, "content": None} for status, path in changed if status == "D"
    ]
    for status, path in changed:
        if read_each_run(path):
            continue
        if status == "D" and _in_deleted_agent(path, deleted):
            reload = True  # an agent deleted, its code with it
            continue
        before = after = None
        if path == "project.yml" and status == "M":
            before = yaml.safe_load(_git(root, "show", f"{current}:project.yml"))
            after = yaml.safe_load(_git(root, "show", f"{commit}:project.yml"))
        if _reloads(root, path, before, after):
            reload = True
            continue
        needs_release.append(path)
    if needs_release:
        raise DefinitionError(
            422,
            "These changes take effect only with a release",
            needs_release,
        )
    _git(root, "checkout", "--quiet", commit)
    return {"commit": commit, "reload": reload}
