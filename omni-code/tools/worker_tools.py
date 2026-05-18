from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata
from omniagents.rpc.agents import WorkerManager


_WORKER_AGENT_YAML = (
    Path(__file__).resolve().parent.parent / "omni_agents" / "worker" / "agent.yml"
)


def _worktrees_dir() -> str:
    """Resolve the worktree parent directory for the active workspace.

    Lives under the workspace abstraction's framework-owned scratch root
    so it stays outside the user's project files (host: ``~/.omniagents/
    scratch/worktrees``; sandbox: ``/var/lib/omniagents/scratch/worktrees``).
    Tools that previously read ``_WORKTREES_DIR`` should call this instead.
    """
    from omniagents import workspace as _ws

    return os.path.join(_ws.scratch_root(), "worktrees")


# ---------------------------------------------------------------------------
# Whimsical worker name generator
# ---------------------------------------------------------------------------

_NAMES = [
    "blix",
    "coda",
    "nyx",
    "zeph",
    "remo",
    "tavi",
    "wren",
    "juno",
    "kip",
    "arlo",
    "miko",
    "vex",
    "pip",
    "zuri",
    "echo",
    "bolt",
    "nova",
    "rue",
    "otto",
    "dex",
    "sage",
    "flux",
    "cleo",
    "hex",
    "finn",
    "lux",
    "gus",
    "ivy",
    "rox",
    "ziggy",
]

_ADJECTIVES = [
    "lil",
    "big",
    "old",
    "tiny",
    "mad",
    "wee",
]

_TITLES = [
    "the swift",
    "the steady",
    "the bold",
    "the keen",
]

_SUFFIXES = [
    "jr",
    "mk2",
    "prime",
    "redux",
    "lite",
]


def _generate_worker_name() -> str:
    """Generate a whimsical worker name.

    ~80% of the time returns a bare name (e.g. ``pip``).
    ~20% of the time adds flair via an adjective, title, or suffix.
    On collision with an existing worker, appends ``-2``, ``-3``, etc.
    """
    name = random.choice(_NAMES)

    # 20% chance of flair
    if random.random() < 0.2:
        style = random.choice(["adj", "title", "suffix"])
        if style == "adj":
            name = f"{random.choice(_ADJECTIVES)}-{name}"
        elif style == "title":
            title = random.choice(_TITLES).replace(" ", "-")
            name = f"{name}-{title}"
        else:
            name = f"{name}-{random.choice(_SUFFIXES)}"

    # Handle collisions
    if name not in _WORKERS:
        return name
    n = 2
    while f"{name}-{n}" in _WORKERS:
        n += 1
    return f"{name}-{n}"


# ---------------------------------------------------------------------------
# Worker registry
# ---------------------------------------------------------------------------


_WORKERS: dict[str, object] = {}

# Worktree metadata for workers spawned with isolation="worktree".
# Maps worker_id -> {"worktree_path": str, "worktree_branch": str,
#                     "worktree_name": str, "parent_workspace": str}
_WORKER_WORKTREES: dict[str, dict[str, str]] = {}

# Display metadata for the workers panel — populated at spawn time so the
# UI can show the task description and a wall-time alongside the
# WorkerHandle's status/result/error. Keyed by worker_id; entries persist
# after worker completion so the panel can render recently-exited rows
# until the user starts a new session.
_WORKER_META: dict[str, dict] = {}

_workers_log = logging.getLogger(__name__)


def _truncate_task(task: str, limit: int = 200) -> str:
    one_line = " ".join(task.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1] + "…"


def worker_state_dict(worker_id: str) -> Optional[dict]:
    """Snapshot a single worker for the UI panel. Combines the runtime
    handle (status/result/error) with display metadata (task description,
    timestamps). Returns ``None`` when the worker_id is unknown."""
    handle = _MANAGER.workers.get(worker_id)
    if handle is None:
        return None
    meta = _WORKER_META.get(worker_id, {})
    started = meta.get("started_at")
    finished = meta.get("finished_at")
    wall_ms: Optional[int] = None
    if started is not None:
        end = finished if finished is not None else time.time()
        wall_ms = int((end - started) * 1000)
    return {
        "worker_id": worker_id,
        "status": handle.status,
        "task": meta.get("task") or "",
        "parent_session_id": getattr(handle, "parent_session_id", None),
        "session_id": getattr(handle, "session_id", ""),
        "run_id": getattr(handle, "run_id", ""),
        "result": handle.result if handle.status == "completed" else None,
        "error": handle.error if handle.status in ("error", "cancelled") else None,
        "isolation": meta.get("isolation"),
        "started_at": started,
        "finished_at": finished,
        "wall_time_ms": wall_ms,
    }


