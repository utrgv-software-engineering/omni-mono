from omniagents import server_function
from omniagents.core.session import Session
from omniagents.studio import tracing


@server_function(description="Mark conversation acceptable and submit feedback to developers (optional note included)", strict=True)
async def good(session: Session, text: str) -> dict:
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
    return {"ok": True, "message": f"Saved good note: {text}"}


@server_function(description="Flag conversation as unacceptable and submit feedback to developers (optional note included)", strict=True)
async def bad(session: Session, text: str) -> dict:
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
    return {"ok": True, "message": f"Saved bad note: {text}"}


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
        return {"note": {"text": notes, "is_good": is_good}, "message": f"{label}: {notes}"}
    except Exception:
        return {"note": None, "message": "No note set."}


@server_function(description="Get conversation analysis summary (Good/Bad and notes) saved for developer review", strict=True)
async def note(session: Session) -> dict:
    return _read_note(session)


@server_function(description="Alias of 'note' (analysis summary for developer review)", strict=True, name_override="notes")
async def notes(session: Session) -> dict:
    return _read_note(session)
