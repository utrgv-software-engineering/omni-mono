"""Postgres-backed session storage.

Cloud-friendly counterpart to :class:`SqliteSessionStorage`: durable across
launcher container restarts, safe for concurrent writers across replicas,
single-server-shared across all (project, agent) shards. The schema mirrors
the SQLite tables (``sessions``, ``history``, ``audio_metadata``) with three
differences:

  * an explicit ``tenant_id`` column on every table — set from the
    ``OMNIAGENTS_TENANT_ID`` env at construction, included in every PK and
    every WHERE clause. RLS is enabled with permissive policies that gate
    on the ``app.current_tenant`` GUC, so when a non-owner role is wired up
    later it will enforce isolation without any backend changes.
  * JSON columns are ``JSONB`` instead of ``TEXT`` — same payloads, but
    indexable / queryable from operators and lifecycle tooling.
  * timestamps are ``TIMESTAMPTZ``; list_sessions / get_user_messages
    serialize them to ISO-8601 strings to match the SQLite backend's output
    shape (which consumers downstream — the launcher renderer included —
    already expect).

The connection pool is shared across all tenants/shards. There's no
per-(project, agent) sharding at the storage layer — the composite key
disambiguates rows, and the indexes do the rest.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from omniagents.core.debug import Debug

from .base import SessionStorageBackend, _UNSET

logger = logging.getLogger(__name__)

# Sentinel tenant id used when the env var isn't set — keeps the
# single-tenant Postgres deployment story working (one db, one tenant,
# RLS effectively a no-op) without requiring callers to thread tenant
# context through every operation.
DEFAULT_TENANT = "local"

# Schema lives inline so the backend has no dependency on a migrations
# directory. ``CREATE TABLE IF NOT EXISTS`` means re-runs are no-ops.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    tenant_id     TEXT        NOT NULL,
    project_slug  TEXT        NOT NULL,
    agent_slug    TEXT        NOT NULL,
    session_id    TEXT        NOT NULL,
    archived      BOOLEAN     NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    context       JSONB,
    variables     JSONB,
    hold          BOOLEAN     NOT NULL DEFAULT false,
    user_id       TEXT,
    PRIMARY KEY (tenant_id, project_slug, agent_slug, session_id)
);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS sdk_context JSONB;

CREATE INDEX IF NOT EXISTS idx_sessions_created_at
    ON sessions (tenant_id, project_slug, agent_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON sessions (tenant_id, project_slug, agent_slug, user_id);

CREATE TABLE IF NOT EXISTS history (
    id            BIGSERIAL   PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    project_slug  TEXT        NOT NULL,
    agent_slug    TEXT        NOT NULL,
    session_id    TEXT        NOT NULL,
    msg           JSONB       NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_history_session
    ON history (tenant_id, project_slug, agent_slug, session_id, id);

CREATE TABLE IF NOT EXISTS history_projection_outbox (
    history_id BIGINT PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
    intent JSONB NOT NULL
);
ALTER TABLE history_projection_outbox ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname=current_schema()
        AND tablename='history_projection_outbox' AND policyname='tenant_isolation') THEN
        CREATE POLICY tenant_isolation ON history_projection_outbox USING (
            EXISTS (SELECT 1 FROM history h WHERE h.id=history_id
                AND h.tenant_id=current_setting('app.current_tenant', true))
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS audio_metadata (
    id             BIGSERIAL   PRIMARY KEY,
    tenant_id      TEXT        NOT NULL,
    project_slug   TEXT        NOT NULL,
    agent_slug     TEXT        NOT NULL,
    session_id     TEXT        NOT NULL,
    message_id     BIGINT      REFERENCES history(id) ON DELETE CASCADE,
    item_id        TEXT        NOT NULL,
    content_index  INTEGER     NOT NULL,
    file_path      TEXT        NOT NULL,
    audio_format   TEXT        NOT NULL,
    duration_ms    INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audio_session
    ON audio_metadata (tenant_id, project_slug, agent_slug, session_id);

CREATE INDEX IF NOT EXISTS idx_audio_item
    ON audio_metadata (tenant_id, project_slug, agent_slug, item_id, content_index);

ALTER TABLE sessions       ENABLE ROW LEVEL SECURITY;
ALTER TABLE history        ENABLE ROW LEVEL SECURITY;
ALTER TABLE audio_metadata ENABLE ROW LEVEL SECURITY;

-- Policies match rows whose tenant_id equals the per-tx
-- ``app.current_tenant`` GUC. The backend sets the GUC at the top of every
-- operation. Until a non-owner role is in use, RLS is permissive (PG
-- bypasses RLS for table owners) but the policies are pre-positioned for
-- defense in depth when the role gets locked down.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = current_schema()
        AND tablename = 'sessions' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON sessions
            USING (tenant_id = current_setting('app.current_tenant', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = current_schema()
        AND tablename = 'history' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON history
            USING (tenant_id = current_setting('app.current_tenant', true));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = current_schema()
        AND tablename = 'audio_metadata' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON audio_metadata
            USING (tenant_id = current_setting('app.current_tenant', true));
    END IF;
END
$$;
"""


