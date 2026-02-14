"""
Context factory for the coding agent.

This module defines context factories that build dynamic context for
Jinja2 instruction templates, including workspace-specific AGENTS.md files.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from omniagents import context_factory, model_config_resolver


def _resolve_invocation_dir() -> Path:
    env_pwd = os.environ.get("PWD")
    if isinstance(env_pwd, str) and env_pwd.strip():
        try:
            return Path(env_pwd).expanduser().resolve()
        except Exception:
            return Path(env_pwd).expanduser()
    try:
        return Path.cwd().resolve()
    except Exception:
        return Path.cwd()


def _default_skill_roots(variables: Dict[str, Any]) -> List[Path]:
    from omni_code.config import get_config_dir

    workspace_root = variables.get("workspace_root")
    roots: List[Path] = []
    if isinstance(workspace_root, str) and workspace_root.strip():
        roots.append(Path(workspace_root).expanduser() / ".omni_code" / "skills")
    else:
        roots.append(_resolve_invocation_dir() / ".omni_code" / "skills")

    seen: set[str] = set()
    unique: List[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)

    global_root = get_config_dir() / "skills"
    unique.append(global_root)
    return unique


def _discover_skill_dirs(skill_roots: List[Path]) -> List[Path]:
    try:
        from skills_ref import find_skill_md
    except Exception:
        return []

    candidates: List[Path] = []
    for root in skill_roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except Exception:
            continue

        if find_skill_md(root) is not None:
            candidates.append(root)

        try:
            children = sorted(root.iterdir(), key=lambda entry: entry.name)
        except Exception:
            continue

        for child in children:
            if not child.is_dir():
                continue
            if find_skill_md(child) is not None:
                candidates.append(child)

    seen: set[str] = set()
    unique: List[Path] = []
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except Exception:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    unique.sort(key=lambda entry: str(entry))
    return unique


def build_available_skills_block(variables: Dict[str, Any]) -> str:
    try:
        from skills_ref import to_prompt, validate
    except Exception:
        return "<available_skills>\n</available_skills>"

    skill_dirs = _discover_skill_dirs(_default_skill_roots(variables))

    valid_skill_dirs: List[Path] = []
    for skill_dir in skill_dirs:
        try:
            errors = validate(skill_dir)
        except Exception:
            continue
        if errors:
            continue
        valid_skill_dirs.append(skill_dir)

    local_dirs = [d for d in valid_skill_dirs if ".omni_code" in str(d)]
    global_dirs = [d for d in valid_skill_dirs if d not in local_dirs]

    merged: List[Path] = []
    seen_names: set[str] = set()
    for candidate in local_dirs + global_dirs:
        name = candidate.name
        if name in seen_names:
            continue
        seen_names.add(name)
        merged.append(candidate)

    try:
        return to_prompt(merged)
    except Exception:
        return to_prompt([])


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
    base_dir = str(_resolve_invocation_dir())
    agents_files = collect_agents_md(base_dir)

    if not agents_files:
        return f"\n\n[Workspace]\n{base_dir}\n"

    parts = [
        f"\n\n[Workspace]\n{base_dir}\n",
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
        "available_skills_block": build_available_skills_block(variables),
    }

    # Merge any input variables into context
    context.update(variables)

    return context


@model_config_resolver
def resolve_default_model() -> Dict[str, Any]:
    """
    Resolve the default model configuration.

    This provides the model's max_input_tokens and max_output_tokens
    to enable context remaining calculation in the UI.

    Returns:
        Dict with model configuration including name, model, provider,
        max_input_tokens, and max_output_tokens.
    """
    try:
        from omni_code.models import get_default_model_config, get_default_model_name

        config = get_default_model_config()
        if config:
            name = get_default_model_name()
            config["name"] = name
            return config
    except Exception:
        pass
    return None
