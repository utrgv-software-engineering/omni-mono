import html
from pathlib import Path

from .parser import find_skill_md, read_properties


def to_prompt(skill_dirs: list[Path]) -> str:
    if not skill_dirs:
        return "<available_skills>\n</available_skills>"

    lines = ["<available_skills>"]

    for skill_dir in skill_dirs:
        resolved_dir = Path(skill_dir).resolve()
        props = read_properties(resolved_dir)
        skill_md_path = find_skill_md(resolved_dir)

        lines.append("<skill>")
        lines.append("<name>")
        lines.append(html.escape(props.name))
        lines.append("</name>")
        lines.append("<description>")
        lines.append(html.escape(props.description))
        lines.append("</description>")
        lines.append("<location>")
        lines.append(str(skill_md_path))
        lines.append("</location>")
        lines.append("</skill>")

    lines.append("</available_skills>")
    return "\n".join(lines)
