"""Worker lifecycle tools.

Spawn autonomous worker sub-agents that execute a task in the background,
optionally in an isolated workspace, and manage them: wait, status,
steer, cancel, resume, clean up.

The run loop is ``omniagents.core.workers.WorkerManager``; *how* a worker
spec is built and *how* its workspace is isolated come from the product
via ``set_worker_defaults`` and the isolation-strategy registry (see
``omniagents.core.workers``). Without a configured ``spec_loader``,
``spawn_worker`` fails with a clear error.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Callable, Dict, Optional

from agents.run_context import RunContextWrapper
from omniagents.core.tools import (
    RichToolOutput,
    approval_metadata,
    rich_function_tool,
    session_id_from_ctx,
)
from omniagents.core.workers import (
    IsolationError,
    WorkerManager,
    WorkerWorkspace,
    get_isolation_strategy,
    worker_defaults,
)

_workers_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Whimsical worker name generator (override via WorkerDefaults.namer)
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


def _builtin_namer(existing: set) -> str:
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

    if name not in existing:
        return name
    n = 2
    while f"{name}-{n}" in existing:
        n += 1
    return f"{name}-{n}"


# Every name ever issued this process. The manager registers a spawned
# handle only at the END of spawn — several awaits later — so concurrent
# spawns (parallel_tool_calls batches) would otherwise pick against the
# same snapshot and collide, with the second handle silently overwriting
# the first. Reserving here is atomic (no await between choose and add),
# and ids are never re-issued: a failed spawn's name staying burned is
# cheaper than its meta/worktree leftovers being adopted by a stranger.
_ISSUED_WORKER_IDS: set = set()


def _generate_worker_name() -> str:
    namer = worker_defaults().namer or _builtin_namer
    name = namer(set(_workers().keys()) | _ISSUED_WORKER_IDS)
    _ISSUED_WORKER_IDS.add(name)
    return name


# ---------------------------------------------------------------------------
# Manager (lazy — needs the product's spec_loader) and registries
# ---------------------------------------------------------------------------

_MANAGER: Optional[WorkerManager] = None

# Isolated workspaces for workers spawned with isolation != "none".
# Keyed by worker_id; metadata is merged into tool payloads so the
# orchestrator sees e.g. worktree paths/branches to integrate.
_WORKER_WORKSPACES: dict[str, WorkerWorkspace] = {}

# Which strategy isolated each worker (for cleanup).
_WORKER_ISOLATION: dict[str, str] = {}

# Environments registered on the shared AgentHost for isolated workers
# (worktree/copy roots). Keyed by worker_id; retired at cleanup so dead
# worktree environments don't accumulate on the host. Shared-workspace
# workers lease the parent's environment and have no entry here.
_WORKER_ENVIRONMENTS: dict[str, str] = {}

# Display metadata for the workers panel — populated at spawn time so the
# UI can show the task description and a wall-time alongside the
# WorkerHandle's status/result/error. Keyed by worker_id; entries persist
# after worker completion so the panel can render recently-exited rows
# until the user starts a new session.
_WORKER_META: dict[str, dict] = {}


def manager() -> WorkerManager:
    """The process-wide WorkerManager, created on first use from the
    product's ``WorkerDefaults.spec_loader``."""
    global _MANAGER
    if _MANAGER is None:
        defaults = worker_defaults()
        if defaults.spec_loader is None:
            raise RuntimeError(
                "No worker spec_loader configured. The product must call "
                "omniagents.core.workers.set_worker_defaults(WorkerDefaults("
                "spec_loader=...)) before workers can be spawned."
            )
        _MANAGER = WorkerManager(
            spec_loader=defaults.spec_loader,
            on_complete=_on_worker_complete,
            on_event=_relay_worker_event,
            on_session_event=_mirror_worker_event,
            host_resources_provider=_host_resources,
        )
    return _MANAGER


def _host_resources():
    """The hosting AgentService's AgentHostResources, or None.

    Workers join the parent AgentHost's registries: their runs route
    through the same workspaces/environments as every other operation in
    the process."""
    try:
        from omniagents.core.agents.service import current_service

        svc = current_service()
    except Exception:
        return None
    return getattr(svc, "agent_host_resources", None) if svc is not None else None


def _workers() -> dict:
    """Live worker handles, or {} before the manager exists."""
    return _MANAGER.workers if _MANAGER is not None else {}


def _truncate_task(task: str, limit: int = 2000) -> str:
    # Generous cap: clients decide how much to SHOW (list rows truncate,
    # detail panes wrap everything); this only bounds the payload.
    one_line = " ".join(task.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1] + "…"


