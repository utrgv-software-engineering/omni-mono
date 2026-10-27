import html
import posixpath
from pathlib import Path

from .parser import find_skill_md, read_properties
from .placement import RunSkill, skill_dir_location


def to_prompt(skill_dirs: list[Path]) -> str:
    entries = []
    for skill_dir in skill_dirs:
        resolved_dir = Path(skill_dir).resolve()
        props = read_properties(resolved_dir)
        skill_md_path = find_skill_md(resolved_dir)
        entries.append(
            RunSkill(
                name=props.name,
                description=props.description,
                # Where the Run's tools reach it: in a sandbox, the read-only copy.
                path=skill_dir_location(resolved_dir),
                skill_md=skill_md_path.name if skill_md_path else "SKILL.md",
                host_dir=resolved_dir,
            )
        )
    return run_skills_prompt(entries)


def run_skills_prompt(skills: list[RunSkill]) -> str:
    """The ``available_skills`` index for skills as a Run reaches them."""
    if not skills:
        return "<available_skills>\n</available_skills>"

    lines = ["<available_skills>"]
    for skill in skills:
        lines.append("<skill>")
        lines.append("<name>")
        lines.append(html.escape(skill.name))
        lines.append("</name>")
        lines.append("<description>")
        lines.append(html.escape(skill.description))
        lines.append("</description>")
        lines.append("<location>")
        lines.append(posixpath.join(skill.path, skill.skill_md))
        lines.append("</location>")
        lines.append("</skill>")

    lines.append("</available_skills>")
    return "\n".join(lines)
