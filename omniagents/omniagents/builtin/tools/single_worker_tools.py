"""Session-owned, named single workers for the swarms capability."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agents.run_context import RunContextWrapper
from omniagents.builtin.tools import worker_tools as workers
from omniagents.builtin.tools.swarm_tools import _validate_worker_spec
from omniagents.core.tools import rich_function_tool, session_id_from_ctx


@dataclass
class SingleWorker:
    name: str
    worker_id: str
    role: str = ""
    task: str = ""
    started: bool = False
    cleaned: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_WORKERS: dict[tuple[str, str], SingleWorker] = {}


def _owner(ctx: RunContextWrapper[Any]) -> str:
    sid = session_id_from_ctx(ctx)
    if not sid:
        raise ValueError("A session is required")
    return sid


def _owned(ctx: RunContextWrapper[Any], name: str) -> SingleWorker:
    worker = _WORKERS.get((_owner(ctx), name))
    if worker is None:
        raise ValueError("Worker not found in this session")
    return worker


def _snapshot(worker: SingleWorker) -> dict[str, Any]:
    return {
        "name": worker.name,
        "worker_id": worker.worker_id,
        "role": worker.role,
        "task": worker.task,
        "state": workers.worker_state_dict(worker.worker_id)
        or {"status": "unavailable" if worker.started else "planned"},
        "workspace": workers._workspace_payload(worker.worker_id),
        "workspace_cleaned": worker.cleaned,
    }


@rich_function_tool
async def dispatch_worker(
    ctx: RunContextWrapper[Any],
    name: str,
    task: str,
    role: str | None = None,
    isolation: str | None = None,
    allow_dirty: bool = False,
) -> str:
    """Dispatch one named worker without a swarm; reuse its name to resume it.

    Include inputs, file ownership, boundaries, and acceptance criteria.
    Optional role instructions persist on resume unless replaced. Existing
    isolation is reused. A running worker cannot be dispatched again.
    Completion automatically notifies this session. Review and integrate
    results before cleanup_single_worker; close_single_worker cancels work.
    """
    sid = _owner(ctx)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name) or not task.strip():
        raise ValueError(
            "Use a lowercase worker name (letters, digits, _ or -) and a nonempty task"
        )
    key = (sid, name)
    worker = _WORKERS.get(key)
    if worker is None:
        if (
            sum(owner == sid and not w.cleaned for (owner, _), w in _WORKERS.items())
            >= 16
        ):
            raise ValueError("Clean up an existing worker before creating another")
        worker = SingleWorker(name, f"single-{name}-{uuid4().hex}")
        _WORKERS[key] = worker
    async with worker.lock:
        if worker.cleaned:
            raise ValueError("Worker workspace was already cleaned; use a new name")
        if (workers.worker_state_dict(worker.worker_id) or {}).get(
            "status"
        ) == "running":
            raise ValueError("Worker is still running")
        instructions = worker.role if role is None else role
        brief = (
            f"Your name: {name}\nYour role: {instructions or 'Complete the assigned task'}\n"
            f"Assignment: {task}\nDo not recruit workers. Finish with your result for the owner."
        )
        if worker.started:
            output = await getattr(workers.resume_worker, "_original_func")(
                ctx, worker.worker_id, brief
            )
        else:
            output = await workers.spawn_worker_run(
                ctx,
                brief,
                isolation,
                allow_dirty,
                worker_id=worker.worker_id,
                configure_spec=_validate_worker_spec,
            )
        data = json.loads(str(output))
        if data.get("status") in {"spawned", "resumed"}:
            worker.started = True
            worker.role = instructions
            worker.task = task
        return json.dumps({"name": name, **data})


@rich_function_tool
async def get_worker(ctx: RunContextWrapper[Any], name: str | None = None) -> str:
    """Inspect one standalone worker, or list this session's standalone workers.

    Includes results, errors and workspace paths. Swarm members use get_swarm.
    """
    if name is not None:
        return json.dumps(_snapshot(_owned(ctx, name)))
    sid = _owner(ctx)
    return json.dumps(
        [_snapshot(w) for (owner, _), w in _WORKERS.items() if owner == sid]
    )


@rich_function_tool
async def close_single_worker(ctx: RunContextWrapper[Any], name: str) -> str:
    """Cancel a standalone worker, retaining history and files for review or resumption."""
    worker = _owned(ctx, name)
    async with worker.lock:
        if not worker.started:
            return json.dumps({"name": name, "status": "not_started"})
        output = await getattr(workers.close_worker, "_original_func")(worker.worker_id)
        return json.dumps({"name": name, **json.loads(str(output))})


@rich_function_tool
async def cleanup_single_worker(ctx: RunContextWrapper[Any], name: str) -> str:
    """Remove an isolated workspace AFTER reviewing and integrating its changes.

    Rejects running workers. Cleanup permanently prevents resuming this name.
    """
    worker = _owned(ctx, name)
    async with worker.lock:
        if (workers.worker_state_dict(worker.worker_id) or {}).get(
            "status"
        ) == "running":
            raise ValueError("Cannot clean a running worker")
        output = await getattr(workers.cleanup_worker_worktree, "_original_func")(
            worker.worker_id
        )
        data = json.loads(str(output))
        if data.get("status") in {"cleaned_up", "no_worktree"}:
            worker.cleaned = True
        return json.dumps({"name": name, **data})