def _workspace_payload(worker_id: str) -> Dict[str, Any]:
    """Isolation metadata to merge into a tool payload (empty for
    non-isolated workers)."""
    ws = _WORKER_WORKSPACES.get(worker_id)
    return dict(ws.metadata) if ws is not None else {}


def worker_state_dict(worker_id: str) -> Optional[dict]:
    """Snapshot a single worker for the UI panel. Combines the runtime
    handle (status/result/error) with display metadata (task description,
    timestamps). Returns ``None`` when the worker_id is unknown."""
    handle = _workers().get(worker_id)
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
        "subagent_id": worker_id,
        "kind": "worker",
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
    for wid in list(_workers().keys()):
        state = worker_state_dict(wid)
        if state is None:
            continue
        if parent_session_id is not None:
            owner = state.get("parent_session_id")
            if owner is not None and owner != parent_session_id:
                continue
        out.append(state)
    return out


async def broadcast_workers_snapshot(service, parent_session_id: Optional[str]) -> None:
    """Push the merged subagent snapshot (``ui.subagents.update``) to every
    connected client of ``parent_session_id``. Workers contribute entries
    via the bus provider registered below; the bus merges in agent-tool
    runs. Best-effort: silently drops on broadcast failure (the next state
    change will catch it up)."""
    from omniagents.core.runtime.subagents import broadcast_subagents_snapshot

    await broadcast_subagents_snapshot(service, parent_session_id)


# Workers contribute their entries to the unified subagent snapshot.
from omniagents.core.runtime.subagents import (
    register_snapshot_provider as _register_snapshot_provider,
)

_register_snapshot_provider(
    lambda parent_session_id: workers_snapshot(parent_session_id=parent_session_id)
)


async def _mirror_worker_event(handle, method: str, params: dict) -> None:
    """Deliver one worker session event — verbatim, already stamped and
    journaled by the worker's own service — to clients attached to that
    session on THIS serve (the launcher's read-only transcript viewer).

    The event journal lives in the shared per-(project, agent) store, so
    the stamps and ``ack_events`` stay coherent. Events not mirrored
    (approvals, client_requests) are recovered from the journal when the
    client resumes over a sequence gap. Best-effort: a dropped mirror only
    costs display latency, never correctness.
    """
    session_id = getattr(handle, "session_id", None)
    if not session_id:
        return
    try:
        from omniagents.core.agents.service import current_service

        service = current_service()
    except Exception:
        return
    if service is None:
        return
    sess = service.get_session(session_id)
    if sess is None:
        return
    for ch in list(getattr(sess, "channels", None) or []):
        try:
            await service._safe_notify(ch, method, params)
        except Exception:
            pass


async def _relay_worker_event(handle, method: str, params: dict) -> None:
    """Forward one worker transcript event to the parent session via the
    subagent bus (``ui.subagent.event``, kind="worker"). Best-effort: a
    dropped event only costs display fidelity."""
    from omniagents.core.runtime.subagents import emit_subagent_event

    await emit_subagent_event(
        getattr(handle, "parent_session_id", None),
        getattr(handle, "worker_id", ""),
        "worker",
        method,
        params,
    )


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
    # Frame the wakeup so the model has a clear directive. Without this,
    # an assistant-role notification with no instructions reads as the
    # agent's own prior utterance — and the model defaults to a near-
    # identical re-acknowledgment of whatever it last said.
    body = f"[worker] {worker_id} status={status} {summary or ''}".rstrip()
    content = (
        f"{body}\n\n"
        "Decide what to do next. If this completes the user's request, "
        "summarize briefly and stop. Otherwise take the next concrete step."
    )
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


async def _default_history_loader(entry, tail: int) -> list[dict]:
    from omniagents.core.session.history_db import load_history

    if tail <= 0 or not entry.session_id:
        return []
    spec = getattr(entry.agent, "spec", None)
    project = getattr(spec, "project_slug", None)
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


def _history_loader():
    return worker_defaults().history_loader or _default_history_loader


def _environment_id_from_ctx(ctx: RunContextWrapper[Any] | None) -> str | None:
    """The parent run's routed environment id, from its lease-populated
    context. None when the caller runs without an environment."""
    if ctx is None:
        return None
    c = getattr(ctx, "context", None)
    value = (
        c.get("environment_id")
        if isinstance(c, dict)
        else getattr(c, "environment_id", None)
    )
    return value if isinstance(value, str) and value.strip() else None