def workers_snapshot(parent_session_id: Optional[str] = None) -> list[dict]:
    """Return every worker visible to ``parent_session_id``. Untagged
    workers (test fixtures / direct invocation) are visible to every
    session so they don't get orphaned. Mirrors ``_bash_jobs``'s
    visibility contract so the two panels behave identically."""
    out: list[dict] = []
    for wid in list(_MANAGER.workers.keys()):
        state = worker_state_dict(wid)
        if state is None:
            continue
        if parent_session_id is not None:
            owner = state.get("parent_session_id")
            if owner is not None and owner != parent_session_id:
                continue
        out.append(state)
    return out


async def broadcast_workers_snapshot(
    service, parent_session_id: Optional[str]
) -> None:
    """Push the per-session workers snapshot to every connected client of
    ``parent_session_id`` via ``ui.workers.update``. Best-effort: silently
    drops on broadcast failure (the next state change will catch it up).
    """
    if service is None or not parent_session_id:
        return
    try:
        manager = getattr(service, "_session_manager", None)
        sess = None
        if manager is not None:
            sess = getattr(manager, "_by_id", {}).get(parent_session_id)
        if sess is None:
            return
        await service.request_client_function(
            "ui.workers.update",
            {"snapshot": workers_snapshot(parent_session_id=parent_session_id)},
            session=sess,
            expect_result=False,
        )
    except Exception as e:
        _workers_log.warning("ui.workers.update broadcast failed: %s", e)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_FALLBACK_PROJECT_CONTEXT: Any = None


def _resolve_project_context() -> Any:
    """Return the ProjectContext to load the worker spec against.

    Prefer the one published by ``omni serve`` (``set_project_context``)
    so the worker shares the running server's resolved registries. Fall
    back to discovering ``project.yml`` relative to this file — that path
    is used by tests and direct invocations where serve_cli never ran.
    The fallback is cached because ``load_project`` walks the agents/
    tools/capabilities directories and is too expensive to repeat per
    spawn.
    """
    from omni_code.agent_workspace_state import get_project_context

    ctx = get_project_context()
    if ctx is not None:
        return ctx

    global _FALLBACK_PROJECT_CONTEXT
    if _FALLBACK_PROJECT_CONTEXT is None:
        from omniagents.core.project.runtime import load_project

        project_yml = Path(__file__).resolve().parent.parent / "project.yml"
        _FALLBACK_PROJECT_CONTEXT = load_project(str(project_yml))
    return _FALLBACK_PROJECT_CONTEXT


