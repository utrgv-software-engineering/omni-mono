import json
import sqlite3
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional, Tuple

from omniagents.core.paths import get_sessions_db_path

_STORES: dict[str, Tuple[sqlite3.Connection, Lock]] = {}

# Pluggable encryption provider — set via set_encryption_provider()
_encryption_provider = None


def set_encryption_provider(provider) -> None:
    """Set the session encryption provider (called by AgentService on startup)."""
    global _encryption_provider
    _encryption_provider = provider


_UNSET = object()


def _serialize_metadata(value: object) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value)
    except TypeError:
        return json.dumps(value, default=str)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"  # noqa: E501
        "session_id TEXT,"  # noqa: E501
        "msg_json TEXT,"  # noqa: E501
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "session_id TEXT PRIMARY KEY,"  # noqa: E501
        "archived INTEGER DEFAULT 0,"  # noqa: E501
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audio_metadata ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"  # noqa: E501
        "session_id TEXT NOT NULL,"  # noqa: E501
        "message_id INTEGER,"  # noqa: E501
        "item_id TEXT NOT NULL,"  # noqa: E501
        "content_index INTEGER NOT NULL,"  # noqa: E501
        "file_path TEXT NOT NULL,"  # noqa: E501
        "audio_format TEXT NOT NULL,"  # noqa: E501
        "duration_ms INTEGER,"  # noqa: E501
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"  # noqa: E501
        "FOREIGN KEY (message_id) REFERENCES history(id) ON DELETE CASCADE)"
    )
    # Create index for faster lookups by session_id
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_session " "ON audio_metadata(session_id)"
    )
    # Create index for faster lookups by item_id
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_item "
        "ON audio_metadata(item_id, content_index)"
    )

    cursor = conn.execute("PRAGMA table_info(history)")
    columns = [col[1] for col in cursor.fetchall()]
    if "created_at" not in columns:
        conn.execute("ALTER TABLE history ADD COLUMN created_at TIMESTAMP")
        conn.execute(
            "UPDATE history SET created_at = datetime('now') WHERE created_at IS NULL"
        )

    cursor = conn.execute("PRAGMA table_info(sessions)")
    columns = [col[1] for col in cursor.fetchall()]
    if "created_at" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN created_at TIMESTAMP")
        conn.execute(
            "UPDATE sessions SET created_at = datetime('now') WHERE created_at IS NULL"
        )
    if "context_json" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN context_json TEXT")
    if "variables_json" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN variables_json TEXT")
    if "hold" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN hold INTEGER DEFAULT 0")
    if "user_id" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")

    conn.commit()


def _get_store(
    project_slug: str | None, agent_slug: str | None
) -> Tuple[sqlite3.Connection, Lock]:
    db_path = get_sessions_db_path(project_slug, agent_slug)
    key = str(db_path)
    store = _STORES.get(key)
    if store is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if _encryption_provider is not None:
            conn = _encryption_provider.get_connection(str(db_path))
        else:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _ensure_schema(conn)
        lock = Lock()
        store = (conn, lock)
        _STORES[key] = store
    return store


