"""Builtin server functions for self-scheduled agent wakeups.

The machinery (schedule state, tick loop, persistence, rehydration,
broadcast, goal-arbitration) lives in ``omniagents.core.autonomy.wakeup``.
The wakeup framing text is domain-neutral and ships with the framework,
so no product prompt builders are installed here. This module keeps the
``@server_function`` registrations (``/wakeup.stop``, ``/wakeup.status``)
that delegate to the framework impls, and re-exports ``schedule_impl`` /
``cancel_impl`` for existing importers.
"""

from __future__ import annotations

from typing import Optional

from omniagents import server_function
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

from omniagents.core.autonomy.wakeup import (  # noqa: F401  (re-exported surface)
    VAR_KEY,
    WakeupSchedule,
    _schedules,
    cancel_impl,
    maybe_rehydrate,
    schedule_impl,
    status_impl,
    wakeup_snapshot_dict,
)

# ---------------------------------------------------------------------------
# Server functions — slash commands for user-facing inspection + cancel.
# ---------------------------------------------------------------------------


@server_function(
    description="Cancel the running wakeup schedule on this session.",
    strict=True,
    name_override="wakeup.stop",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def wakeup_stop(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _wakeup_stop_on_invoke(service, session, args=None):
    return await cancel_impl(service, session)


setattr(wakeup_stop, "_server_function_on_invoke", _wakeup_stop_on_invoke)


@server_function(
    description="Report the status of the current wakeup schedule on this session.",
    strict=True,
    name_override="wakeup.status",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def wakeup_status(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(wakeup_status, "_server_function_on_invoke", status_impl)
