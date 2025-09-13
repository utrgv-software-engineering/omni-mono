from __future__ import annotations

from omniagents import server_function
from omniagents.core.session import Session


@server_function(description="Set per-session note", strict=True)
async def set_note(session: Session, text: str) -> dict:
    session._example_note = text
    return {"ok": True}


@server_function(description="Get per-session note", strict=True)
async def get_note(session: Session) -> dict:
    note = getattr(session, "_example_note", "")
    return {"note": note}
