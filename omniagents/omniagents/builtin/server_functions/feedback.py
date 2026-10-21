"""Builtin feedback server functions: /good /bad /note /notes.

Thumbs-up/down conversation feedback wired into Studio tracing
(``set_conversation_analysis`` keyed by ``session_<id>``), plus a
read-back for developer review. Activated by the ``feedback``
capability pack; lifted from omni-code.
"""

from __future__ import annotations

from omniagents import server_function
from omniagents.core.session import Session
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from omniagents.studio import tracing


from omniagents.core.server_functions.slash import (
    slash_command,
    text as slash_text,
    words as slash_words,
)


@slash_command(
    description="Mark this conversation as good, with an optional note",
    usage="[note]",
    args=slash_text(),
    order=50,
)
@server_function(
    description="Mark conversation acceptable and submit feedback to developers (optional note included)",
    strict=True,
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def good(session: Session, text: str = "") -> dict:
    try:
        group_id = f"session_{session.id}"
        tracing.set_conversation_analysis(
            group_id,
            judgment="acceptable",
            notes=text,
            marked=True,
        )
    except Exception:
        pass
    message = "Saved good note." if not text else f"Saved good note: {text}"
    return {"ok": True, "message": message}


@slash_command(
    description="Flag this conversation as bad, with an optional note",
    usage="[note]",
    args=slash_text(),
    order=51,
)
@server_function(
    description="Flag conversation as unacceptable and submit feedback to developers (optional note included)",
    strict=True,
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def bad(session: Session, text: str = "") -> dict:
    try:
        group_id = f"session_{session.id}"
        tracing.set_conversation_analysis(
            group_id,
            judgment="unacceptable",
            notes=text,
            marked=True,
        )
    except Exception:
        pass
    message = "Saved bad note." if not text else f"Saved bad note: {text}"
    return {"ok": True, "message": message}


def _read_note(session: Session) -> dict:
    try:
        group_id = f"session_{session.id}"
        analysis = tracing.get_conversation_analysis(group_id)
        notes = analysis.get("notes") or ""
        judgment = analysis.get("judgment")
        if not notes or judgment not in ("acceptable", "unacceptable"):
            return {"note": None, "message": "No note set."}
        is_good = judgment == "acceptable"
        label = "Good" if is_good else "Bad"
        return {
            "note": {"text": notes, "is_good": is_good},
            "message": f"{label}: {notes}",
        }
    except Exception:
        return {"note": None, "message": "No note set."}


@server_function(
    description="Get conversation analysis summary (Good/Bad and notes) saved for developer review",
    strict=True,
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def note(session: Session) -> dict:
    return _read_note(session)


@server_function(
    description="Alias of 'note' (analysis summary for developer review)",
    strict=True,
    name_override="notes",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def notes(session: Session) -> dict:
    return _read_note(session)
