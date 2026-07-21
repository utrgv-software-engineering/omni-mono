"""Dedicated tool for activating an agent skill.

Skills live as ``<root>/<name>/SKILL.md`` under the directories returned
by :func:`omniagents.core.skills.skill_roots` (derived from the product
identity and serve-session state — ``<workspace>/.{slug}/skills``,
published source mounts, ``<config dir>/skills`` — unless a product
installs its own via ``set_skill_roots_provider``). Loading a skill is a
*named action*
— the agent calls ``load_skill('<name>')`` and the tool returns the full
SKILL.md text. This keeps activation unambiguous (one tool, not three
different ways to read a file) and measurable (an eval can count
``load_skill`` calls cleanly).

The tool intentionally always returns the *full* file — no offset/limit
parameter — because skill instructions are meant to be read end-to-end.
Partial loads defeat the discipline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.run_context import RunContextWrapper
from omniagents.core.skills import skill_roots
from omniagents.core.tools import RichToolOutput, rich_function_tool

from ._workspace import workspace_root_from_ctx


def _candidate_paths(name: str, workspace_root: str | None) -> list[Path]:
    candidates: list[Path] = []
    for root in skill_roots(workspace_root):
        candidates.append(root / name / "SKILL.md")
        candidates.append(root / name / "skill.md")
    return candidates


def _resolve_skill_path(name: str, workspace_root: str | None) -> Path | None:
    """Find <name>/SKILL.md across the configured skill roots, in order."""
    for p in _candidate_paths(name, workspace_root):
        if p.is_file():
            return p
    return None


@rich_function_tool(running_summary="Loading skill...")
async def load_skill(
    ctx: RunContextWrapper[Any],
    name: str,
) -> RichToolOutput:
    """Activate a skill by loading its SKILL.md end-to-end.

    Use this whenever your instructions tell you to activate a skill,
    or whenever you decide a skill is relevant to the current task. The
    returned text is the canonical instructions for that skill — read
    them fully, then proceed with the work the skill governs.

    Skills are searched across the configured skill roots in order
    (workspace-local locations first, then shared/global ones).

    Args:
        name: The skill's directory name, matching the ``name`` field in
            the skill's frontmatter and the ``available_skills`` index in
            the system prompt.
    """
    workspace = workspace_root_from_ctx(ctx)
    path = _resolve_skill_path(name, workspace)
    if path is None:
        searched = [
            str(p) for p in _candidate_paths(name, workspace) if p.name == "SKILL.md"
        ]
        llm_output = f"Skill {name!r} not found. Searched:\n" + "\n".join(
            f"  - {p}" for p in searched
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Skill {name!r} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "skill_name": name},
            },
        )

    text = path.read_text(encoding="utf-8", errors="replace")
    llm_output = text
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Loaded skill {name!r}",
            "preview": f"Loaded {path} ({len(text):,} chars)",
            "truncated": False,
            "metadata": {
                "success": True,
                "skill_name": name,
                "skill_path": str(path),
                "char_count": len(text),
            },
        },
    )
