"""Server functions for displaying and managing background workers from
the UI.

Mirrors ``server_functions/bash_jobs.py`` in shape: read-only ``list`` +
state-changing ``kill``, each filtered to workers spawned from the
caller's session. State-changing calls also broadcast
``ui.subagents.update`` to every channel on the parent session so the
docked panel refreshes in every connected tab / the Ink TUI.

Workers are simpler than bash jobs in two ways:
- The ``WorkerManager`` already exposes ``close(worker_id)``; there's no
  log file or signal-escalation dance to mirror.
- ``WorkerManager`` invokes ``on_complete`` (omni-code's
  ``_on_worker_complete``) on every terminal state, and that callback
  broadcasts ``ui.subagents.update``. So there's no separate sweeper
  needed — exits broadcast on their own.
"""

from __future__ import annotations

import logging
from typing import Optional

from omniagents import server_function
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

from omniagents.builtin.tools.worker_tools import (
    broadcast_workers_snapshot,
    manager,
    worker_state_dict,
    workers_snapshot,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# workers.list — read-only snapshot filtered by parent session.
# ---------------------------------------------------------------------------


@server_function(
    description="List background workers spawned from the current session.",
    strict=True,
    name_override="workers.list",
    resource_scope=ResourceScope.THREAD,
    # This controls separate worker-agent processes; it does not execute a
    # process in the selected Workspace/Environment.
    execution_requirements=ExecutionRequirement.NONE,
)
async def workers_list(_session_id: Optional[str] = None) -> dict:
    from omniagents.core.runtime.subagents import subagents_snapshot
    from omniagents.builtin.tools.swarm_tools import swarm_snapshots

    return {
        "snapshot": workers_snapshot(parent_session_id=_session_id),
        "subagents": subagents_snapshot(_session_id) if _session_id else [],
        "swarms": swarm_snapshots(_session_id),
    }


async def _in_worker_scope(service, session, function, **kwargs):
    from omniagents.core.agents.service import (
        bind_current_service,
        reset_current_service,
    )
    from omniagents.core.session.helpers import (
        set_current_session_id,
        reset_current_session_id,
    )

    service_token = bind_current_service(service)
    session_token = set_current_session_id(getattr(session, "id", None))
    try:
        return await function(**kwargs)
    finally:
        reset_current_session_id(session_token)
        reset_current_service(service_token)


async def _workers_list_on_invoke(service, session, args=None):
    return await _in_worker_scope(
        service, session, workers_list, _session_id=getattr(session, "id", None)
    )


setattr(workers_list, "_server_function_on_invoke", _workers_list_on_invoke)


# ---------------------------------------------------------------------------
# workers.kill — stop a worker by id and broadcast the refreshed snapshot.
# ---------------------------------------------------------------------------


@server_function(
    description=(
        "Stop a running worker. Idempotent: returns the current state for "
        "workers that have already exited."
    ),
    params_schema={
        "type": "object",
        "properties": {"worker_id": {"type": "string"}},
        "required": ["worker_id"],
        "additionalProperties": False,
    },
    strict=True,
    name_override="workers.kill",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def workers_kill(worker_id: str, _session_id: Optional[str] = None) -> dict:
    from omniagents.builtin.tools.worker_tools import _restore_workers

    _restore_workers(_session_id)
    handle = manager().workers.get(worker_id)
    if handle is None:
        return {
            "ok": False,
            "error": "unknown_worker",
            "worker_id": worker_id,
            "snapshot": workers_snapshot(parent_session_id=_session_id),
        }
    # Same visibility rule as bash_jobs: a session sees its own workers
    # plus any untagged ones. A worker from another session looks the
    # same as not-found to the caller.
    owner = getattr(handle, "parent_session_id", None)
    if _session_id is not None and owner is not None and owner != _session_id:
        return {
            "ok": False,
            "error": "unknown_worker",
            "worker_id": worker_id,
            "snapshot": workers_snapshot(parent_session_id=_session_id),
        }

    try:
        status = await manager().close(worker_id)
    except Exception as e:
        return {
            "ok": False,
            "error": "close_failed",
            "worker_id": worker_id,
            "message": str(e),
            "snapshot": workers_snapshot(parent_session_id=_session_id),
        }

    state = worker_state_dict(worker_id)
    return {
        "ok": True,
        "worker": state,
        "status": status,
        "snapshot": workers_snapshot(parent_session_id=_session_id),
    }


async def _workers_kill_on_invoke(service, session, args=None):
    args = args or {}
    sid = getattr(session, "id", None)
    result = await _in_worker_scope(
        service, session, workers_kill, worker_id=args.get("worker_id"), _session_id=sid
    )
    # ``manager().close`` waits for the worker's terminal state, which
    # triggers ``_on_worker_complete`` and that callback already
    # broadcasts. The extra broadcast here is a belt-and-braces refresh
    # for the case where close() returned via the "already done" branch
    # without firing the callback again.
    if sid:
        await broadcast_workers_snapshot(service, sid)
    return result


setattr(workers_kill, "_server_function_on_invoke", _workers_kill_on_invoke)


@server_function(
    description="Continue an interrupted worker in its original conversation and workspace.",
    strict=True,
    name_override="workers.resume",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
    params_schema={
        "type": "object",
        "properties": {
            "worker_id": {"type": "string"},
            "operation_id": {"type": "string"},
        },
        "required": ["worker_id", "operation_id"],
        "additionalProperties": False,
    },
)
async def workers_resume(
    worker_id: str, operation_id: str, _session_id: Optional[str] = None
) -> dict:
    from types import SimpleNamespace
    from omniagents.builtin.tools.worker_tools import _restore_workers, resume_worker
    from omniagents.core.session.helpers import (
        set_current_session_id,
        reset_current_session_id,
    )

    if not _session_id:
        return {"ok": False, "error": "unknown_worker"}
    token = set_current_session_id(_session_id)
    try:
        _restore_workers(_session_id)
        handle = manager().workers.get(worker_id)
        if handle is None or handle.parent_session_id != _session_id:
            return {"ok": False, "error": "unknown_worker"}
        ctx = SimpleNamespace(
            context={"session_id": _session_id}, tool_call_id=operation_id
        )
        output = await getattr(resume_worker, "_original_func")(
            ctx,
            worker_id,
            "Continue the existing assignment from its recorded state. Inspect uncertain tool outcomes before taking further action; do not blindly repeat them.",
        )
        import json

        result = json.loads(str(output))
        await broadcast_workers_snapshot(handle.parent_service, _session_id)
        return {
            "ok": result.get("status") == "resumed",
            **result,
            "snapshot": workers_snapshot(_session_id),
        }
    finally:
        reset_current_session_id(token)


async def _workers_resume_on_invoke(service, session, args=None):
    args = args or {}
    return await _in_worker_scope(
        service,
        session,
        workers_resume,
        worker_id=args.get("worker_id"),
        operation_id=args.get("operation_id"),
        _session_id=getattr(session, "id", None),
    )


setattr(workers_resume, "_server_function_on_invoke", _workers_resume_on_invoke)
