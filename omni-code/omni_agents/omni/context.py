"""
Context factory for the coding agent.

This module defines context factories that build dynamic context for
Jinja2 instruction templates, including workspace-specific AGENTS.md files.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

from omniagents import context_factory


def collect_agents_md(start_dir: str) -> List[Tuple[str, str]]:
    """Collect all AGENTS.md files from the workspace, ordered parent → child."""
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


def format_agents_md_block() -> str:
    """Format AGENTS.md files into an instruction block."""
    cwd = os.getcwd()
    agents_files = collect_agents_md(cwd)

    if not agents_files:
        return f"\n\n[Workspace]\n{cwd}\n"

    parts = [
        f"\n\n[Workspace]\n{cwd}\n",
        "\n\n# Repository AGENTS.md (parent → child)\n",
    ]
    for path, body in agents_files:
        parts.append(f"\n[AGENTS.md at {os.path.dirname(path)}]\n{body}")

    return "".join(parts)


@context_factory
def build_coding_context(variables: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Build the instruction context for the coding agent.

    Args:
        variables: Dict with input variables from agent.yml.

    Returns:
        Dict with current_date, current_time, agents_md_block, etc.
    """
    variables = variables or {}

    now = datetime.now().astimezone()
    context: Dict[str, Any] = {
        "current_date": now.strftime("%Y-%m-%d"),
        "current_time": now.strftime("%H:%M %Z"),
        "agents_md_block": format_agents_md_block(),
    }

    # Merge any input variables into context
    context.update(variables)

    return context
