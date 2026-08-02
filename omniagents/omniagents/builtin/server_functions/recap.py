"""Builtin ``/recap`` server function.

Produces a short human-friendly recap of the session and pushes it to
the client panel. The orchestration lives in
``omniagents.core.runtime.recap``; products install their summarizer
with ``set_recap_summarizer``. Activated by the ``recap`` capability
pack; lifted from omni-code.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function
from omniagents.core.runtime.recap import generate_and_broadcast_recap
from omniagents.core.session.manager import Session
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@server_function(
    description="Recap the session so far in <=400 words (shown in a panel)",
    strict=True,
    name_override="recap",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def recap(service: Any, session: Session) -> dict:
    await service.set_client_status(
        "Recapping...",
        session=session,
        run_id=getattr(session, "active_run_id", None),
        show_spinner=True,
    )
    try:
        text = await generate_and_broadcast_recap(service, session)
    finally:
        await service.set_client_status(
            "",
            session=session,
            run_id=getattr(session, "active_run_id", None),
            show_spinner=False,
        )
    # Return the text too, so the interactive slash path can render it even
    # if the broadcast was dropped. The client routes recap's result into
    # the panel rather than dumping it in chat.
    return {"ok": True, "text": text}
