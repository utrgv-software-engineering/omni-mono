import sqlite3
import os
import json
from threading import Lock

_DB_PATH = os.getenv("OMNIAGENTS_HISTORY_DB", "sessions.db")
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
_conn.execute(
    "CREATE TABLE IF NOT EXISTS history ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "session_id TEXT,"
    "msg_json TEXT)"
)
_conn.execute(
    "CREATE TABLE IF NOT EXISTS sessions ("
    "session_id TEXT PRIMARY KEY,"
    "archived INTEGER DEFAULT 0)"
)
_conn.commit()
_lock = Lock()

def load_history(session_id: str) -> list[dict]:
    with _lock:
        cur = _conn.execute(
            "SELECT msg_json FROM history WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    return [json.loads(r[0]) for r in rows]

def append_message(session_id: str, message: dict) -> None:
    msg_json = json.dumps(message)
    with _lock:
        _conn.execute(
            "INSERT INTO history(session_id, msg_json) VALUES (?, ?)",
            (session_id, msg_json),
        )
        _conn.commit()


def register_session(session_id: str) -> None:
    """Ensure *session_id* exists in the sessions table."""
    with _lock:
        _conn.execute(
            "INSERT OR IGNORE INTO sessions(session_id) VALUES (?)",
            (session_id,),
        )
        _conn.commit()


def set_archived(session_id: str, archived: bool) -> None:
    """Update archived state for *session_id*."""
    with _lock:
        _conn.execute(
            "UPDATE sessions SET archived=? WHERE session_id=?",
            (1 if archived else 0, session_id),
        )
        _conn.commit()


def get_archived(session_id: str) -> bool:
    """Return archived state for *session_id*."""
    with _lock:
        cur = _conn.execute(
            "SELECT archived FROM sessions WHERE session_id=?",
            (session_id,),
        )
        row = cur.fetchone()
    return bool(row[0]) if row else False


def list_sessions() -> list[dict]:
    """Return all sessions with their archived state."""
    with _lock:
        cur = _conn.execute("SELECT session_id, archived FROM sessions")
        rows = cur.fetchall()
    return [
        {"id": sid, "archived": bool(arch)} for sid, arch in rows
    ]
