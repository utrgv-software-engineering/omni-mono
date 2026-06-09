"""Dedicated tool for activating an agent skill.

Skills live as markdown files under either the workspace
(``<workspace>/.omni_code/skills/<name>/SKILL.md``) or the user's global
config dir (``~/.config/omni_code/skills/<name>/SKILL.md``). Loading a
skill is a *named action* — the agent calls ``load_skill('<name>')`` and
the tool returns the full SKILL.md text. This keeps activation
unambiguous (one tool, not three different ways to read a file) and
measurable (the eval can count ``load_skill`` calls cleanly).

The tool intentionally always returns the *full* file — no offset/limit
parameter — because the Skills Protocol mandates reading skills
end-to-end. Partial loads defeat the discipline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput
from omniagents.builtin.tools._workspace import workspace_root_from_ctx

from omni_code.skill_sources import source_skill_roots


def _global_skills_dir() -> Path:
    """Mirror omni-code's global skills location."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "omni_code" / "skills"
    return Path.home() / ".config" / "omni_code" / "skills"


def _resolve_skill_path(name: str, workspace_root: str | None) -> Path | None:
    """Find <name>/SKILL.md, preferring the per-workspace location.

    Returns the resolved path or None if not found anywhere.
    """
    candidates: list[Path] = []
    if workspace_root:
        ws = Path(workspace_root)
        candidates.append(ws / ".omni_code" / "skills" / name / "SKILL.md")
        candidates.append(ws / ".omni_code" / "skills" / name / "skill.md")
    for root in source_skill_roots(workspace_root):
        candidates.append(root / name / "SKILL.md")
        candidates.append(root / name / "skill.md")
    candidates.append(_global_skills_dir() / name / "SKILL.md")
    candidates.append(_global_skills_dir() / name / "skill.md")
    for p in candidates:
        if p.is_file():
            return p
    return None


@rich_function_tool(client_status="Loading skill...")
async def load_skill(
    ctx: RunContextWrapper[Any],
    name: str,
) -> RichToolOutput:
    """Activate a skill by loading its SKILL.md end-to-end.

    Use this whenever the Skills Protocol tells you to activate a skill,
    or whenever you decide a skill is relevant to the current task. The
    returned text is the canonical instructions for that skill — read
    them fully, then proceed with the work the skill governs.

    Resolution order: per-workspace skill first
    (``<workspace>/.omni_code/skills/<name>/SKILL.md``), then global
    (``~/.config/omni_code/skills/<name>/SKILL.md``).

    Args:
        name: The skill's directory name (e.g. ``"software-planning"``,
            ``"bugfix"``, ``"webapp-acceptance-runner"``). Match the
            ``name`` field in the skill's frontmatter.
    """
    workspace = workspace_root_from_ctx(ctx)
    path = _resolve_skill_path(name, workspace)
    if path is None:
        searched: list[str] = []
        if workspace:
            searched.append(
                str(Path(workspace) / ".omni_code" / "skills" / name / "SKILL.md")
            )
        for root in source_skill_roots(workspace):
            searched.append(str(root / name / "SKILL.md"))
        searched.append(str(_global_skills_dir() / name / "SKILL.md"))
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
