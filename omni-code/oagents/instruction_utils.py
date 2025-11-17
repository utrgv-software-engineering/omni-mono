import os
from typing import List, Tuple


def collect_agents_md(start_dir: str) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    for root, dirs, files in os.walk(start_dir):
        if "AGENTS.md" in files:
            path = os.path.join(root, "AGENTS.md")
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    found.append((path, handle.read()))
            except Exception:
                pass
    found.sort(
        key=lambda entry: (
            (
                len(
                    os.path.relpath(
                        os.path.dirname(entry[0]), start_dir
                    ).split(os.sep)
                )
                if os.path.dirname(entry[0]) != start_dir
                else 0
            ),
            entry[0],
        )
    )
    return found


def format_instructions(base_text: str) -> str:
    cwd = os.getcwd()
    agents_files = collect_agents_md(cwd)
    if not agents_files:
        return base_text + "\n\n[Workspace]\n{{ workspace_root }}\n"
    parts = [
        base_text,
        "\n\n[Workspace]\n{{ workspace_root }}\n",
        "\n\n# Repository AGENTS.md (parent → child)\n",
    ]
    for path, body in agents_files:
        parts.append(f"\n[AGENTS.md at {os.path.dirname(path)}]\n{body}")
    return "".join(parts)