def _iso(ts: Any) -> str | None:
    """Coerce a datetime (or already-stringified value) to ISO-8601.

    psycopg returns aware ``datetime`` objects for TIMESTAMPTZ columns. The
    SQLite backend returned strings shaped like ``2026-05-28 12:34:56``. We
    normalise to ISO-8601 with timezone so renderer code can parse without
    branching on backend."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(ts)


_PREVIEW_MAX_LEN = 200


def _preview_from_message(msg: object) -> str:
    """Same plain-text extraction as the SQLite backend."""
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


def _message_stub(msg: object, created_at: Any) -> dict | None:
    """JSONB columns come back already decoded — no ``json.loads`` needed."""
    if msg is None:
        return None
    role = msg.get("role") if isinstance(msg, dict) else None
    return {
        "content": _preview_from_message(msg),
        "timestamp": _iso(created_at),
        "role": role if isinstance(role, str) else None,
    }


class PgSessionStorage(SessionStorageBackend):
    """Postgres implementation of the session-storage backend."""

    def __init__(
        self,
        dsn: str,
        *,
        tenant_id: str | None = None,
        min_size: int = 1,
        max_size: int = 8,
    ) -> None:
        try:
            from psycopg.types.json import Jsonb  # noqa: F401 — used in queries
            from psycopg_pool import ConnectionPool
        except ImportError as e:
            raise ImportError(
                "PgSessionStorage requires the 'session-postgres' extra. "
                "Install with: pip install 'omniagents[session-postgres]'"
            ) from e

        self._dsn = dsn
        self._tenant_id = (
            tenant_id or os.getenv("OMNIAGENTS_TENANT_ID") or DEFAULT_TENANT
        )
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=True,
            kwargs={"autocommit": False},
        )
        self._bootstrap()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _bootstrap(self) -> None:
        """Idempotent schema + policy install."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
            conn.commit()

    def _conn(self):
        """Open a connection from the pool and set ``app.current_tenant`` so
        the RLS policy (and our own WHERE clauses, which echo it for parity)
        match."""
        cm = self._pool.connection()
        conn = cm.__enter__()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.current_tenant', %s, true)",
                    (self._tenant_id,),
                )
        except Exception:
            cm.__exit__(None, None, None)
            raise
        return _PooledConn(cm, conn, self._tenant_id)

    def close(self) -> None:
        try:
            self._pool.close()
        except Exception:
            logger.exception("PgSessionStorage: pool close failed")

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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND created_at < now() - make_interval(days => %s)
                      AND hold = false
                    RETURNING session_id
                    """,
                    (conn.tenant_id, ps, ags, retention_days),
                )
                purged_ids = [row[0] for row in cur.fetchall()]
                if purged_ids:
                    # history + audio_metadata don't FK to sessions; clean
                    # them by the same (tenant, project, agent, session_id)
                    # tuple to mirror SQLite's purge semantics.
                    cur.execute(
                        """
                        DELETE FROM history
                        WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                          AND session_id = ANY(%s)
                        """,
                        (conn.tenant_id, ps, ags, purged_ids),
                    )
                    # audio_metadata cascades from history via FK, but we may
                    # have audio rows without a message_id, so explicit too.
                    cur.execute(
                        """
                        DELETE FROM audio_metadata
                        WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                          AND session_id = ANY(%s)
                        """,
                        (conn.tenant_id, ps, ags, purged_ids),
                    )
            conn.commit()
        return purged_ids

    def set_session_hold(
        self,
        session_id: str,
        hold: bool = True,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sessions SET hold = %s
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (hold, conn.tenant_id, ps, ags, session_id),
                )
                rowcount = cur.rowcount
            conn.commit()
        return rowcount > 0

    def get_session_hold(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT hold FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                row = cur.fetchone()
        return bool(row[0]) if row else False

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def load_sdk_context(
        self, session_id: str, *, project_slug=None, agent_slug=None
    ) -> list[dict] | None:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sdk_context FROM sessions WHERE tenant_id=%s AND project_slug=%s AND agent_slug=%s AND session_id=%s",
                    (conn.tenant_id, ps, ags, session_id),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def replace_sdk_context(
        self, session_id: str, items: list[dict], *, project_slug=None, agent_slug=None
    ) -> None:
        from psycopg.types.json import Jsonb

        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions(tenant_id,project_slug,agent_slug,session_id,sdk_context) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT(tenant_id,project_slug,agent_slug,session_id) DO UPDATE SET sdk_context=excluded.sdk_context",
                    (conn.tenant_id, ps, ags, session_id, Jsonb(items)),
                )
            conn.commit()

    def load_history(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]:
        t0 = time.perf_counter()
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT msg FROM history
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    ORDER BY id ASC
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                rows = cur.fetchall()
        result = [row[0] for row in rows]
        Debug.log(
            f"[profile] load_history(pg) session={session_id} rows={len(rows)} "
            f"total_ms={(time.perf_counter() - t0) * 1000:.1f}"
        )
        return result

    def append_message(
        self,
        session_id: str,
        message: dict,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        projection: dict | None = None,
    ) -> None:
        from psycopg.types.json import Jsonb

        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO history (tenant_id, project_slug, agent_slug,
                                         session_id, msg)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """,
                    (conn.tenant_id, ps, ags, session_id, Jsonb(message)),
                )
                row_id = cur.fetchone()[0]
                cur.execute(
                    "UPDATE sessions SET sdk_context=sdk_context || %s WHERE tenant_id=%s AND project_slug=%s AND agent_slug=%s AND session_id=%s AND sdk_context IS NOT NULL",
                    (Jsonb([message]), conn.tenant_id, ps, ags, session_id),
                )
                if projection is not None:
                    cur.execute(
                        "INSERT INTO history_projection_outbox(history_id,intent) VALUES (%s,%s)",
                        (row_id, Jsonb(projection)),
                    )
            conn.commit()

    def pending_projections(
        self, session_id: str, *, project_slug=None, agent_slug=None
    ) -> list[dict]:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT h.id,h.msg,o.intent FROM history h "
                    "JOIN history_projection_outbox o ON o.history_id=h.id "
                    "WHERE h.tenant_id=%s AND h.project_slug=%s AND h.agent_slug=%s "
                    "AND h.session_id=%s ORDER BY h.id",
                    (conn.tenant_id, ps, ags, session_id),
                )
                return [
                    {"row_id": row[0], "message": row[1], "intent": row[2]}
                    for row in cur.fetchall()
                ]

    def acknowledge_projection(
        self, session_id: str, row_id: int, *, project_slug=None, agent_slug=None
    ) -> None:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM history_projection_outbox o USING history h "
                    "WHERE o.history_id=h.id AND h.id=%s AND h.tenant_id=%s "
                    "AND h.project_slug=%s AND h.agent_slug=%s AND h.session_id=%s",
                    (row_id, conn.tenant_id, ps, ags, session_id),
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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (tenant_id, project_slug, agent_slug,
                                          session_id, user_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, project_slug, agent_slug, session_id)
                    DO NOTHING
                    """,
                    (conn.tenant_id, ps, ags, session_id, user_id),
                )
            conn.commit()

    def get_session_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> Tuple[Any, Any]:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT context, variables FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                row = cur.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

    def update_session_metadata(
        self,
        session_id: str,
        *,
        context: object = _UNSET,
        variables: object = _UNSET,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None:
        from psycopg.types.json import Jsonb

        assignments: list[str] = []
        values: list[Any] = []
        if context is not _UNSET:
            assignments.append("context = %s")
            values.append(Jsonb(context) if context is not None else None)
        if variables is not _UNSET:
            assignments.append("variables = %s")
            values.append(Jsonb(variables) if variables is not None else None)
        if not assignments:
            return
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE sessions SET {', '.join(assignments)}
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (*values, conn.tenant_id, ps, ags, session_id),
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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sessions SET archived = %s
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (archived, conn.tenant_id, ps, ags, session_id),
                )
            conn.commit()

    def get_archived(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT archived FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT hold FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                if row[0]:
                    raise ValueError(
                        f"Session {session_id} is under legal hold and cannot be deleted"
                    )
                # audio cascades via FK from history; explicit second delete
                # covers audio rows that have NULL message_id.
                cur.execute(
                    """
                    DELETE FROM audio_metadata
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                cur.execute(
                    """
                    DELETE FROM history
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                cur.execute(
                    """
                    DELETE FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
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
        ps, ags = _slugs(project_slug, agent_slug)
        # Same CTE shape as SQLite — single query, first/last message join.
        query = """
            WITH bounds AS (
                SELECT session_id,
                       MIN(id) AS first_id,
                       MAX(id) AS last_id,
                       COUNT(*) AS message_count
                FROM history
                WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                GROUP BY session_id
            )
            SELECT s.session_id,
                   s.archived,
                   s.created_at,
                   s.user_id,
                   COALESCE(b.message_count, 0) AS message_count,
                   fh.msg AS first_msg,
                   fh.created_at AS first_at,
                   lh.msg AS last_msg,
                   lh.created_at AS last_at
            FROM sessions s
            LEFT JOIN bounds b ON b.session_id = s.session_id
            LEFT JOIN history fh ON fh.id = b.first_id
            LEFT JOIN history lh ON lh.id = b.last_id
            WHERE s.tenant_id = %s AND s.project_slug = %s AND s.agent_slug = %s
        """
        # Owner scoping happens in SQL so it composes with LIMIT/OFFSET —
        # filtering after pagination would silently under-deliver pages.
        if user_id is not None:
            query += " AND s.user_id = %s"
        query += " ORDER BY s.created_at DESC, s.session_id DESC"
        if limit is not None:
            query += " LIMIT %s OFFSET %s"
        with self._conn() as conn:
            with conn.cursor() as cur:
                params: tuple = (
                    conn.tenant_id,
                    ps,
                    ags,
                    conn.tenant_id,
                    ps,
                    ags,
                )
                if user_id is not None:
                    params += (user_id,)
                if limit is not None:
                    params += (limit, offset)
                cur.execute(query, params)
                rows = cur.fetchall()

        result: list[dict] = []
        for (
            session_id,
            archived,
            session_created,
            user_id,
            message_count,
            first_msg,
            first_at,
            last_msg,
            last_at,
        ) in rows:
            session_data: dict = {
                "id": session_id,
                "archived": bool(archived),
                "created_at": _iso(session_created) or datetime.now().isoformat(),
                "user_id": user_id,
                "message_count": message_count,
            }
            if message_count and message_count > 0:
                first_stub = _message_stub(first_msg, first_at)
                last_stub = _message_stub(last_msg, last_at)
                if first_stub is not None:
                    session_data["first_message"] = first_stub
                if last_stub is not None:
                    session_data["last_message"] = last_stub
            result.append(session_data)

        Debug.log(
            f"[profile] list_sessions(pg) sessions={len(result)} "
            f"total_ms={(time.perf_counter() - t0) * 1000:.1f}"
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
        ps, ags = _slugs(project_slug, agent_slug)
        # Filter on role and content type server-side — JSONB lets us push
        # the predicate down so we don't page through tool calls etc.
        archived_filter = "" if include_archived else " AND s.archived = false"
        # Owner scoping in SQL: the caller's LIMIT must be spent on rows
        # they can actually see.
        owner_filter = " AND s.user_id = %s" if user_id is not None else ""
        owner_params: tuple = (user_id,) if user_id is not None else ()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT h.msg, h.created_at, h.session_id
                    FROM history h
                    JOIN sessions s
                      ON s.tenant_id = h.tenant_id
                     AND s.project_slug = h.project_slug
                     AND s.agent_slug = h.agent_slug
                     AND s.session_id = h.session_id
                    WHERE h.tenant_id = %s AND h.project_slug = %s AND h.agent_slug = %s
                      AND h.msg ->> 'role' = 'user'
                      AND jsonb_typeof(h.msg -> 'content') = 'string'
                      AND length(trim(h.msg ->> 'content')) > 0
                      {archived_filter}{owner_filter}
                    ORDER BY h.id DESC
                    LIMIT %s
                    """,
                    (conn.tenant_id, ps, ags) + owner_params + (limit,),
                )
                rows = cur.fetchall()
        results: list[dict] = []
        for msg, created_at, session_id in rows:
            if not isinstance(msg, dict):
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
                    "timestamp": _iso(created_at),
                }
            )
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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sessions SET user_id = %s
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (user_id, conn.tenant_id, ps, ags, session_id),
                )
            conn.commit()

    def get_session_user_id(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> str | None:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                row = cur.fetchone()
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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM sessions
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                row = cur.fetchone()
        return row is not None

    def list_sessions_for_user(
        self,
        user_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.session_id, s.archived, s.created_at,
                           COUNT(h.id) AS message_count
                    FROM sessions s
                    LEFT JOIN history h
                      ON h.tenant_id = s.tenant_id
                     AND h.project_slug = s.project_slug
                     AND h.agent_slug = s.agent_slug
                     AND h.session_id = s.session_id
                    WHERE s.tenant_id = %s AND s.project_slug = %s AND s.agent_slug = %s
                      AND s.user_id = %s
                    GROUP BY s.session_id, s.archived, s.created_at
                    ORDER BY s.created_at DESC
                    """,
                    (conn.tenant_id, ps, ags, user_id),
                )
                rows = cur.fetchall()
        return [
            {
                "id": sid,
                "archived": bool(archived),
                "created_at": _iso(created) or datetime.now().isoformat(),
                "message_count": msg_count,
            }
            for sid, archived, created, msg_count in rows
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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audio_metadata (
                        tenant_id, project_slug, agent_slug, session_id,
                        message_id, item_id, content_index, file_path,
                        audio_format, duration_ms
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        conn.tenant_id,
                        ps,
                        ags,
                        session_id,
                        message_id,
                        item_id,
                        content_index,
                        file_path,
                        audio_format,
                        duration_ms,
                    ),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
        return row_id

    def get_audio_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, message_id, item_id, content_index, file_path,
                           audio_format, duration_ms, created_at
                    FROM audio_metadata
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    ORDER BY id ASC
                    """,
                    (conn.tenant_id, ps, ags, session_id),
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
                "created_at": _iso(row[7]),
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
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, session_id, message_id, file_path, audio_format,
                           duration_ms, created_at
                    FROM audio_metadata
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND item_id = %s AND content_index = %s
                    LIMIT 1
                    """,
                    (conn.tenant_id, ps, ags, item_id, content_index),
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
            "created_at": _iso(row[6]),
        }

    def delete_audio_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> int:
        ps, ags = _slugs(project_slug, agent_slug)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM audio_metadata
                    WHERE tenant_id = %s AND project_slug = %s AND agent_slug = %s
                      AND session_id = %s
                    """,
                    (conn.tenant_id, ps, ags, session_id),
                )
                rowcount = cur.rowcount
            conn.commit()
        return rowcount


def _slugs(project_slug: str | None, agent_slug: str | None) -> tuple[str, str]:
    """Mirror the SQLite path's default-slug behaviour. ``None`` shards
    collapse to ``"default"`` so a missing slug doesn't fragment storage
    across NULL and the literal string."""
    return (project_slug or "default", agent_slug or "default")


class _PooledConn:
    """Tiny context-manager wrapper around a pooled psycopg connection.

    Exists so per-op callers can write ``with self._conn() as conn:`` and
    still reach ``conn.tenant_id`` without re-binding to the pool every
    time. ``__exit__`` defers to the pool's context manager so transactions
    that raise are rolled back automatically."""

    def __init__(self, cm, conn, tenant_id: str) -> None:
        self._cm = cm
        self._conn = conn
        self.tenant_id = tenant_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._cm.__exit__(exc_type, exc, tb)

    def cursor(self, *a, **kw):
        return self._conn.cursor(*a, **kw)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()