def purge_old_sessions(
    retention_days: int,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> int:
    """Delete sessions older than *retention_days*.

    Returns the number of sessions purged.  Does nothing if
    *retention_days* <= 0.
    """
    if retention_days <= 0:
        return 0
    conn, lock = _get_store(project_slug, agent_slug)
    cutoff = (datetime.utcnow() - timedelta(days=retention_days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with lock:
        # Find sessions to purge (skip sessions under legal hold)
        cur = conn.execute(
            "SELECT session_id FROM sessions WHERE created_at < ? AND hold = 0",
            (cutoff,),
        )
        old_ids = [row[0] for row in cur.fetchall()]
        if not old_ids:
            return 0
        placeholders = ",".join("?" for _ in old_ids)
        conn.execute(
            f"DELETE FROM audio_metadata WHERE session_id IN ({placeholders})",
            old_ids,
        )
        conn.execute(
            f"DELETE FROM history WHERE session_id IN ({placeholders})",
            old_ids,
        )
        conn.execute(
            f"DELETE FROM sessions WHERE session_id IN ({placeholders})",
            old_ids,
        )
        conn.commit()
    return len(old_ids)


def load_history(
    session_id: str, *, project_slug: str | None = None, agent_slug: str | None = None
) -> list[dict]:
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            "SELECT msg_json FROM history WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    return [json.loads(r[0]) for r in rows]


def append_message(
    session_id: str,
    message: dict,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    conn, lock = _get_store(project_slug, agent_slug)
    msg_json = json.dumps(message)
    with lock:
        conn.execute(
            "INSERT INTO history(session_id, msg_json, created_at) VALUES (?, ?, datetime('now'))",
            (session_id, msg_json),
        )
        conn.commit()


def register_session(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
    user_id: str | None = None,
) -> None:
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        conn.execute(
            "INSERT OR IGNORE INTO sessions(session_id, created_at, user_id) "
            "VALUES (?, datetime('now'), ?)",
            (session_id, user_id),
        )
        conn.commit()


def get_session_metadata(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> tuple[
    dict | list | str | int | float | bool | None,
    dict | list | str | int | float | bool | None,
]:
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            "SELECT context_json, variables_json FROM sessions WHERE session_id=?",
            (session_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None, None
    ctx_raw, vars_raw = row
    ctx = json.loads(ctx_raw) if ctx_raw is not None else None
    vars_value = json.loads(vars_raw) if vars_raw is not None else None
    return ctx, vars_value


def update_session_metadata(
    session_id: str,
    *,
    context: object = _UNSET,
    variables: object = _UNSET,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    assignments: list[str] = []
    values: list[object] = []
    if context is not _UNSET:
        assignments.append("context_json=?")
        values.append(_serialize_metadata(context))
    if variables is not _UNSET:
        assignments.append("variables_json=?")
        values.append(_serialize_metadata(variables))
    if not assignments:
        return
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        conn.execute(
            f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id=?",
            (*values, session_id),
        )
        conn.commit()


def set_archived(
    session_id: str,
    archived: bool,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        conn.execute(
            "UPDATE sessions SET archived=? WHERE session_id=?",
            (1 if archived else 0, session_id),
        )
        conn.commit()


def get_archived(
    session_id: str, *, project_slug: str | None = None, agent_slug: str | None = None
) -> bool:
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            "SELECT archived FROM sessions WHERE session_id=?",
            (session_id,),
        )
        row = cur.fetchone()
    return bool(row[0]) if row else False


def list_sessions(
    *, project_slug: str | None = None, agent_slug: str | None = None
) -> list[dict]:
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
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
            "message_count": message_count,
        }

        if message_count > 0:
            with lock:
                first_cur = conn.execute(
                    """
                    SELECT msg_json, created_at 
                    FROM history 
                    WHERE session_id = ? 
                    ORDER BY id ASC 
                    LIMIT 1
                    """,
                    (session_id,),
                )
                first_row = first_cur.fetchone()
                last_cur = conn.execute(
                    """
                    SELECT msg_json, created_at 
                    FROM history 
                    WHERE session_id = ? 
                    ORDER BY id DESC 
                    LIMIT 1
                    """,
                    (session_id,),
                )
                last_row = last_cur.fetchone()

            if first_row:
                first_msg = json.loads(first_row[0])
                if first_row[1]:
                    first_msg["timestamp"] = first_row[1]
                session_data["first_message"] = first_msg

            if last_row:
                last_msg = json.loads(last_row[0])
                if last_row[1]:
                    last_msg["timestamp"] = last_row[1]
                session_data["last_message"] = last_msg

        result.append(session_data)

    return result


def get_user_messages(
    *,
    limit: int = 200,
    include_archived: bool = True,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> list[dict]:
    conn, lock = _get_store(project_slug, agent_slug)
    results: list[dict] = []
    page_size = 1000
    offset = 0

    where_archived = "" if include_archived else "WHERE s.archived = 0"

    while len(results) < limit:
        with lock:
            cur = conn.execute(
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


# --- Audio Metadata Functions ---


def save_audio_metadata(
    session_id: str,
    item_id: str,
    content_index: int,
    file_path: str,
    audio_format: str,
    duration_ms: Optional[int] = None,
    message_id: Optional[int] = None,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> int:
    """Save audio metadata to the database.

    Args:
        session_id: The session ID
        item_id: The realtime item ID
        content_index: The content index within the item
        file_path: Relative path to the audio file
        audio_format: Audio format (e.g., 'pcm16')
        duration_ms: Optional duration in milliseconds
        message_id: Optional FK to history message
        project_slug: Project identifier
        agent_slug: Agent identifier

    Returns:
        The inserted row ID
    """
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cursor = conn.execute(
            "INSERT INTO audio_metadata(session_id, message_id, item_id, content_index, "
            "file_path, audio_format, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                session_id,
                message_id,
                item_id,
                content_index,
                file_path,
                audio_format,
                duration_ms,
            ),
        )
        row_id = cursor.lastrowid
        conn.commit()
    return row_id


def get_audio_metadata(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> list[dict]:
    """Get all audio metadata for a session.

    Args:
        session_id: The session ID
        project_slug: Project identifier
        agent_slug: Agent identifier

    Returns:
        List of audio metadata dictionaries
    """
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            "SELECT id, message_id, item_id, content_index, file_path, audio_format, "
            "duration_ms, created_at FROM audio_metadata WHERE session_id=? "
            "ORDER BY id ASC",
            (session_id,),
        )
        rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "message_id": row[1],
            "item_id": row[2],
            "content_index": row[3],
            "file_path": row[4],
            "audio_format": row[5],
            "duration_ms": row[6],
            "created_at": row[7],
        }
        for row in rows
    ]


def get_audio_metadata_by_item(
    item_id: str,
    content_index: int,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> Optional[dict]:
    """Get audio metadata for a specific item and content index.

    Args:
        item_id: The realtime item ID
        content_index: The content index
        project_slug: Project identifier
        agent_slug: Agent identifier

    Returns:
        Audio metadata dictionary or None if not found
    """
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            "SELECT id, session_id, message_id, file_path, audio_format, "
            "duration_ms, created_at FROM audio_metadata "
            "WHERE item_id=? AND content_index=? LIMIT 1",
            (item_id, content_index),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "session_id": row[1],
        "message_id": row[2],
        "item_id": item_id,
        "content_index": content_index,
        "file_path": row[3],
        "audio_format": row[4],
        "duration_ms": row[5],
        "created_at": row[6],
    }


def delete_audio_metadata(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> int:
    """Delete all audio metadata for a session.

    Args:
        session_id: The session ID
        project_slug: Project identifier
        agent_slug: Agent identifier

    Returns:
        Number of rows deleted
    """
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cursor = conn.execute(
            "DELETE FROM audio_metadata WHERE session_id=?", (session_id,)
        )
        deleted_count = cursor.rowcount
        conn.commit()
    return deleted_count


def delete_session(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    """Delete a session and all its associated data.

    This deletes:
    - The session record from the sessions table
    - All history messages from the history table
    - All audio metadata from the audio_metadata table

    Args:
        session_id: The session ID to delete
        project_slug: Project identifier
        agent_slug: Agent identifier

    Returns:
        True if the session was deleted, False if it didn't exist
    """
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        # Check if session exists and is not under legal hold
        cur = conn.execute(
            "SELECT session_id, hold FROM sessions WHERE session_id=?", (session_id,)
        )
        row = cur.fetchone()
        if row is None:
            return False
        if row[1]:
            raise ValueError(
                f"Session {session_id} is under legal hold and cannot be deleted"
            )

        # Delete audio metadata (if any)
        conn.execute("DELETE FROM audio_metadata WHERE session_id=?", (session_id,))

        # Delete history messages
        conn.execute("DELETE FROM history WHERE session_id=?", (session_id,))

        # Delete session record
        conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))

        conn.commit()
    return True


# --- Legal Hold Functions ---


def set_session_hold(
    session_id: str,
    hold: bool = True,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    """Set or clear legal hold on a session.

    Held sessions are excluded from retention purge and cannot be deleted.

    Returns True if the session was found and updated, False if it doesn't exist.
    """
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            "UPDATE sessions SET hold=? WHERE session_id=?",
            (1 if hold else 0, session_id),
        )
        conn.commit()
    return cur.rowcount > 0


def get_session_hold(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    """Check whether a session is under legal hold."""
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            "SELECT hold FROM sessions WHERE session_id=?", (session_id,)
        )
        row = cur.fetchone()
    return bool(row[0]) if row else False


# --- User Identity Functions ---


def update_session_user_id(
    session_id: str,
    user_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    """Associate a user_id with an existing session."""
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        conn.execute(
            "UPDATE sessions SET user_id=? WHERE session_id=?",
            (user_id, session_id),
        )
        conn.commit()


def list_sessions_for_user(
    user_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> list[dict]:
    """Like :func:`list_sessions` but filtered to sessions owned by *user_id*."""
    conn, lock = _get_store(project_slug, agent_slug)
    with lock:
        cur = conn.execute(
            """
            SELECT
                s.session_id,
                s.archived,
                s.created_at as session_created,
                COUNT(h.id) as message_count
            FROM sessions s
            LEFT JOIN history h ON s.session_id = h.session_id
            WHERE s.user_id = ?
            GROUP BY s.session_id, s.archived, s.created_at
            ORDER BY s.created_at DESC
            """,
            (user_id,),
        )
        sessions = cur.fetchall()

    return [
        {
            "id": sid,
            "archived": bool(archived),
            "created_at": created or datetime.now().isoformat(),
            "message_count": msg_count,
        }
        for sid, archived, created, msg_count in sessions
    ]
