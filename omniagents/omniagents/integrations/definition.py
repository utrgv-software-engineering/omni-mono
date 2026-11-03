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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

__all__ = [
    "DefinitionError",
    "advance",
    "check_changes",
    "definition_tree",
    "live_commit",
    "read_each_run",
]

#: An automation's folder name, and a skill's (the skill validator checks the rest).
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


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
    """The skills in a ``skills`` folder: each subfolder with a ``SKILL.md``."""
    out = []
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            skill_md = child / "SKILL.md"
            relative = _relative(root, skill_md)
            if child.is_dir() and skill_md.is_file() and relative:
                out.append({"name": child.name, "path": relative})
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


def definition_tree(root: Path) -> Dict[str, Any]:
    """What people can edit in the factory at ``root``: each agent and each
    base they extend (instructions and skills), the project's own skills,
    and its automations."""
    from omniagents.core.project.agent_files import agent_paths

    root = Path(root).resolve()
    agents: List[Dict[str, Any]] = []
    bases: Dict[str, Dict[str, Any]] = {}
    pending: List[str] = []
    for key, agent_yml in agent_paths(root).items():
        entry, extends = _definition(root, key, agent_yml, "agent")
        agents.append(entry)
        if extends:
            pending.append(extends)
    while pending:
        folder = pending.pop()
        if folder in bases or not (root / folder / "agent.yml").is_file():
            continue
        entry, extends = _definition(root, folder, root / folder / "agent.yml", "base")
        bases[folder] = entry
        if extends:
            pending.append(extends)
    project = _yaml(root / "project.yml")
    configured = (project.get("paths") or {}).get("skills", "skills")
    folders = configured if isinstance(configured, list) else [configured]
    skills = []
    for folder in folders:
        relative = _relative(root, root / str(folder))
        if relative:
            skills.append(
                {"folder": relative, "skills": _skills(root, root / str(folder))}
            )
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


def _editable(tree: Dict[str, Any], path: str) -> Optional[str]:
    """What ``path`` is ("instructions", "skill" or "automation"), or ``None``
    when it is not something people edit here."""
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
    if pure.name == "SKILL.md" and len(parts) >= 3 and _NAME.match(parts[-2]):
        folder = PurePosixPath(*parts[:-2]).as_posix()
        folders = {d["skills_folder"] for d in definitions} | {
            s["folder"] for s in tree["skills"]
        }
        if folder in folders:
            return "skill"
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
    for change in changes:
        path = str(change.get("path") or "")
        content = change.get("content")
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
    return problems


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


def advance(root: Path, commit: str, *, token: Optional[str] = None) -> str:
    """Fast-forward the checkout at ``root`` to ``commit`` (fetched from its
    ``origin``), when everything between them is read on each run; the
    commit. Refuses (:class:`DefinitionError`) a checkout with uncommitted
    changes, a commit that is not ahead of the running one, and one that
    changes what only a release changes."""
    root = Path(root).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise DefinitionError(422, "Expected a full commit id")
    current = live_commit(root)
    if current is None:
        raise DefinitionError(409, "This factory does not run from a checkout")
    if commit == current:
        return commit
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
    changed = _git(root, "diff", "--name-only", current, commit).splitlines()
    needs_release = [p for p in changed if not read_each_run(p)]
    if needs_release:
        raise DefinitionError(
            422,
            "These changes take effect only with a release",
            needs_release,
        )
    _git(root, "checkout", "--quiet", commit)
    return commit
