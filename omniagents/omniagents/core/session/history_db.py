import json
import sqlite3
from datetime import datetime
from threading import Lock

from omniagents.core.paths import get_sessions_db_path

_DB_PATH = get_sessions_db_path()
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)

# Enable foreign keys
_conn.execute("PRAGMA foreign_keys = ON")

# Create tables with timestamps
_conn.execute(
    "CREATE TABLE IF NOT EXISTS history ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "session_id TEXT,"
    "msg_json TEXT,"
    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)
_conn.execute(
    "CREATE TABLE IF NOT EXISTS sessions ("
    "session_id TEXT PRIMARY KEY,"
    "archived INTEGER DEFAULT 0,"
    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)

# Add timestamps to existing tables if they don't have them
cursor = _conn.execute("PRAGMA table_info(history)")
columns = [col[1] for col in cursor.fetchall()]
if 'created_at' not in columns:
    _conn.execute("ALTER TABLE history ADD COLUMN created_at TIMESTAMP")
    # Update existing rows with current timestamp
    _conn.execute("UPDATE history SET created_at = datetime('now') WHERE created_at IS NULL")

cursor = _conn.execute("PRAGMA table_info(sessions)")
columns = [col[1] for col in cursor.fetchall()]
if 'created_at' not in columns:
    _conn.execute("ALTER TABLE sessions ADD COLUMN created_at TIMESTAMP")
    # Update existing rows with current timestamp
    _conn.execute("UPDATE sessions SET created_at = datetime('now') WHERE created_at IS NULL")

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
            "INSERT INTO history(session_id, msg_json, created_at) VALUES (?, ?, datetime('now'))",
            (session_id, msg_json),
        )
        _conn.commit()


def register_session(session_id: str) -> None:
    """Ensure *session_id* exists in the sessions table."""
    with _lock:
        _conn.execute(
            "INSERT OR IGNORE INTO sessions(session_id, created_at) VALUES (?, datetime('now'))",
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
    """Return all sessions with their archived state, timestamps, and message previews."""
    with _lock:
        # Get all sessions with their metadata
        cur = _conn.execute(
            """
            SELECT 
                s.session_id,
                s.archived,
                s.created_at as session_created,
                COUNT(h.id) as message_count
            FROM sessions s
            LEFT JOIN history h ON s.session_id = h.session_id
            GROUP BY s.session_id, s.archived, s.created_at
            ORDER BY s.created_at DESC
            """
        )
        sessions = cur.fetchall()
        
        result = []
        for session_id, archived, session_created, message_count in sessions:
            session_data = {
                "id": session_id,
                "archived": bool(archived),
                "created_at": session_created or datetime.now().isoformat(),
                "message_count": message_count
            }
            
            # Get first and last messages for this session
            if message_count > 0:
                # Get first message
                first_cur = _conn.execute(
                    """
                    SELECT msg_json, created_at 
                    FROM history 
                    WHERE session_id = ? 
                    ORDER BY id ASC 
                    LIMIT 1
                    """,
                    (session_id,)
                )
                first_row = first_cur.fetchone()
                if first_row:
                    first_msg = json.loads(first_row[0])
                    if first_row[1]:
                        first_msg["timestamp"] = first_row[1]
                    session_data["first_message"] = first_msg
                
                # Get last message
                last_cur = _conn.execute(
                    """
                    SELECT msg_json, created_at 
                    FROM history 
                    WHERE session_id = ? 
                    ORDER BY id DESC 
                    LIMIT 1
                    """,
                    (session_id,)
                )
                last_row = last_cur.fetchone()
                if last_row:
                    last_msg = json.loads(last_row[0])
                    if last_row[1]:
                        last_msg["timestamp"] = last_row[1]
                    session_data["last_message"] = last_msg
            
            result.append(session_data)
        
    return result


def get_user_messages(limit: int = 200, include_archived: bool = True) -> list[dict]:
    results: list[dict] = []
    page_size = 1000
    offset = 0

    where_archived = "" if include_archived else "WHERE s.archived = 0"

    while len(results) < limit:
        with _lock:
            cur = _conn.execute(
                f"""
                SELECT h.msg_json, h.created_at, h.session_id
                FROM history h
                JOIN sessions s ON s.session_id = h.session_id
                {where_archived}
                ORDER BY h.id DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            )
            rows = cur.fetchall()
        if not rows:
            break
        for msg_json, created_at, session_id in rows:
            try:
                msg = json.loads(msg_json)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text:
                continue
            results.append(
                {
                    "content": text,
                    "session_id": session_id,
                    "timestamp": created_at,
                }
            )
            if len(results) >= limit:
                break
        offset += page_size

    results.reverse()
    return results