def _register_worktree_environment(worker_id: str, root: str) -> str | None:
    """Register an isolated worker root as a first-class environment on
    the shared AgentHost. Returns its id, or None without a host."""
    resources = _host_resources()
    if resources is None:
        return None
    from omniagents.execution.host import register_host_environment

    descriptor = register_host_environment(
        resources,
        root,
        owner_user_id=None,
        workspace_id=f"workspace-worker-{worker_id}",
        environment_id=f"environment-worker-{worker_id}",
    )
    _WORKER_ENVIRONMENTS[worker_id] = descriptor.environment_id
    return descriptor.environment_id


async def _retire_worker_environment(worker_id: str) -> None:
    """Best-effort removal of a worker's worktree environment from the
    shared AgentHost (leases already held stay valid)."""
    environment_id = _WORKER_ENVIRONMENTS.pop(worker_id, None)
    if environment_id is None:
        return
    resources = _host_resources()
    if resources is None:
        return
    try:
        await resources.environments.retire(environment_id)
    except Exception:
        pass


def _workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> str:
    """Extract workspace_root from the caller's context.

    Defers to ``omniagents.builtin.tools._workspace.require_workspace_root``
    so the error type and message line up with the framework's other
    builtin tools."""
    from ._workspace import require_workspace_root

    return require_workspace_root(ctx, where="worker_tools")


def _error_output(
    worker_id: str, error: str, summary: str, preview: str
) -> RichToolOutput:
    llm_output = json.dumps({"worker_id": worker_id, "status": "error", "error": error})
    return RichToolOutput(
        llm_output,
        {
            "display_type": "error",
            "summary": summary,
            "preview": preview,
            "truncated": False,
            "metadata": {"success": False, "worker_id": worker_id},
        },
    )


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


@rich_function_tool(running_summary="Spawning worker...")
async def spawn_worker(
    ctx: RunContextWrapper[Any],
    task: str,
    isolation: Optional[str] = None,
    allow_dirty: bool = False,
) -> RichToolOutput:
    """Spawn an autonomous worker agent to execute a subtask in the background.

    Workers have their own tools and workspace access and run concurrently
    with the orchestrator and other workers.

    Use this to parallelise independent subtasks — separate deliverables,
    independent transformations, or verification running alongside
    production work.

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
              context the worker needs (paths, names, requirements) because the
              worker has no access to conversation history.
        isolation: Name of an isolation strategy to give the worker its own
              isolated workspace instead of sharing yours. What strategies
              exist depends on the deployment (e.g. "worktree" in git-based
              setups, "copy" for a plain directory copy). After the
              completion notification arrives, integrate the worker's
              changes and call cleanup_worker_worktree to remove the
              isolated workspace.
        allow_dirty: Only relevant with isolation. If the parent workspace
              has un-captured changes the worker would not see, isolation
              fails by default. Set to True to proceed anyway when you know
              those changes are not relevant.
    """
    return await spawn_worker_run(ctx, task, isolation, allow_dirty)


