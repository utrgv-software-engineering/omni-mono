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
