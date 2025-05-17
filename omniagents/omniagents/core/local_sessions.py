import os
import sqlite3
import json
import uuid
from threading import Lock

__all__ = ["LocalSession", "load_history", "append_message"]

_DB_PATH = os.getenv("OMNIAGENTS_HISTORY_DB", "sessions.db")
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
_conn.execute(
    "CREATE TABLE IF NOT EXISTS history ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "session_id TEXT,"
    "msg_json TEXT)"
)
_conn.commit()
_lock = Lock()


def load_history(session_id: str) -> list[dict]:
    """Return the message history for *session_id*."""
    with _lock:
        cur = _conn.execute(
            "SELECT msg_json FROM history WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    return [json.loads(r[0]) for r in rows]


def append_message(session_id: str, message: dict) -> None:
    """Persist *message* for *session_id*."""
    msg_json = json.dumps(message)
    with _lock:
        _conn.execute(
            "INSERT INTO history(session_id, msg_json) VALUES (?, ?)",
            (session_id, msg_json),
        )
        _conn.commit()


class LocalSession:
    """Simple container for a local chat session."""

    def __init__(self, session_id: str | None = None) -> None:
        self.id: str = session_id or str(uuid.uuid4())
        self.history: list[dict] = load_history(self.id) if session_id else []

    def append_message(self, message: dict) -> None:
        """Add *message* to this session and persist it."""
        self.history.append(message)
        append_message(self.id, message)
