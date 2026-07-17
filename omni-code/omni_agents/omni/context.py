"""
Context factory for the coding agent.

This module defines context factories that build dynamic context for
Jinja2 instruction templates, including workspace-specific AGENTS.md files.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from omniagents import context_factory, model_config_resolver
from omni_code.skill_sources import source_skill_roots


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
        roots.extend(source_skill_roots(workspace_root))
    else:
        roots.append(_resolve_invocation_dir() / ".omni_code" / "skills")
        roots.extend(source_skill_roots(str(_resolve_invocation_dir())))

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


def _skill_roots_for_workspace(workspace_root: Optional[str]) -> List[Path]:
    """Adapter for the framework's skill-roots provider: same roots the
    available-skills prompt block is built from, keyed by workspace."""
    variables: Dict[str, Any] = (
        {"workspace_root": workspace_root} if workspace_root else {}
    )
    return _default_skill_roots(variables)


# Install omni-code's skill layout as the process-wide provider so the
# builtin ``load_skill`` tool resolves the same roots this module renders
# into the system prompt.
from omniagents.core.skills import set_skill_roots_provider  # noqa: E402

set_skill_roots_provider(_skill_roots_for_workspace)


# ---------------------------------------------------------------------------
# Product wiring for the builtin autonomy + worker server functions.
#
# The /goal, /loop, wakeup, workers, and bash-jobs server functions ship
# with the framework (builtin capability packs); the coding-specific
# prompts and the worktree isolation strategy are omni-code's. This
# module is the product's install point: it is imported (as the omni
# agent's context factory) when the agent spec loads, which precedes any
# run — and therefore any goal start, loop fire, or worker spawn — in
# every process shape (serve, TUI, evals, tests that build the spec).
# ---------------------------------------------------------------------------
from omniagents.core.autonomy import (  # noqa: E402
    set_goal_prompt_builders,
    set_loop_default_prompt_resolver,
    set_loop_prompt_builders,
)

from omni_code.goal_prompts import (  # noqa: E402
    build_goal_initial_prompt,
    build_goal_tick_prompt,
)
from omni_code.loop_prompts import (  # noqa: E402
    build_loop_concluded_prompt,
    build_loop_tick_prompt,
    resolve_default_loop_prompt,
)
from omni_code.workers_runtime import install as _install_workers_runtime  # noqa: E402

set_goal_prompt_builders(
    initial=build_goal_initial_prompt,
    tick=build_goal_tick_prompt,
)
set_loop_prompt_builders(
    tick=build_loop_tick_prompt,
    concluded=build_loop_concluded_prompt,
)
set_loop_default_prompt_resolver(resolve_default_loop_prompt)
_install_workers_runtime()

# Slash commands executed by the builtin `dispatch` tool (slash pack).
from omniagents.core.tools import register_slash_command  # noqa: E402

from omni_code.init_command import run_init_agent  # noqa: E402

register_slash_command("init", lambda _arg=None: run_init_agent())


def _discover_skill_dirs(skill_roots: List[Path]) -> List[Path]:
    try:
        from omniagents.core.skills import discover_skill_dirs
    except Exception:
        return []

    return discover_skill_dirs(skill_roots)


def build_available_skills_block(variables: Dict[str, Any]) -> str:
    try:
        from omniagents.core.skills import merge_skill_dirs, to_prompt, validate
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
    merged = merge_skill_dirs(local_dirs + global_dirs)

    try:
        return to_prompt(merged)
    except Exception:
        return to_prompt([])


_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".next", ".nuxt",
    "target", ".cargo",
})

_MAX_WALK_DEPTH = 5


def discover_agents_md(start_dir: str) -> List[str]:
    """Discover AGENTS.md file paths from the workspace, ordered parent -> child.

    Skips hidden directories, common dependency/build directories, and
    limits depth to avoid slow walks near the filesystem root.
    """
    found: List[str] = []
    start = os.path.abspath(start_dir)
    start_depth = start.count(os.sep)

    for root, dirs, files in os.walk(start):
        depth = root.count(os.sep) - start_depth
        if depth >= _MAX_WALK_DEPTH:
            dirs.clear()
            continue

        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]

        if "AGENTS.md" in files:
            found.append(os.path.join(root, "AGENTS.md"))

    found.sort(key=lambda path: (path.count(os.sep), path))
    return found


def _get_published_sources() -> List[Dict[str, Any]]:
    """Fetch the source descriptors omni serve published for this session.

    Best-effort: returns an empty list if ``agent_workspace_state`` isn't
    importable (e.g. running outside ``omni serve``) or if no sources were
    declared.
    """
    try:
        from omni_code.agent_workspace_state import get_sources
    except Exception:
        return []
    try:
        return get_sources()
    except Exception:
        return []


def _source_local_path(source: Dict[str, Any]) -> Optional[str]:
    kind = source.get("kind")
    if kind not in ("local", "local-git"):
        return None
    path = source.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return path


def format_agents_md_block(variables: Dict[str, Any] = None) -> str:
    """Format AGENTS.md references and workspace-source descriptors into
    an instruction block.

    Only file locations are injected — file content is never embedded in
    the system prompt. The agent reads files via ``read_file`` at runtime.

    Multi-source projects: every ``local``/``local-git`` source is walked
    for AGENTS.md (not just the primary). Under host mode (``unix_local``)
    these are the actual host paths the agent can touch; under containerised
    profiles the same descriptors are still useful as informational
    metadata (the in-container layout is ``<manifest_root>/<mountName>/``).
    """
    variables = variables or {}
    # ``workspace_root`` here is authoritative: ``omni serve`` forces it to the
    # sandbox manifest root (or the host dir for the host target) via
    # ``AgentService(workspace_root_override=…)``, so it is the path the
    # agent's tools actually operate in — never a leaked host path.
    workspace_root = variables.get("workspace_root")
    if isinstance(workspace_root, str) and workspace_root.strip():
        base_dir = str(Path(workspace_root).expanduser().resolve())
    else:
        base_dir = str(_resolve_invocation_dir())

    sources = _get_published_sources()

    walk_dirs: List[str] = [base_dir]
    seen_walk: set[str] = {base_dir}
    for src in sources:
        local_path = _source_local_path(src)
        if local_path and local_path not in seen_walk:
            walk_dirs.append(local_path)
            seen_walk.add(local_path)

    agents_paths: List[str] = []
    seen_paths: set[str] = set()
    for d in walk_dirs:
        if not os.path.isdir(d):
            continue
        for path in discover_agents_md(d):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            agents_paths.append(path)
    agents_paths.sort(key=lambda path: (path.count(os.sep), path))

    parts = [f"\n\n[Workspace]\n{base_dir}\n"]

    if sources:
        parts.append("\n<workspace_sources>")
        for src in sources:
            kind = src.get("kind") or "unknown"
            mount = src.get("mountName") or ""
            path = src.get("path")
            repo_url = src.get("repoUrl")
            ref = src.get("ref")
            attrs = [f'kind="{kind}"', f'mount="{mount}"']
            if isinstance(path, str) and path.strip():
                resolved = _source_local_path(src) or path
                attrs.append(f'path="{resolved}"')
            if isinstance(repo_url, str) and repo_url.strip():
                attrs.append(f'repo="{repo_url}"')
            if isinstance(ref, str) and ref.strip():
                attrs.append(f'ref="{ref}"')
            parts.append(f"\n<source {' '.join(attrs)} />")
        parts.append("\n</workspace_sources>\n")

    if agents_paths:
        parts.append("\n<agents_md_files>")
        for path in agents_paths:
            scope = os.path.dirname(path)
            parts.append(f"\n<file>\n<location>{path}</location>\n<scope>{scope}</scope>\n</file>")
        parts.append("\n</agents_md_files>\n")

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
        "agents_md_block": format_agents_md_block(variables),
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