def _load_and_configure_spec(model: Optional[str]):
    """Build the worker spec.

    Routes through ``build_spec_from_project`` — the same path the
    orchestrator uses (omni_code/serve_cli.py:_load_spec) — so the worker
    inherits the project's full capability_registry, tool_registry,
    guardrails_registry, server_functions_registry, agents_map, and
    project_defaults. Loading the worker YAML directly via
    ``load_agent_spec_from_yaml`` would pass capability_registry=None and
    silently skip every entry in the worker's ``capabilities:`` list,
    leaving the worker with zero tools.

    On top of that, forward the running orchestrator's sandbox attachment
    (sandbox_use_sandbox_agent / sandbox_run_as / sandbox_run_config) so
    worker tools route through the same SandboxSession in sandbox mode.
    """
    from omniagents.core.project.runtime import build_spec_from_project
    from omni_code.agent_workspace_state import get_orchestrator_spec
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = (
        resolve_model_for_runtime(model) if model else resolve_model_for_runtime()
    )

    proj = _resolve_project_context()
    spec = build_spec_from_project(proj, agent_key="worker")

    # build_spec_from_project sets project_slug from project.name — which
    # is "omni_code" — but pin it explicitly so worker sessions/traces
    # stay grouped even if the project name ever drifts.
    spec.project_slug = "omni_code"

    # Forward sandbox state from the running orchestrator. Skips silently
    # when the orchestrator hasn't published (tests, direct invocation)
    # or when the orchestrator isn't running in sandbox mode.
    orch = get_orchestrator_spec()
    if orch is not None:
        if getattr(orch, "sandbox_use_sandbox_agent", False):
            spec.sandbox_use_sandbox_agent = True
        run_as = getattr(orch, "sandbox_run_as", None)
        if run_as:
            spec.sandbox_run_as = run_as
        run_cfg = getattr(orch, "sandbox_run_config", None)
        if run_cfg is not None:
            spec.sandbox_run_config = run_cfg

    resolved_model = (
        model_runtime.get("model") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = (
        model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    spec.resolve_default_model_config = lambda: (
        model_runtime if isinstance(model_runtime, dict) else None
    )

    return spec, model_runtime


async def _on_worker_complete(handle) -> None:
    """Push the worker's completion summary to the parent (orchestrator)
    session via the batched notification path. The summary is the worker's
    full final result — the orchestrator reads it to decide what to do
    next, so no truncation. Drops silently when the worker wasn't spawned
    with a parent session (tests, direct invocation) or when the service
    capture hasn't fired yet."""
    parent_sid = getattr(handle, "parent_session_id", None)
    if not parent_sid:
        return
    try:
        from omniagents.core.agents.service import current_service
    except Exception:
        return
    service = current_service()
    if service is None:
        return
    worker_id = getattr(handle, "worker_id", "") or "?"
    # Stamp the finish time so the panel can render wall-time for exited
    # workers. ``_WORKER_META`` is the display-only sidecar populated at
    # spawn; ``handle`` is the runtime truth (status, result, error).
    meta = _WORKER_META.get(worker_id)
    if meta is not None and "finished_at" not in meta:
        meta["finished_at"] = time.time()
    status = handle.status
    summary = handle.result if status == "completed" else (handle.error or "")
    content = f"[worker] {worker_id} status={status} {summary or ''}".rstrip()
    try:
        await service.enqueue_notification(
            session_id=parent_sid,
            content=content,
            source="worker.complete",
        )
    except Exception:
        pass
    # Refresh the docked workers panel on the parent session so the row
    # flips from running → completed/cancelled/error in every connected
    # client. Best-effort: a stale panel just lingers until the user
    # hits ``/workers`` (or whatever the next state change is).
    try:
        await broadcast_workers_snapshot(service, parent_sid)
    except Exception:
        pass


_MANAGER = WorkerManager(
    spec_loader=_load_and_configure_spec,
    on_complete=_on_worker_complete,
)
_WORKERS = _MANAGER.workers


async def _history_loader(entry, tail: int) -> list[dict]:
    from omniagents.core.session.history_db import load_history

    if tail <= 0 or not entry.session_id:
        return []
    spec = getattr(entry.agent, "spec", None)
    project = getattr(spec, "project_slug", None) or "omni_code"
    agent = getattr(spec, "agent_slug", None) or getattr(spec, "name", None) or "worker"
    all_history = [
        item
        for item in load_history(
            entry.session_id,
            project_slug=project,
            agent_slug=agent,
        )
        if item.get("type") != "reasoning"
    ]
    return all_history[-tail:] if len(all_history) > tail else all_history


def _workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> str:
    """Extract workspace_root from the caller's context.

    Defers to ``omniagents.builtin.tools._workspace.require_workspace_root``
    so the error type and message line up with the framework's other
    builtin tools. The framework's ``AgentService.start_run`` is strict
    about workspace_root since v0.6.48, so by the time this code runs
    the value is guaranteed to be present unless something upstream is
    broken — this call is defense-in-depth.
    """
    from omniagents.builtin.tools._workspace import require_workspace_root

    return require_workspace_root(ctx, where="worker_tools")


# ---------------------------------------------------------------------------
# Worktree helpers
# ---------------------------------------------------------------------------


async def _is_git_repo(workspace_root: str) -> bool:
    """Check if workspace_root is inside a git repository.

    Routed through ``workspace.exec`` so the same body works against a
    SandboxWorkspace (where the git binary lives inside the sandbox).
    """
    from omniagents import workspace as _ws

    try:
        result = await _ws.exec(
            f"git -C {shlex.quote(workspace_root)} rev-parse --is-inside-work-tree",
            timeout=5.0,
            shell=True,
        )
        return result.exit_code == 0
    except Exception:
        return False


async def _is_git_dirty(workspace_root: str) -> bool:
    """Check if the workspace has uncommitted changes."""
    from omniagents import workspace as _ws

    try:
        result = await _ws.exec(
            f"git -C {shlex.quote(workspace_root)} status --porcelain",
            timeout=5.0,
            shell=True,
        )
        if result.exit_code != 0:
            return False
        return bool((result.stdout or "").strip())
    except Exception:
        return False


async def _create_worker_worktree(
    workspace_root: str, worker_id: str
) -> dict[str, str]:
    """Create a git worktree for an isolated worker.

    Works whether the supervisor is in a regular repo or an existing worktree —
    ``git worktree add`` operates on the shared repository in both cases.

    Returns dict with worktree_path, worktree_branch, worktree_name, parent_workspace.
    """
    from omniagents import workspace as _ws

    # Derive a worktree name from the workspace directory name + worker id.
    # e.g. supervisor in /workspace/bold-hawk → "bold-hawk-worker-pip"
    # e.g. supervisor in ~/projects/my-app    → "my-app-worker-pip"
    parent_name = Path(workspace_root).name
    worktree_name = f"{parent_name}-worker-{worker_id}"
    worktree_branch = f"worker/{worktree_name}"
    worktrees_dir = _worktrees_dir()
    worktree_path = os.path.join(worktrees_dir, worktree_name)

    # Existence check + mkdir via exec rather than the workspace's typed
    # methods. Reason: sandbox ``scratch_root`` lives outside the manifest
    # mount, so ``session.mkdir`` / ``session.exists`` may be blocked by
    # path policy. ``session.exec`` (which workspace.exec routes through)
    # is not path-policy checked and can read/write anywhere the shell
    # has permission. Host backend evaluates the same shell commands
    # locally, so behavior is consistent.
    async def _scratch_exists(p: str) -> bool:
        probe = await _ws.exec(f"test -e {shlex.quote(p)}", shell=True, timeout=5.0)
        return probe.exit_code == 0

    if await _scratch_exists(worktree_path):
        n = 2
        while await _scratch_exists(f"{worktree_path}-{n}"):
            n += 1
        worktree_name = f"{worktree_name}-{n}"
        worktree_branch = f"worker/{worktree_name}"
        worktree_path = os.path.join(worktrees_dir, worktree_name)

    mkdir_result = await _ws.exec(
        f"mkdir -p {shlex.quote(worktrees_dir)}", shell=True, timeout=10.0
    )
    if mkdir_result.exit_code != 0:
        raise OSError(
            f"Failed to create worktrees scratch dir {worktrees_dir!r}: "
            f"{mkdir_result.stderr or mkdir_result.stdout}"
        )

    result = await _ws.exec(
        f"git -C {shlex.quote(workspace_root)} worktree add "
        f"-b {shlex.quote(worktree_branch)} {shlex.quote(worktree_path)} HEAD",
        timeout=30.0,
        shell=True,
    )
    if result.exit_code != 0:
        raise subprocess.CalledProcessError(
            result.exit_code,
            ["git", "worktree", "add"],
            output=(result.stdout or "").encode("utf-8"),
            stderr=(result.stderr or "").encode("utf-8"),
        )

    return {
        "worktree_path": worktree_path,
        "worktree_branch": worktree_branch,
        "worktree_name": worktree_name,
        "parent_workspace": workspace_root,
    }


async def _remove_worker_worktree(worker_id: str) -> bool:
    """Remove a worker's worktree and branch. Returns True if cleanup succeeded."""
    info = _WORKER_WORKTREES.get(worker_id)
    if info is None:
        return False

    parent = info["parent_workspace"]
    wt_path = info["worktree_path"]
    wt_name = info["worktree_name"]
    branch = info["worktree_branch"]

    from omniagents import workspace as _ws

    try:
        await _ws.exec(
            f"git -C {shlex.quote(parent)} worktree remove --force {shlex.quote(wt_path)}",
            timeout=10.0,
            shell=True,
        )
    except Exception:
        pass

    try:
        await _ws.exec(
            f"git -C {shlex.quote(parent)} branch -D {shlex.quote(branch)}",
            timeout=5.0,
            shell=True,
        )
    except Exception:
        pass

    _WORKER_WORKTREES.pop(worker_id, None)
    return True


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


@rich_function_tool(client_status="Spawning worker...")
async def spawn_worker(
    ctx: RunContextWrapper[Any],
    task: str,
    isolation: Optional[str] = None,
    allow_dirty: bool = False,
) -> RichToolOutput:
    """Spawn an autonomous worker agent to execute a coding subtask in the background.

    Workers have full read/write access to the workspace (list_directory, glob_files,
    grep_files, read_file, apply_patch, write_file, execute_bash, etc.) and run
    concurrently with the orchestrator and other workers.

    Use this to parallelise independent subtasks — for example, implementing
    separate files, running independent refactors, or writing tests while another
    worker builds the feature.

    When the worker completes, an automatic notification with its status and
    result is delivered to this session — the next turn starts with that
    available in context. You can end the current turn after spawning; you
    don't need to call wait_for_workers. Spawn multiple workers in the same
    turn and their completions will be batched into one wakeup.

    Returns a short worker ID. Use it with send_worker_message (steer mid-run),
    get_worker_status (peek without blocking), close_worker (cancel), or
    resume_worker (restart with new instructions). wait_for_workers exists for
    the rare case you need to block synchronously within the same turn.

    Args:
        task: A precise, self-contained description of the subtask. Include all
              context the worker needs (file paths, function names, requirements)
              because the worker has no access to conversation history.
        isolation: Set to "worktree" to give the worker its own git worktree
              branching from the current HEAD. The worker can commit freely
              without affecting the main workspace. After the completion
              notification arrives, merge the branch and call
              cleanup_worker_worktree to remove the worktree. Requires the
              workspace to be a git repository.
        allow_dirty: Only relevant when isolation="worktree". If the workspace
              has uncommitted changes, worktree creation fails by default because
              the worker would not see those changes. Set to True to proceed
              anyway when you know the uncommitted changes are not relevant.
    """
    worker_id = _generate_worker_name()
    workspace_root = _workspace_root_from_ctx(ctx)

    worktree_info: dict[str, str] | None = None
    worker_workspace = workspace_root

    if isolation == "worktree":
        if not await _is_git_repo(workspace_root):
            llm_output = json.dumps(
                {
                    "worker_id": worker_id,
                    "status": "error",
                    "error": "Cannot create worktree: workspace is not a git repository.",
                }
            )
            return RichToolOutput(
                llm_output,
                {
                    "display_type": "error",
                    "summary": "Worktree isolation failed",
                    "preview": "Workspace is not a git repository.",
                    "truncated": False,
                    "metadata": {"success": False, "worker_id": worker_id},
                },
            )

        try:
            worktree_info = await _create_worker_worktree(workspace_root, worker_id)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)
            llm_output = json.dumps(
                {
                    "worker_id": worker_id,
                    "status": "error",
                    "error": f"Failed to create worktree: {stderr.strip()}",
                }
            )
            return RichToolOutput(
                llm_output,
                {
                    "display_type": "error",
                    "summary": "Worktree creation failed",
                    "preview": stderr.strip()[:200],
                    "truncated": False,
                    "metadata": {"success": False, "worker_id": worker_id},
                },
            )

        _WORKER_WORKTREES[worker_id] = worktree_info
        worker_workspace = worktree_info["worktree_path"]

        # Prepend worktree context to the task so the worker knows to commit.
        task = (
            f"[You are working in an isolated git worktree at {worker_workspace} "
            f"on branch {worktree_info['worktree_branch']}. "
            f"Commit your changes to this branch when done so they can be merged.]\n\n"
            f"{task}"
        )

        # The worker's worktree is checked out from HEAD and will not contain
        # any uncommitted changes. Fail unless the LLM opts in with allow_dirty.
        if not allow_dirty and await _is_git_dirty(workspace_root):
            # Roll back the worktree we just created.
            await _remove_worker_worktree(worker_id)
            llm_output = json.dumps(
                {
                    "worker_id": worker_id,
                    "status": "error",
                    "error": (
                        "Workspace has uncommitted changes that would not be visible "
                        "in the worker's worktree. Commit your changes first, or set "
                        "allow_dirty=True if the uncommitted changes are not relevant."
                    ),
                }
            )
            return RichToolOutput(
                llm_output,
                {
                    "display_type": "error",
                    "summary": "Worktree isolation failed: dirty workspace",
                    "preview": "Uncommitted changes would not be visible to the worker.",
                    "truncated": False,
                    "metadata": {"success": False, "worker_id": worker_id},
                },
            )

    # Capture the orchestrator session id so worker completion notices
    # land in the right session. Reuses bash_tool's canonical accessor
    # for parity with how Job.session_id is captured.
    from tools.bash_tool import _session_id_from_ctx
    parent_sid = _session_id_from_ctx(ctx)

    await _MANAGER.spawn(
        worker_id=worker_id,
        task_description=task,
        context={"workspace_root": worker_workspace, "task_scope": worker_id},
        parent_session_id=parent_sid,
    )

    # Record display metadata + broadcast to the workers panel so the row
    # appears immediately in every connected client rather than waiting
    # for the next user action. The handle in ``_MANAGER.workers`` is the
    # runtime truth; ``_WORKER_META`` is the display sidecar.
    _WORKER_META[worker_id] = {
        "task": _truncate_task(task),
        "started_at": time.time(),
        "isolation": isolation,
        "finished_at": None,
    }
    try:
        from omniagents.core.agents.service import current_service

        _svc = current_service()
        if _svc is not None and parent_sid:
            await broadcast_workers_snapshot(_svc, parent_sid)
    except Exception:
        pass

    response: dict[str, Any] = {
        "worker_id": worker_id,
        "status": "spawned",
        "hint": (
            "Worker is running asynchronously. A completion notification with "
            "its status and result will arrive automatically — you can end "
            "this turn after spawning. Use wait_for_workers only if you need "
            "to block synchronously within the current turn."
        ),
    }
    if worktree_info:
        response["worktree_path"] = worktree_info["worktree_path"]
        response["worktree_branch"] = worktree_info["worktree_branch"]
        response["hint"] = (
            f"Worker is running asynchronously in worktree "
            f"{worktree_info['worktree_path']} on branch "
            f"{worktree_info['worktree_branch']}. A completion notification "
            f"will arrive automatically — end this turn after spawning, then "
            f"in the turn that wakes you, merge the branch and call "
            f"cleanup_worker_worktree({worker_id})."
        )

    llm_output = json.dumps(response)
    task_summary = task[:80] + "..." if len(task) > 80 else task
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} spawned",
            "preview": f"Task: {task_summary}",
            "truncated": False,
            "metadata": {
                "success": True,
                "worker_id": worker_id,
                "status": "spawned",
                "isolation": isolation,
            },
        },
    )


