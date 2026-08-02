from __future__ import annotations

from omniagents import server_function
from omniagents.core.session import Session
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@server_function(
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
    description="Set per-session note",
    strict=True,
)
async def set_note(session: Session, text: str) -> dict:
    session._example_note = text
    return {"ok": True}


@server_function(
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
    description="Get per-session note",
    strict=True,
)
async def get_note(session: Session) -> dict:
    note = getattr(session, "_example_note", "")
    return {"note": note}
