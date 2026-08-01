"""SQLite-backed session storage.

Port of the original ``history_db.py`` body into a class implementing
:class:`SessionStorageBackend`. Behaviour is byte-for-byte equivalent —
same schema migrations, same connection cache keyed by db path, same
encryption-provider hook, same preview-trimming logic in ``list_sessions``.

The legacy ``history_db._STORES`` module attribute is fed by this class so
the conftest fixture (which iterates ``_STORES.values()`` to close
connections between tests) keeps working.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Optional, Tuple

from omniagents.core.debug import Debug
from omniagents.core.paths import get_sessions_db_path

from .base import SessionStorageBackend, _UNSET

# Module-level connection cache shared with history_db (the conftest fixture
# closes connections through this same dict). Keyed by absolute db path so
# different (project, agent) shards each get their own sqlite3.Connection.
_STORES: dict[str, Tuple[sqlite3.Connection, Lock]] = {}

# Pluggable encryption provider — sqlite-only. ``set_encryption_provider``
# below assigns this; ``_get_store`` consults it when opening a new file.
_encryption_provider = None


def set_encryption_provider(provider) -> None:
    """Install an encryption provider for new connections (SqlCipher etc.).

    The provider's ``get_connection(db_path) -> sqlite3.Connection`` is called
    in place of ``sqlite3.connect`` for any not-yet-opened db file. Already-
    opened connections are untouched."""
    global _encryption_provider
    _encryption_provider = provider


def _serialize_metadata(value: object) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value)
    except TypeError:
        return json.dumps(value, default=str)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    # Zero freed pages rather than leaving deleted transcript bytes readable
    # in the free list. This file holds the raw conversation history, so a
    # retention purge or a delete_session has to mean "gone", not
    # "unlinked" — the same posture the conversation domain store adopted.
    # Cheaper than VACUUM: no whole-file rewrite, no exclusive lock.
    conn.execute("PRAGMA secure_delete = ON")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "session_id TEXT,"
        "msg_json TEXT,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "session_id TEXT PRIMARY KEY,"
        "archived INTEGER DEFAULT 0,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audio_metadata ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "session_id TEXT NOT NULL,"
        "message_id INTEGER,"
        "item_id TEXT NOT NULL,"
        "content_index INTEGER NOT NULL,"
        "file_path TEXT NOT NULL,"
        "audio_format TEXT NOT NULL,"
        "duration_ms INTEGER,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "FOREIGN KEY (message_id) REFERENCES history(id) ON DELETE CASCADE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_session ON audio_metadata(session_id)"
    )
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

    # Indexes that depend on columns added by the migration above must be
    # created AFTER the ALTER TABLE statements run.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_session_id "
        "ON history(session_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_created_at " "ON sessions(created_at)"
    )
    conn.commit()


_PREVIEW_MAX_LEN = 200


def _preview_from_message(msg: object) -> str:
    """Extract a short plain-text preview from a persisted message dict."""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    text: str
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                for key in ("text", "input_text", "output_text"):
                    v = p.get(key)
                    if isinstance(v, str):
                        parts.append(v)
                        break
        text = " ".join(parts)
    else:
        text = ""
    text = text.strip()
    if len(text) > _PREVIEW_MAX_LEN:
        text = text[: _PREVIEW_MAX_LEN - 1] + "…"
    return text


def _message_stub(msg_json: str | None, created_at: str | None) -> dict | None:
    """Build the trimmed {content, timestamp, role} stub used by list_sessions."""
    if msg_json is None:
        return None
    try:
        msg = json.loads(msg_json)
    except Exception:
        msg = None
    role = msg.get("role") if isinstance(msg, dict) else None
    return {
        "content": _preview_from_message(msg),
        "timestamp": created_at,
        "role": role if isinstance(role, str) else None,
    }


class SqliteSessionStorage(SessionStorageBackend):
    """Sqlite implementation. One ``sqlite3.Connection`` per (project, agent)
    shard, cached in the module-level ``_STORES`` dict."""

    def close(self) -> None:
        for conn, _ in _STORES.values():
            try:
                conn.close()
            except Exception:
                pass
        _STORES.clear()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_store(
        self, project_slug: str | None, agent_slug: str | None
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

    # ------------------------------------------------------------------
    # Retention + hold
    # ------------------------------------------------------------------

    def purge_old_sessions(
        self,
        retention_days: int,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[str]:
        if retention_days <= 0:
            return []
        conn, lock = self._get_store(project_slug, agent_slug)
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with lock:
            cur = conn.execute(
                "SELECT session_id FROM sessions WHERE created_at < ? AND hold = 0",
                (cutoff,),
            )
            old_ids = [row[0] for row in cur.fetchall()]
            if not old_ids:
                return []
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
        return old_ids

    def set_session_hold(
        self,
        session_id: str,
        hold: bool = True,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            cur = conn.execute(
                "UPDATE sessions SET hold=? WHERE session_id=?",
                (1 if hold else 0, session_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_session_hold(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            cur = conn.execute(
                "SELECT hold FROM sessions WHERE session_id=?", (session_id,)
            )
            row = cur.fetchone()
        return bool(row[0]) if row else False

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def load_history(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]:
        t0 = time.perf_counter()
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            cur = conn.execute(
                "SELECT msg_json FROM history WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            )
            rows = cur.fetchall()
        t_query = time.perf_counter()
        total_bytes = sum(len(r[0]) for r in rows)
        result = [json.loads(r[0]) for r in rows]
        t_end = time.perf_counter()
        Debug.log(
            f"[profile] load_history session={session_id} rows={len(rows)} "
            f"bytes={total_bytes} query_ms={(t_query - t0) * 1000:.1f} "
            f"parse_ms={(t_end - t_query) * 1000:.1f} total_ms={(t_end - t0) * 1000:.1f}"
        )
        return result

    def append_message(
        self,
        session_id: str,
        message: dict,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None:
        conn, lock = self._get_store(project_slug, agent_slug)
        msg_json = json.dumps(message)
        with lock:
            conn.execute(
                "INSERT INTO history(session_id, msg_json, created_at) VALUES (?, ?, datetime('now'))",
                (session_id, msg_json),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Session lifecycle + metadata
    # ------------------------------------------------------------------

    def register_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        user_id: str | None = None,
    ) -> None:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(session_id, created_at, user_id) "
                "VALUES (?, datetime('now'), ?)",
                (session_id, user_id),
            )
            conn.commit()

    def get_session_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> Tuple[Any, Any]:
        conn, lock = self._get_store(project_slug, agent_slug)
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
        self,
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
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            conn.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id=?",
                (*values, session_id),
            )
            conn.commit()

    def set_archived(
        self,
        session_id: str,
        archived: bool,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            conn.execute(
                "UPDATE sessions SET archived=? WHERE session_id=?",
                (1 if archived else 0, session_id),
            )
            conn.commit()

    def get_archived(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            cur = conn.execute(
                "SELECT archived FROM sessions WHERE session_id=?", (session_id,)
            )
            row = cur.fetchone()
        return bool(row[0]) if row else False

    def delete_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            cur = conn.execute(
                "SELECT session_id, hold FROM sessions WHERE session_id=?",
                (session_id,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            if row[1]:
                raise ValueError(
                    f"Session {session_id} is under legal hold and cannot be deleted"
                )
            conn.execute("DELETE FROM audio_metadata WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM history WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            conn.commit()
        return True

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_sessions(
        self,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        user_id: str | None = None,
    ) -> list[dict]:
        t0 = time.perf_counter()
        conn, lock = self._get_store(project_slug, agent_slug)
        query = """
            WITH bounds AS (
                SELECT session_id,
                       MIN(id) AS first_id,
                       MAX(id) AS last_id,
                       COUNT(*) AS message_count
                FROM history
                GROUP BY session_id
            )
            SELECT s.session_id,
                   s.archived,
                   s.created_at,
                   s.user_id,
                   COALESCE(b.message_count, 0) AS message_count,
                   fh.msg_json AS first_json,
                   fh.created_at AS first_at,
                   lh.msg_json AS last_json,
                   lh.created_at AS last_at
            FROM sessions s
            LEFT JOIN bounds b ON b.session_id = s.session_id
            LEFT JOIN history fh ON fh.id = b.first_id
            LEFT JOIN history lh ON lh.id = b.last_id
        """
        params: list = []
        # Owner scoping happens in SQL so it composes with LIMIT/OFFSET —
        # filtering after pagination would silently under-deliver pages.
        if user_id is not None:
            query += " WHERE s.user_id = ?"
            params.append(user_id)
        query += " ORDER BY s.created_at DESC, s.session_id DESC"
        if limit is not None:
            # created_at is second-precision here — the session_id tiebreaker
            # keeps the ordering total so pages never overlap or skip.
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        with lock:
            rows = conn.execute(query, params).fetchall()

        result: list[dict] = []
        for (
            session_id,
            archived,
            session_created,
            user_id,
            message_count,
            first_json,
            first_at,
            last_json,
            last_at,
        ) in rows:
            session_data: dict = {
                "id": session_id,
                "archived": bool(archived),
                "created_at": session_created or datetime.now().isoformat(),
                "user_id": user_id,
                "message_count": message_count,
            }
            if message_count and message_count > 0:
                first_stub = _message_stub(first_json, first_at)
                last_stub = _message_stub(last_json, last_at)
                if first_stub is not None:
                    session_data["first_message"] = first_stub
                if last_stub is not None:
                    session_data["last_message"] = last_stub
            result.append(session_data)

        t_end = time.perf_counter()
        Debug.log(
            f"[profile] list_sessions sessions={len(result)} queries=1 "
            f"total_ms={(t_end - t0) * 1000:.1f}"
        )
        return result

    def get_user_messages(
        self,
        *,
        limit: int = 200,
        include_archived: bool = True,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        conn, lock = self._get_store(project_slug, agent_slug)
        results: list[dict] = []
        page_size = 1000
        offset = 0

        conditions: list[str] = []
        if not include_archived:
            conditions.append("s.archived = 0")
        # Owner scoping in SQL: the caller's page budget must be spent on
        # rows they can actually see.
        scope_params: tuple = ()
        if user_id is not None:
            conditions.append("s.user_id = ?")
            scope_params = (user_id,)
        where_archived = ("WHERE " + " AND ".join(conditions)) if conditions else ""

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
                    scope_params + (page_size, offset),
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

    # ------------------------------------------------------------------
    # User identity
    # ------------------------------------------------------------------

    def update_session_user_id(
        self,
        session_id: str,
        user_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            conn.execute(
                "UPDATE sessions SET user_id=? WHERE session_id=?",
                (user_id, session_id),
            )
            conn.commit()

    def get_session_user_id(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> str | None:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return row[0]

    def session_exists(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return row is not None

    def list_sessions_for_user(
        self,
        user_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]:
        conn, lock = self._get_store(project_slug, agent_slug)
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

    # ------------------------------------------------------------------
    # Audio metadata
    # ------------------------------------------------------------------

    def save_audio_metadata(
        self,
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
        conn, lock = self._get_store(project_slug, agent_slug)
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
        assert row_id is not None  # always set after a successful INSERT
        return row_id

    def get_audio_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]:
        conn, lock = self._get_store(project_slug, agent_slug)
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
        self,
        item_id: str,
        content_index: int,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> Optional[dict]:
        conn, lock = self._get_store(project_slug, agent_slug)
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
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> int:
        conn, lock = self._get_store(project_slug, agent_slug)
        with lock:
            cursor = conn.execute(
                "DELETE FROM audio_metadata WHERE session_id=?", (session_id,)
            )
            deleted_count = cursor.rowcount
            conn.commit()
        return deleted_count