async def spawn_worker_run(
    ctx: RunContextWrapper[Any],
    task: str,
    isolation: Optional[str] = None,
    allow_dirty: bool = False,
    *,
    worker_id: Optional[str] = None,
    configure_spec: Optional[Callable[[Any], None]] = None,
) -> RichToolOutput:
    """Internal execution adapter shared by worker and swarm capabilities.

    The public worker tool schema and defaults remain unchanged.
    """
    worker_id = worker_id or _generate_worker_name()
    if worker_id in _workers():
        return _error_output(
            worker_id, "Worker already exists", "Duplicate worker", worker_id
        )
    workspace_root = _workspace_root_from_ctx(ctx)

    defaults = worker_defaults()
    strategy_name = isolation if isolation is not None else defaults.default_isolation
    try:
        strategy = get_isolation_strategy(strategy_name or "none")
    except KeyError as e:
        return _error_output(worker_id, str(e), "Unknown isolation strategy", str(e))

    worker_workspace_obj: Optional[WorkerWorkspace] = None
    worker_workspace = workspace_root

    if strategy.name != "none":
        if not await strategy.ready(workspace_root):
            return _error_output(
                worker_id,
                strategy.unavailable_message,
                f"{strategy.name} isolation failed",
                strategy.unavailable_message,
            )

        try:
            worker_workspace_obj = await strategy.prepare(
                worker_id=worker_id, parent_workspace_root=workspace_root
            )
        except IsolationError as e:
            return _error_output(
                worker_id, str(e), f"{strategy.name} isolation failed", str(e)[:200]
            )

        _WORKER_WORKSPACES[worker_id] = worker_workspace_obj
        _WORKER_ISOLATION[worker_id] = strategy.name
        worker_workspace = worker_workspace_obj.root

        preamble = strategy.task_preamble(worker_workspace_obj)
        if preamble:
            task = f"{preamble}\n\n{task}"

        # The isolated workspace is created from the parent's last captured
        # state and will not contain un-captured changes. Fail unless the
        # caller opts in with allow_dirty.
        if not allow_dirty and await strategy.is_parent_dirty(workspace_root):
            # Roll back the workspace we just created.
            await strategy.cleanup(worker_id, worker_workspace_obj)
            _WORKER_WORKSPACES.pop(worker_id, None)
            _WORKER_ISOLATION.pop(worker_id, None)
            return _error_output(
                worker_id,
                (
                    "Workspace has uncommitted changes that would not be visible "
                    "in the worker's worktree. Commit your changes first, or set "
                    "allow_dirty=True if the uncommitted changes are not relevant."
                ),
                f"{strategy.name} isolation failed: dirty workspace",
                "Uncommitted changes would not be visible to the worker.",
            )

    # Capture the orchestrator session id so worker completion notices
    # land in the right session. Uses the framework's canonical accessor
    # for parity with how Job.session_id is captured.
    parent_sid = session_id_from_ctx(ctx)

    # The worker acts AS the user who owns the parent session: resolve the
    # driving channel's principal (falling back to the parent session's
    # persisted owner) so the worker's session is created owned by that
    # user. Without this, enforced-authorization deployments reject the
    # owner's own reads of the worker thread ("Not authorized to access
    # this thread") because internally spawned sessions were unowned.
    parent_principal = None
    try:
        from omniagents.core.agents.service import current_service

        _svc = current_service()
    except Exception:
        _svc = None
    parent_project_slug = None
    parent_agent_slug = None
    if _svc is not None:
        parent_principal = getattr(getattr(_svc, "channel", None), "principal", None)
        if parent_principal is None and parent_sid:
            _owner = getattr(_svc.get_session(parent_sid), "user_id", None)
            if isinstance(_owner, str) and _owner:
                from omniagents.core.providers.auth.base import UserIdentity

                parent_principal = UserIdentity(user_id=_owner)
        # Workers persist in the parent's session namespace so the parent
        # serve can authorize AND read their threads (owner rows and items
        # live in the per-(project, agent) store the parent looks in).
        _mgr = getattr(_svc, "_session_manager", None)
        parent_project_slug = getattr(_mgr, "_project_slug", None)
        parent_agent_slug = getattr(_mgr, "_agent_slug", None)

    # The worker's run must select a real environment on the shared
    # AgentHost: the parent's own environment when sharing the workspace,
    # or the isolated root registered as a fresh environment. No
    # environment reachable -> the worker runs targetless (thinking-only).
    if worker_workspace_obj is not None:
        environment_id = _register_worktree_environment(worker_id, worker_workspace)
    else:
        environment_id = _environment_id_from_ctx(ctx)

    spawn_options: dict[str, Any] = {}
    if configure_spec is not None:
        spawn_options["configure_spec"] = configure_spec

    try:
        await manager().spawn(
            worker_id=worker_id,
            task_description=task,
            context={
                "workspace_root": worker_workspace,
                "task_scope": worker_id,
                "environment_id": environment_id,
            },
            parent_session_id=parent_sid,
            principal=parent_principal,
            parent_service=_svc,
            project_slug=parent_project_slug,
            agent_slug=parent_agent_slug,
            **spawn_options,
        )
    except Exception as e:
        # The worktree was prepared above; a failed spawn must not leak it —
        # leftovers ride into the workspace snapshot tar and inflate every
        # rehydrate from then on.
        try:
            if worker_workspace_obj is not None:
                await strategy.cleanup(worker_id, worker_workspace_obj)
        except Exception:
            pass
        await _retire_worker_environment(worker_id)
        _WORKER_WORKSPACES.pop(worker_id, None)
        _WORKER_ISOLATION.pop(worker_id, None)
        return _error_output(
            worker_id, str(e), f"Failed to spawn worker {worker_id}", str(e)[:200]
        )

    # Record display metadata + broadcast to the workers panel so the row
    # appears immediately in every connected client rather than waiting
    # for the next user action. The handle in the manager is the runtime
    # truth; ``_WORKER_META`` is the display sidecar.
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
    if worker_workspace_obj is not None:
        response.update(_workspace_payload(worker_id))
        response["hint"] = (
            f"Worker is running asynchronously in an isolated workspace at "
            f"{worker_workspace_obj.root}. A completion notification "
            f"will arrive automatically — end this turn after spawning, then "
            f"in the turn that wakes you, integrate its changes and call "
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


@rich_function_tool(running_summary="Waiting for workers...")
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
    result_data = await manager().wait(worker_ids, timeout_seconds)
    completed = result_data["completed"]
    still_running = result_data["still_running"]
    not_found = result_data["not_found"]

    if still_running:
        result_data["hint"] = (
            f"Workers still running: {', '.join(still_running)}. "
            "Call wait_for_workers again with the remaining IDs to collect their results."
        )

    # Attach isolated-workspace info for completed workers that used isolation.
    worktrees: dict[str, dict[str, Any]] = {}
    for wid in completed:
        payload = _workspace_payload(wid)
        if payload:
            worktrees[wid] = payload
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


@rich_function_tool(running_summary="Checking worker status...")
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
    entry = _workers().get(worker_id)
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

    result_data = await manager().get_status(
        worker_id, tail=tail, history_loader=_history_loader()
    )
    history_items = result_data.get("history", [])

    if history_items:
        result_data["history"] = history_items

    # Include isolated-workspace info if present
    result_data.update(_workspace_payload(worker_id))

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


@rich_function_tool(running_summary="Sending message to worker...")
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
    entry = _workers().get(worker_id)
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

    send_status = await manager().send_message(worker_id, message)
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


@rich_function_tool(running_summary="Closing worker...")
async def close_worker(worker_id: str) -> RichToolOutput:
    """Cancel a running worker and clean up its resources.

    If the worker was spawned with isolation, its isolated workspace is
    preserved so you can inspect or integrate partial work. Call
    cleanup_worker_worktree when you are done with it.

    Args:
        worker_id: The worker ID returned by spawn_worker.
    """
    entry = _workers().get(worker_id)
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

    status = await manager().close(worker_id)

    response: dict[str, Any] = {"worker_id": worker_id, "status": status}
    response.update(_workspace_payload(worker_id))

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


@rich_function_tool(running_summary="Resuming worker...")
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
    entry = _workers().get(worker_id)
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

    # If the worker has an existing isolated workspace, reuse it (and its
    # registered environment); otherwise share the parent's.
    ws = _WORKER_WORKSPACES.get(worker_id)
    if ws is not None:
        workspace_root = ws.root
        environment_id = _WORKER_ENVIRONMENTS.get(worker_id)
    else:
        workspace_root = _workspace_root_from_ctx(ctx)
        environment_id = _environment_id_from_ctx(ctx)

    new_entry = await manager().resume(
        worker_id,
        message,
        context={
            "workspace_root": workspace_root,
            "task_scope": worker_id,
            "environment_id": environment_id,
        },
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
    response.update(_workspace_payload(worker_id))

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


@rich_function_tool(running_summary="Cleaning up worker workspace...")
async def cleanup_worker_worktree(worker_id: str) -> RichToolOutput:
    """Remove a worker's isolated workspace after integrating its changes.

    Call this after you have integrated the worker's work into your
    workspace. What exactly is removed depends on the isolation strategy
    the worker was spawned with (e.g. a git worktree and its branch, or a
    directory copy).

    Args:
        worker_id: The worker ID whose isolated workspace should be removed.
    """
    ws = _WORKER_WORKSPACES.get(worker_id)
    if ws is None:
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

    strategy = get_isolation_strategy(_WORKER_ISOLATION.get(worker_id, "none"))
    payload = _workspace_payload(worker_id)
    await strategy.cleanup(worker_id, ws)
    await _retire_worker_environment(worker_id)
    _WORKER_WORKSPACES.pop(worker_id, None)
    _WORKER_ISOLATION.pop(worker_id, None)

    llm_output = json.dumps(
        {
            "worker_id": worker_id,
            "status": "cleaned_up",
            "removed_path": payload.get("worktree_path") or ws.root,
            "removed_branch": payload.get("worktree_branch", ""),
            "removed_sources": payload.get("sources") or [],
        }
    )
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} worktree cleaned up",
            "preview": f"Removed {ws.root}",
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
    if isolation and isolation != "none":
        summary = f"Spawn worker agent (isolated: {isolation})"

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