@rich_function_tool(client_status="Waiting for workers...")
async def wait_for_workers(
    worker_ids: list[str],
    timeout_seconds: Optional[float] = None,
) -> RichToolOutput:
    """Wait for one or more workers to finish and return their results.

    Usually unnecessary: every worker completion sends an automatic
    notification to this session that wakes the next turn with the
    result in context. Prefer ending your turn after spawning and
    letting the notification bring you back. Use this tool only when
    you need to block synchronously within the current turn — for
    example, when a single response must include results from multiple
    workers and ending the turn early would split that response.

    If any listed workers are already done, their results are returned
    immediately. Otherwise blocks until at least one running worker completes
    (or the timeout expires).

    Args:
        worker_ids: List of worker IDs returned by spawn_worker.
        timeout_seconds: Max seconds to wait. None means wait indefinitely.
    """
    result_data = await _MANAGER.wait(worker_ids, timeout_seconds)
    completed = result_data["completed"]
    still_running = result_data["still_running"]
    not_found = result_data["not_found"]

    if still_running:
        result_data["hint"] = (
            f"Workers still running: {', '.join(still_running)}. "
            "Call wait_for_workers again with the remaining IDs to collect their results."
        )

    # Attach worktree info for completed workers that used isolation.
    worktrees: dict[str, dict[str, str]] = {}
    for wid in completed:
        wt = _WORKER_WORKTREES.get(wid)
        if wt:
            worktrees[wid] = {
                "worktree_path": wt["worktree_path"],
                "worktree_branch": wt["worktree_branch"],
            }
    if worktrees:
        result_data["worktrees"] = worktrees

    llm_output = json.dumps(result_data)

    # Build a concise preview for the UI
    preview_lines = []
    for wid, res in completed.items():
        res_str = res or ""
        first_lines = res_str[:200] + ("..." if len(res_str) > 200 else "")
        preview_lines.append(f"[{wid}] {first_lines}")
    if still_running:
        preview_lines.append(f"Still running: {', '.join(still_running)}")
    if not_found:
        preview_lines.append(f"Not found: {', '.join(not_found)}")
    preview = "\n".join(preview_lines)

    parts = []
    if completed:
        parts.append(f"{len(completed)} completed")
    if still_running:
        parts.append(f"{len(still_running)} running")
    if not_found:
        parts.append(f"{len(not_found)} not found")
    summary = ", ".join(parts) if parts else "No workers matched"

    return RichToolOutput(
        llm_output,
        {
            "display_type": "search_results",
            "summary": f"Workers: {summary}",
            "preview": preview,
            "truncated": any(len(r or "") > 200 for r in completed.values()),
            "metadata": {
                "success": bool(completed),
                "completed_count": len(completed),
                "still_running_count": len(still_running),
                "not_found_count": len(not_found),
            },
        },
    )


