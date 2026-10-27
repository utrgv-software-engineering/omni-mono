"""Builtin ``skills.list`` and ``skills.read``: the skills an agent can activate.

The model sees the same index in its system prompt; this is the RPC a
client uses to show it to the person (Ink's ``/skills`` drawer, which reads a
skill body with ``skills.read``). Workspace-local
roots come from the routed lease when the caller has one, and are read
through its workspace (a sandbox's exist only in the container); the
global root is always included. Paths are where the session's tools reach
each skill.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function
from omniagents.execution import current_request_context
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


def _workspace_root() -> str | None:
    context = current_request_context()
    lease = getattr(context, "environment_lease", None) if context else None
    workspace = getattr(lease, "workspace", None) if lease else None
    root = getattr(workspace, "root", None) if workspace else None
    return root if isinstance(root, str) and root else None


@server_function(
    name_override="skills.list",
    description="List the skills available to this session, with descriptions.",
    strict=True,
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.OPTIONAL_WORKSPACE_READ,
)
async def skills_list(_session_id: str | None = None) -> dict:
    from omniagents.core.skills import run_skills, skill_roots

    roots = skill_roots(_workspace_root())
    skills: list[dict[str, Any]] = []
    for skill in await run_skills(roots, include_invalid=True):
        entry: dict[str, Any] = {
            "name": skill.name,
            "description": skill.description,
            "path": skill.path,
        }
        if skill.error:  # a broken SKILL.md is reported, not hidden
            entry["error"] = skill.error
        skills.append(entry)
    return {"skills": skills, "roots": [str(r) for r in roots]}


@server_function(
    name_override="skills.read",
    description="Read one skill's SKILL.md by name.",
    strict=True,
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.OPTIONAL_WORKSPACE_READ,
)
async def skills_read(name: str, _session_id: str | None = None) -> dict:
    from omniagents.core.skills import read_run_skill, run_skills, skill_roots

    wanted = (name or "").strip().lower()
    for skill in await run_skills(skill_roots(_workspace_root())):
        if skill.name.lower() != wanted:
            continue
        try:
            text = await read_run_skill(skill)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"{skill.name}: {exc}"}
        return {"ok": True, "name": skill.name, "path": skill.path, "text": text}
    return {"ok": False, "error": f"No skill named {name!r}"}
