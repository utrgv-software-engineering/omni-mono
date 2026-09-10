"""Builtin ``skills.list`` and ``skills.read``: the skills an agent can activate.

The model sees the same index in its system prompt; this is the RPC a
client uses to show it to the person (Ink's ``/skills`` drawer, which reads a
skill body with ``skills.read``). Workspace-local
roots come from the routed lease when the caller has one; the global root
is always included.
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
    from omniagents.core.skills import (
        discover_skill_dirs,
        merge_skill_dirs,
        read_properties,
        skill_roots,
    )

    roots = skill_roots(_workspace_root())
    skills: list[dict[str, Any]] = []
    for skill_dir in merge_skill_dirs(discover_skill_dirs(roots)):
        try:
            props = read_properties(skill_dir)
        except Exception as exc:  # a broken SKILL.md is reported, not hidden
            skills.append(
                {
                    "name": skill_dir.name,
                    "description": "",
                    "path": str(skill_dir),
                    "error": str(exc),
                }
            )
            continue
        skills.append(
            {
                "name": props.name,
                "description": props.description,
                "path": str(skill_dir),
            }
        )
    return {"skills": skills, "roots": [str(r) for r in roots]}


@server_function(
    name_override="skills.read",
    description="Read one skill's SKILL.md by name.",
    strict=True,
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.OPTIONAL_WORKSPACE_READ,
)
async def skills_read(name: str, _session_id: str | None = None) -> dict:
    from omniagents.core.skills import (
        discover_skill_dirs,
        merge_skill_dirs,
        read_properties,
        read_skill_text,
        skill_roots,
    )

    wanted = (name or "").strip().lower()
    for skill_dir in merge_skill_dirs(
        discover_skill_dirs(skill_roots(_workspace_root()))
    ):
        try:
            props = read_properties(skill_dir)
        except Exception:
            continue
        if props.name.lower() != wanted:
            continue
        text = read_skill_text(skill_dir)
        if text is None:
            return {"ok": False, "error": f"{props.name} has no SKILL.md"}
        return {"ok": True, "name": props.name, "path": str(skill_dir), "text": text}
    return {"ok": False, "error": f"No skill named {name!r}"}