@rich_function_tool(client_status="Checking worker status...")
async def get_worker_status(
    worker_id: str,
    tail: int = 5,
) -> RichToolOutput:
    """Check a worker's current status and optionally retrieve recent history.

    Non-blocking — returns immediately with whatever state the worker is in.

    Useful for peeking at progress mid-turn without waiting, or for
    fetching the full history tail after a completion notification
    arrived (the notification carries only the final status and a short
    summary; deeper inspection lives here).

    Args:
        worker_id: The worker ID returned by spawn_worker.
        tail: Number of recent history items to include (default: 5).
              Each item is a message dict (role, content, tool calls, etc.).
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    result_data = await _MANAGER.get_status(
        worker_id, tail=tail, history_loader=_history_loader
    )
    history_items = result_data.get("history", [])

    if history_items:
        result_data["history"] = history_items

    # Include worktree info if present
    wt = _WORKER_WORKTREES.get(worker_id)
    if wt:
        result_data["worktree_path"] = wt["worktree_path"]
        result_data["worktree_branch"] = wt["worktree_branch"]

    llm_output = json.dumps(result_data)

    # Build preview
    preview_lines = [f"Status: {entry.status}"]
    if history_items:
        preview_lines.append(f"History: {len(history_items)} items")
    preview = "\n".join(preview_lines)

    return RichToolOutput(
        llm_output,
        {
            "display_type": "search_results",
            "summary": f"Worker {worker_id}: {entry.status}",
            "preview": preview,
            "truncated": False,
            "metadata": {
                "success": True,
                "worker_id": worker_id,
                "status": entry.status,
                "history_count": len(history_items),
            },
        },
    )


@rich_function_tool(client_status="Sending message to worker...")
async def send_worker_message(worker_id: str, message: str) -> RichToolOutput:
    """Send a message to a running worker, injecting new instructions mid-task.

    Use this to course-correct a worker (e.g. "stop working on X and focus on Y")
    or to supply additional context it needs. The message is appended to the
    worker's conversation as a user turn; the worker will see it on its next
    reasoning step.

    Args:
        worker_id: The worker ID returned by spawn_worker.
        message: The message to send to the worker.
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    if entry.status != "running":
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": entry.status,
                "error": "Worker is not running.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} is {entry.status}",
                "preview": llm_output,
                "truncated": False,
                "metadata": {
                    "success": False,
                    "worker_id": worker_id,
                    "status": entry.status,
                },
            },
        )

    send_status = await _MANAGER.send_message(worker_id, message)
    if send_status != "message_sent":
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": send_status,
                "error": "Worker is not running.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} is {send_status}",
                "preview": llm_output,
                "truncated": False,
                "metadata": {
                    "success": False,
                    "worker_id": worker_id,
                    "status": send_status,
                },
            },
        )
    llm_output = json.dumps({"worker_id": worker_id, "status": "message_sent"})
    msg_summary = message[:60] + "..." if len(message) > 60 else message
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Message sent to worker {worker_id}",
            "preview": f"Message: {msg_summary}",
            "truncated": False,
            "metadata": {
                "success": True,
                "worker_id": worker_id,
                "status": "message_sent",
            },
        },
    )


