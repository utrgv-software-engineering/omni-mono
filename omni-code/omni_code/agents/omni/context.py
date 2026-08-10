"""
Context factory for the coding agent.

This module defines context factories that build dynamic context for
Jinja2 instruction templates, including workspace-specific AGENTS.md files.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# Skill roots need no product wiring: the framework derives omni-code's
# layout (`<workspace>/.omni_code/skills` + routed source mounts +
# `~/.config/omni_code/skills`) from the installed product identity and
# the current RequestContext (`omniagents.core.skills.skill_roots`).


# ---------------------------------------------------------------------------
# Product wiring for the builtin autonomy + worker server functions.
#
# The /goal, /loop, wakeup, workers, and bash-jobs server functions ship
# with the framework (builtin capability packs). The coding-specific
# prompt wording is data, not wiring: the manifest's `paths.prompts`
# directory (omni_code/prompts/) carries goal_initial.md / goal_tick.md
# (rendered with the goal context) and loop.md (the packaged tail of the
# framework's /loop default chain: <workspace>/.omni/loop.md →
# ~/.config/omni_code/loop.md → packaged). No install calls here —
# the framework resolves the templates by convention
# (omniagents.core.project.prompts); loop tick/concluded prompts use the
# framework defaults as-is.
# ---------------------------------------------------------------------------

# Workers: every input the wiring needs — the routed Workspace sources
# (worktree isolation), the current ProjectContext (packaged-manifest
# fallback via the product identity), the orchestrator's sandbox
# attachment, the product slug, and the model store (installed from the
# declared catalog by the product bootstrap, i.e. the omni_code package
# import above) — derives from framework state, so the whole thing is
# one framework call.
from omniagents.core.workers import install_project_worker_defaults  # noqa: E402

install_project_worker_defaults()

# Recap + compaction: fully conventional. The framework owns the /recap
# and /compact orchestration AND the summarizer wiring — with no explicit
# install, the seams run the project agents named by convention
# (omni_code/agents/recapper, omni_code/agents/compactor) one-shot under
# each agent's own instructions.md (omni-code owns the prompts) on the
# utility-run model resolution. Shipping those agents IS the install;
# nothing to wire here.

# Workspace info: the framework owns the fs_* picker server functions
# (builtin `workspace_info` capability pack) and the terminal.* functions
# (builtin `terminal` pack). Both resolve the current Workspace and immutable
# EnvironmentLease from the routed RequestContext — no providers or
# process-global serve state are installed here.

# Slash commands executed by the builtin `dispatch` tool (slash pack):
# declared, not registered. The manifest's `slash_commands:` section
# binds /init to the packaged init_agent; the framework's default slash
# registry falls back to manifest bindings (via the generic
# omniagents.core.runtime.slash_agents runner) whenever no Python
# handler is registered — nothing to wire here.


def build_available_skills_block(variables: Dict[str, Any]) -> str:
    """Render the available-skills prompt block for the derived skill roots.

    The framework's ``skill_roots`` orders workspace-local roots before
    the global config root, and its merge is first-wins by skill name, so
    local skills shadow global ones without any re-sorting here.
    """
    from omniagents.core.skills import build_available_skills_block as _fw_block
    from omniagents.core.skills import skill_roots

    workspace_root = variables.get("workspace_root")
    if not (isinstance(workspace_root, str) and workspace_root.strip()):
        workspace_root = None
    return _fw_block(skill_roots(workspace_root))


_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".cargo",
    }
)

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

        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]

        if "AGENTS.md" in files:
            found.append(os.path.join(root, "AGENTS.md"))

    found.sort(key=lambda path: (path.count(os.sep), path))
    return found


def _get_routed_sources() -> List[Dict[str, Any]]:
    """Fetch source descriptors from the current routed Workspace.

    Context formatting is also used outside an active Run, where no routed
    Workspace exists; that path intentionally returns an empty list.
    """
    from omniagents.execution import current_request_context

    context = current_request_context()
    if context is None or context.workspace is None:
        return []
    return [dict(source) for source in context.workspace.sources]


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


def format_agents_md_block(variables: Optional[Dict[str, Any]] = None) -> str:
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
    # ``workspace_root`` here is authoritative: the Run's captured Workspace
    # and EnvironmentLease supply the sandbox manifest root (or host Workspace
    # root), so it is the path the agent's tools actually operate in — never a
    # leaked host path.
    workspace_root = variables.get("workspace_root")
    if isinstance(workspace_root, str) and workspace_root.strip():
        base_dir = str(Path(workspace_root).expanduser().resolve())
    else:
        base_dir = str(_resolve_invocation_dir())

    sources = _get_routed_sources()

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
            src_path = src.get("path")
            repo_url = src.get("repoUrl")
            ref = src.get("ref")
            attrs = [f'kind="{kind}"', f'mount="{mount}"']
            if isinstance(src_path, str) and src_path.strip():
                resolved = _source_local_path(src) or src_path
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
            parts.append(
                f"\n<file>\n<location>{path}</location>\n<scope>{scope}</scope>\n</file>"
            )
        parts.append("\n</agents_md_files>\n")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Plan-in-context: the current session's task list, rendered into the
# instructions so the plan stays in the agent's face without a task_list
# call. Instructions rebuild per run, which covers session start, resume,
# and post-compaction; intra-run state is already covered by every plan
# tool result echoing the snapshot.
# ---------------------------------------------------------------------------

#: Same markers ``task_list`` uses in its previews, so the block reads
#: identically to the tool results the agent already sees.
_PLAN_STATUS_MARKERS = {
    "pending": "·",
    "in_progress": "▶",
    "completed": "✓",
    "blocked": "⨯",
}


def _load_current_plan(variables: Dict[str, Any]) -> Optional[Any]:
    """Read the session's durable plan, or ``None`` when unreachable.

    The plan is keyed ``(thread_id, scope)`` where the thread id IS the
    session id (``task_list_tools._handle``). Both keys arrive in the
    factory's variables: ``AgentService._apply_session_metadata`` stamps
    ``session_id`` onto ``session.context``, sub-agent spawners stamp
    ``task_scope``, and the run worker merges ``session.context`` into the
    factory input. The store shard is resolved exactly the way
    ``task_list_tools._PlanHandle._bind`` resolves it: through the live
    session's recorder when the session is findable via ``current_service()``
    (set before the factory runs), else the ``(project, agent)`` shard
    fallback — reading a different shard would look up the plan in a
    different database from the one the plan tools write.
    """
    session_id = variables.get("session_id")
    if not (isinstance(session_id, str) and session_id.strip()):
        return None
    scope = variables.get("task_scope")
    if not (isinstance(scope, str) and scope.strip()):
        scope = "main"

    from omniagents.core.agents.service import current_service
    from omniagents.core.conversation.plans import PlanStore
    from omniagents.core.conversation.recorder import recorder_for_session
    from omniagents.core.conversation.store import get_conversation_store

    session = None
    service = current_service()
    if service is not None:
        manager = getattr(service, "_session_manager", None)
        by_id = getattr(manager, "_by_id", None) if manager is not None else None
        if isinstance(by_id, dict):
            session = by_id.get(session_id)

    recorder = recorder_for_session(session) if session is not None else None
    if recorder is not None:
        store = recorder.store
    else:
        store = get_conversation_store(
            getattr(session, "_project_slug", None),
            getattr(session, "_agent_slug", None),
        )
    return PlanStore(store).current(session_id, scope)


def build_current_plan_block(variables: Optional[Dict[str, Any]] = None) -> str:
    """Render the session's live plan as an instruction block, or ``""``.

    Empty when there is no plan, the plan is finalized, every step is
    completed, or the read fails for ANY reason — instructions must never
    fail to build over plan bookkeeping (the same best-effort posture the
    plan tools themselves take).

    Step lines mirror the ``tasks_snapshot`` / ``task_list`` preview format
    (status marker, ``#id``, subject, open blockers), plus the step's
    ``exitCriteria`` when the summary carries one, and a final
    "Next unblocked" line naming the pending steps with no open blockers.
    """
    try:
        plan = _load_current_plan(variables or {})
        if plan is None or not getattr(plan, "is_open", False):
            return ""
        summaries = plan.to_summaries()
        if not any(s.get("status") != "completed" for s in summaries):
            return ""

        lines: List[str] = []
        unblocked: List[str] = []
        for s in summaries:
            status = str(s.get("status") or "pending")
            marker = _PLAN_STATUS_MARKERS.get(status, "?")
            subject = str(s.get("subject") or "")[:80]
            line = f"{marker} #{s.get('id')} {subject}"
            blocked_by = [b for b in (s.get("blockedBy") or []) if isinstance(b, str)]
            if blocked_by:
                line += f"  [blocked by {','.join(blocked_by)}]"
            criteria = s.get("exitCriteria")
            if isinstance(criteria, str) and criteria.strip():
                line += f"  (exit: {criteria.strip()})"
            lines.append(line)
            if status == "pending" and not blocked_by:
                unblocked.append(f"#{s.get('id')}")

        next_line = (
            ", ".join(unblocked)
            if unblocked
            else "none — finish or re-plan blockers first"
        )
        return (
            "\n\n<current_plan>\n"
            "Your live task list (· pending ▶ in_progress ✓ completed ⨯ blocked); "
            "keep it current with task_update.\n"
            + "\n".join(lines)
            + f"\nNext unblocked: {next_line}\n"
            + "</current_plan>\n"
        )
    except Exception:
        return ""


@context_factory
def build_coding_context(variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        "current_plan_block": build_current_plan_block(variables),
    }

    # Merge any input variables into context
    context.update(variables)

    return context


@model_config_resolver
def resolve_default_model() -> Optional[Dict[str, Any]]:
    """
    Resolve the default model configuration.

    This provides the model's max_input_tokens and max_output_tokens
    to enable context remaining calculation in the UI.

    Returns:
        Dict with model configuration including name, model, provider,
        max_input_tokens, and max_output_tokens.
    """
    try:
        from omniagents.core.models import model_store

        store = model_store()
        config = store.get_default_model_config()
        if config:
            config["name"] = store.get_default_model_name()
            return config
    except Exception:
        pass
    return None
