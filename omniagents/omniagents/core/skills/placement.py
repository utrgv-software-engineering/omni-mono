"""Where a skill folder is, as the current Run's tools see it, and which skills a Run has.

Skill folders that aren't in the Run's workspace (an agent's own
``skills/``, a project's, the product's global root) live on the AgentHost.
A host Run's tools read them where they are. A sandbox Run's tools work in
the container, so each such folder is copied into it, read-only, under
:data:`~omniagents.workspace.sandbox.PROVIDED_ROOT`, and the agent is
shown that copy: the paths in ``available_skills`` and from ``load_skill``,
and so every file the skill's instructions point to (``references/``,
scripts), resolve where the agent works. The copy follows edits on the host.

Skill folders inside a sandbox Run's workspace (a repository's own
dot-dir, a mounted source's ``skills/``) exist only in the container, so
:func:`run_skills` lists and reads them through the Run's workspace.
"""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

__all__ = ["RunSkill", "provide_skill_dirs", "run_skills", "skill_dir_location"]


@dataclass(frozen=True)
class RunSkill:
    """One skill a Run can activate."""

    name: str
    description: str
    path: str
    """The skill's folder, as the Run's tools reach it."""
    skill_md: str
    """Its ``SKILL.md`` (or ``skill.md``) file name."""
    host_dir: Optional[Path] = None
    """The folder on the AgentHost; ``None`` for one only the Run's sandbox has."""
    error: Optional[str] = None
    """Why the skill is invalid (only listed with ``include_invalid``)."""


def _sandbox_workspace() -> Optional[Any]:
    """The current Run's workspace when it is a sandbox, else ``None``."""
    from omniagents.execution import current_request_context
    from omniagents.workspace.sandbox import SandboxWorkspace

    context = current_request_context()
    lease = context.environment_lease if context is not None else None
    workspace = lease.workspace if lease is not None else None
    return workspace if isinstance(workspace, SandboxWorkspace) else None


def _copy_name(skill_dir: Path) -> str:
    """The copy's path under PROVIDED_ROOT: one folder per host folder."""
    resolved = str(Path(skill_dir).resolve())
    digest = hashlib.sha256(resolved.encode()).hexdigest()[:12]
    return f"skills/{digest}/{Path(resolved).name}"


def _in_workspace(skill_dir: Path, workspace: Any) -> bool:
    path = posixpath.normpath(str(skill_dir))
    return path == workspace.root or path.startswith(workspace.root.rstrip("/") + "/")


def skill_dir_location(skill_dir: Path) -> str:
    """``skill_dir`` as the current Run's tools reach it.

    The host path, except in a sandbox Run for a folder outside its
    workspace: there, the copy :func:`provide_skill_dirs` makes.
    """
    workspace = _sandbox_workspace()
    if workspace is None or _in_workspace(skill_dir, workspace):
        return str(skill_dir)
    from omniagents.workspace.sandbox import PROVIDED_ROOT

    return posixpath.join(PROVIDED_ROOT, _copy_name(skill_dir))


async def provide_skill_dirs(skill_dirs: Iterable[Path]) -> None:
    """Copy host skill folders into the current Run's sandbox (no-op on a host Run)."""
    workspace = _sandbox_workspace()
    if workspace is None:
        return
    for skill_dir in skill_dirs:
        skill_dir = Path(skill_dir)
        if _in_workspace(skill_dir, workspace) or not skill_dir.is_dir():
            continue
        await workspace.provide(skill_dir, _copy_name(skill_dir))


async def run_skills(
    roots: Iterable[Path], *, include_invalid: bool = False
) -> List[RunSkill]:
    """The skills in ``roots`` (earlier roots win a name), where the Run reaches them.

    A root inside a sandbox Run's workspace is listed and read through the
    workspace; every other root on the AgentHost's disk.
    """
    from .discovery import discover_skill_dirs

    workspace = _sandbox_workspace()
    found: List[RunSkill] = []
    for root in roots:
        root = Path(root)
        if workspace is not None and _in_workspace(root, workspace):
            found.extend(await _sandbox_skills(workspace, str(root)))
        else:
            found.extend(_host_skill(d) for d in discover_skill_dirs([root]))
    merged: List[RunSkill] = []
    seen: set[str] = set()
    for skill in found:
        key = posixpath.basename(skill.path)
        if key in seen or (skill.error and not include_invalid):
            continue
        seen.add(key)
        merged.append(skill)
    return merged


async def read_run_skill(skill: RunSkill) -> str:
    """A skill's ``SKILL.md`` text."""
    if skill.host_dir is not None:
        return (skill.host_dir / skill.skill_md).read_text(encoding="utf-8")
    workspace = _sandbox_workspace()
    if workspace is None:
        raise FileNotFoundError(posixpath.join(skill.path, skill.skill_md))
    return await workspace.read_text(posixpath.join(skill.path, skill.skill_md))


def _host_skill(skill_dir: Path) -> RunSkill:
    from .parser import find_skill_md

    skill_md = find_skill_md(skill_dir)
    text = skill_md.read_text(encoding="utf-8") if skill_md is not None else None
    return _skill_from_text(
        skill_dir.name,
        skill_dir_location(skill_dir),
        skill_md.name if skill_md is not None else "SKILL.md",
        text,
        host_dir=skill_dir,
    )


async def _sandbox_skills(workspace: Any, root: str) -> List[RunSkill]:
    """Skills in a folder of the sandbox: the folder itself, or its subfolders."""
    from omniagents.workspace.types import WorkspaceError

    async def read(folder: str) -> Optional[RunSkill]:
        for name in ("SKILL.md", "skill.md"):
            path = posixpath.join(folder, name)
            try:
                if not await workspace.is_file(path):
                    continue
                text = await workspace.read_text(path)
            except (OSError, WorkspaceError):
                continue
            return _skill_from_text(posixpath.basename(folder), folder, name, text)
        return None

    try:
        if not await workspace.is_dir(root):
            return []
        own = await read(root)
        entries = await workspace.list_dir(root, include_hidden=True)
    except (OSError, WorkspaceError):
        return []
    out = [own] if own is not None else []
    for entry in entries:
        if entry.is_dir and not entry.is_symlink:
            child = await read(posixpath.join(root, entry.name))
            if child is not None:
                out.append(child)
    return out


def _skill_from_text(
    folder_name: str,
    path: str,
    skill_md: str,
    text: Optional[str],
    *,
    host_dir: Optional[Path] = None,
) -> RunSkill:
    from .errors import ParseError
    from .parser import parse_frontmatter
    from .validator import validate_metadata

    def invalid(error: str) -> RunSkill:
        return RunSkill(folder_name, "", path, skill_md, host_dir, error)

    if text is None:
        return invalid("Missing required file: SKILL.md")
    try:
        metadata, _ = parse_frontmatter(text)
    except ParseError as exc:
        return invalid(str(exc))
    errors = validate_metadata(metadata, Path(folder_name))
    if errors:
        return invalid("; ".join(errors))
    return RunSkill(
        name=str(metadata["name"]).strip(),
        description=str(metadata["description"]).strip(),
        path=path,
        skill_md=skill_md,
        host_dir=host_dir,
    )