@rich_function_tool(client_status="Closing worker...")
async def close_worker(worker_id: str) -> RichToolOutput:
    """Cancel a running worker and clean up its resources.

    If the worker was spawned with isolation="worktree", the worktree is
    preserved so you can inspect or merge partial work. Call
    cleanup_worker_worktree when you are done with it.

    Args:
        worker_id: The worker ID returned by spawn_worker.
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    if entry.status != "running":
        llm_output = json.dumps({"worker_id": worker_id, "status": entry.status})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "command",
                "summary": f"Worker {worker_id} already {entry.status}",
                "preview": llm_output,
                "truncated": False,
                "metadata": {
                    "success": True,
                    "worker_id": worker_id,
                    "status": entry.status,
                },
            },
        )

    status = await _MANAGER.close(worker_id)

    response: dict[str, Any] = {"worker_id": worker_id, "status": status}
    wt = _WORKER_WORKTREES.get(worker_id)
    if wt:
        response["worktree_path"] = wt["worktree_path"]
        response["worktree_branch"] = wt["worktree_branch"]

    llm_output = json.dumps(response)
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} {status}",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": True, "worker_id": worker_id, "status": status},
        },
    )


@rich_function_tool(client_status="Resuming worker...")
async def resume_worker(
    ctx: RunContextWrapper[Any], worker_id: str, message: str
) -> RichToolOutput:
    """Resume a previously closed or completed worker with a new message.

    Restores the worker's full conversation history and starts a new run.
    Use this to iterate on a worker's output — for example, asking it to fix
    issues you found in its previous work.

    Args:
        worker_id: The worker ID of the closed/completed worker.
        message: The follow-up instruction for the worker.
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    if entry.status == "running":
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": "error",
                "error": "Worker is still running. Use send_worker_message instead.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} still running",
                "preview": "Use send_worker_message instead.",
                "truncated": False,
                "metadata": {
                    "success": False,
                    "worker_id": worker_id,
                    "status": "running",
                },
            },
        )

    # If the worker has an existing worktree, reuse it.
    wt = _WORKER_WORKTREES.get(worker_id)
    if wt:
        workspace_root = wt["worktree_path"]
    else:
        workspace_root = _workspace_root_from_ctx(ctx)

    new_entry = await _MANAGER.resume(
        worker_id,
        message,
        context={"workspace_root": workspace_root, "task_scope": worker_id},
    )
    if new_entry is None:
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": "error",
                "error": "Failed to resume worker.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} failed to resume",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    response: dict[str, Any] = {
        "worker_id": worker_id,
        "status": "resumed",
        "session_id": new_entry.session_id,
    }
    if wt:
        response["worktree_path"] = wt["worktree_path"]
        response["worktree_branch"] = wt["worktree_branch"]

    llm_output = json.dumps(response)
    msg_summary = message[:60] + "..." if len(message) > 60 else message
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} resumed",
            "preview": f"Task: {msg_summary}",
            "truncated": False,
            "metadata": {"success": True, "worker_id": worker_id, "status": "resumed"},
        },
    )


