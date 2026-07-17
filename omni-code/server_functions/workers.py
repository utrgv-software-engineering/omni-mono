"""Server functions for displaying and managing background workers from
the UI.

Mirrors ``server_functions/bash_jobs.py`` in shape: read-only ``list`` +
state-changing ``kill``, each filtered to workers spawned from the
caller's session. State-changing calls also broadcast
``ui.workers.update`` to every channel on the parent session so the
docked panel refreshes in every connected tab / the Ink TUI.

Workers are simpler than bash jobs in two ways:
- The ``WorkerManager`` already exposes ``close(worker_id)``; there's no
  log file or signal-escalation dance to mirror.
- ``WorkerManager`` invokes ``on_complete`` (omni-code's
  ``_on_worker_complete``) on every terminal state, and that callback
  broadcasts ``ui.workers.update``. So there's no separate sweeper
  needed — exits broadcast on their own.
"""

from __future__ import annotations

import logging
from typing import Optional

from omniagents import server_function

# Install omni-code's worker wiring (spec loader, history loader, the
# "worktree" isolation strategy) before the framework worker tools are
# first used. Server-function discovery imports this module in every
# omni-code server process, so this provably precedes the first spawn —
# same pattern as the autonomy prompt builders.
from omni_code.workers_runtime import install as _install_workers_runtime

_install_workers_runtime()

from omniagents.builtin.tools.worker_tools import (  # noqa: E402
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
)
async def workers_list(_session_id: Optional[str] = None) -> dict:
    return {"snapshot": workers_snapshot(parent_session_id=_session_id)}


async def _workers_list_on_invoke(service, session, args=None):
    return await workers_list(_session_id=getattr(session, "id", None))


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
)
async def workers_kill(
    worker_id: str, _session_id: Optional[str] = None
) -> dict:
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
    result = await workers_kill(
        worker_id=args.get("worker_id"), _session_id=sid
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