@rich_function_tool(client_status="Cleaning up worker worktree...")
async def cleanup_worker_worktree(worker_id: str) -> RichToolOutput:
    """Remove a worker's git worktree and branch after merging its changes.

    Call this after you have merged the worker's branch into your workspace.
    It removes the worktree directory and deletes the worker branch.

    Args:
        worker_id: The worker ID whose worktree should be cleaned up.
    """
    wt = _WORKER_WORKTREES.get(worker_id)
    if wt is None:
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": "no_worktree",
                "error": "No worktree found for this worker.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"No worktree for worker {worker_id}",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    worktree_path = wt["worktree_path"]
    worktree_branch = wt["worktree_branch"]
    await _remove_worker_worktree(worker_id)

    llm_output = json.dumps(
        {
            "worker_id": worker_id,
            "status": "cleaned_up",
            "removed_path": worktree_path,
            "removed_branch": worktree_branch,
        }
    )
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} worktree cleaned up",
            "preview": f"Removed {worktree_path} and branch {worktree_branch}",
            "truncated": False,
            "metadata": {"success": True, "worker_id": worker_id},
        },
    )


# ---------------------------------------------------------------------------
# Approval metadata
# ---------------------------------------------------------------------------


@approval_metadata()
def spawn_worker_approval(
    task: str, isolation: Optional[str] = None, allow_dirty: bool = False
) -> dict:
    """Generate approval metadata for spawn_worker showing the full task description."""
    lines = task.splitlines()
    preview = task
    summary = "Spawn worker agent"
    if isolation == "worktree":
        summary = "Spawn worker agent (isolated worktree)"

    return {
        "display_type": "file_write",
        "summary": summary,
        "value": task,
        "preview": preview,
        "truncated": False,
        "metadata": {
            "operation": "spawn_worker",
            "line_count": len(lines),
            "isolation": isolation,
        },
    }
