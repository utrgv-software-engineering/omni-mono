from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omniagents.core.paths import get_sessions_db_path
from omniagents.core.session.history_db import list_sessions


def parse_datetime_arg(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw_norm = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw_norm)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def ensure_sessions_schema(*, project: str, agent: str) -> None:
    db_path = get_sessions_db_path(project, agent)
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("SELECT 1 FROM sessions LIMIT 1")
        sessions_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        history_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(history)").fetchall()
        }
        if "created_at" not in sessions_cols or "created_at" not in history_cols:
            list_sessions(project_slug=project, agent_slug=agent)
    except sqlite3.OperationalError:
        list_sessions(project_slug=project, agent_slug=agent)
    finally:
        conn.close()


def query_sessions(
    *,
    project: str,
    agent: str,
    include_archived: bool,
    after: str | None,
    before: str | None,
    limit: int | None,
    offset: int,
) -> list[dict[str, Any]]:
    db_path = get_sessions_db_path(project, agent)
    if not db_path.exists():
        return []

    where: list[str] = []
    params: list[Any] = []
    if not include_archived:
        where.append("s.archived = 0")
    if after:
        where.append("s.created_at >= ?")
        params.append(after)
    if before:
        where.append("s.created_at < ?")
        params.append(before)
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    paging_sql = ""
    if limit is not None:
        paging_sql = "LIMIT ?"
        params.append(limit)
        if offset:
            paging_sql += " OFFSET ?"
            params.append(offset)
    elif offset:
        paging_sql = "LIMIT -1 OFFSET ?"
        params.append(offset)

    conn = sqlite3.connect(str(db_path))
    try:
        # Check if context_json column exists
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        has_context = "context_json" in cols

        select_cols = "s.session_id, s.archived, s.created_at"
        if has_context:
            select_cols += ", s.context_json"

        cur = conn.execute(
            f"""
            SELECT {select_cols}
            FROM sessions s
            {where_sql}
            ORDER BY s.created_at DESC
            {paging_sql}
            """,
            tuple(params),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        session_id, archived, created_at = row[0], row[1], row[2]
        workspace_root = None
        if has_context and len(row) > 3 and row[3]:
            try:
                ctx = json.loads(row[3])
                if isinstance(ctx, dict):
                    workspace_root = ctx.get("workspace_root")
            except Exception:
                pass
        entry: dict[str, Any] = {
            "id": session_id,
            "archived": bool(archived),
            "created_at": created_at,
            "message_count": None,
        }
        if workspace_root:
            entry["workspace_root"] = workspace_root
        results.append(entry)
    return results


def count_sessions(
    *,
    project: str,
    agent: str,
    include_archived: bool,
    after: str | None,
    before: str | None,
) -> int:
    db_path = get_sessions_db_path(project, agent)
    if not db_path.exists():
        return 0

    where: list[str] = []
    params: list[Any] = []
    if not include_archived:
        where.append("archived = 0")
    if after:
        where.append("created_at >= ?")
        params.append(after)
    if before:
        where.append("created_at < ?")
        params.append(before)
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(f"SELECT COUNT(1) FROM sessions {where_sql}", tuple(params))
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def _message_preview(message: Any, *, content_chars: int) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    timestamp = message.get("timestamp")
    content = message.get("content")
    if isinstance(content, str):
        preview_content = (
            content
            if len(content) <= content_chars
            else content[:content_chars]
            + f"\n...[TRUNCATED {len(content) - content_chars} chars]..."
        )
    elif content is None:
        preview_content = ""
    else:
        rendered = json.dumps(content, ensure_ascii=False, default=str)
        preview_content = (
            rendered
            if len(rendered) <= content_chars
            else rendered[:content_chars]
            + f"\n...[TRUNCATED {len(rendered) - content_chars} chars]..."
        )
    out: dict[str, Any] = {"role": role, "content": preview_content}
    if timestamp is not None:
        out["timestamp"] = timestamp
    return out


def populate_session_stats(
    *, project: str, agent: str, sessions: list[dict[str, Any]]
) -> None:
    if not sessions:
        return
    db_path = get_sessions_db_path(project, agent)
    if not db_path.exists():
        return
    session_ids = [s.get("id") for s in sessions if isinstance(s.get("id"), str)]
    session_ids = [sid for sid in session_ids if sid]
    if not session_ids:
        return

    placeholders = ",".join("?" for _ in session_ids)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            f"""
            SELECT session_id, COUNT(id) as message_count, MIN(id) as first_id, MAX(id) as last_id
            FROM history
            WHERE session_id IN ({placeholders})
            GROUP BY session_id
            """,
            tuple(session_ids),
        )
        stats_rows = cur.fetchall()

        first_last_ids: list[int] = []
        stats_by_session: dict[str, tuple[int, int | None, int | None]] = {}
        for sid, count, first_id, last_id in stats_rows:
            stats_by_session[str(sid)] = (int(count or 0), first_id, last_id)
            if first_id is not None:
                first_last_ids.append(int(first_id))
            if last_id is not None and last_id != first_id:
                first_last_ids.append(int(last_id))

        msgs_by_id: dict[int, tuple[str, str | None]] = {}
        if first_last_ids:
            placeholders2 = ",".join("?" for _ in first_last_ids)
            cur = conn.execute(
                f"SELECT id, msg_json, created_at FROM history WHERE id IN ({placeholders2})",
                tuple(first_last_ids),
            )
            for mid, msg_json, created_at in cur.fetchall():
                msgs_by_id[int(mid)] = (msg_json, created_at)
    finally:
        conn.close()

    for session in sessions:
        sid = session.get("id")
        if not isinstance(sid, str) or not sid:
            continue
        stats = stats_by_session.get(sid)
        if not stats:
            session["message_count"] = 0
            continue
        message_count, first_id, last_id = stats
        session["message_count"] = message_count

        if first_id is not None:
            payload = msgs_by_id.get(int(first_id))
            if payload:
                first_json, first_ts = payload
                try:
                    first_msg = json.loads(first_json)
                    if isinstance(first_msg, dict) and first_ts:
                        first_msg["timestamp"] = first_ts
                except Exception:
                    first_msg = None
                preview = _message_preview(first_msg, content_chars=240)
                if preview is not None:
                    session["first_message"] = preview

        if last_id is not None:
            payload = msgs_by_id.get(int(last_id))
            if payload:
                last_json, last_ts = payload
                try:
                    last_msg = json.loads(last_json)
                    if isinstance(last_msg, dict) and last_ts:
                        last_msg["timestamp"] = last_ts
                except Exception:
                    last_msg = None
                preview = _message_preview(last_msg, content_chars=240)
                if preview is not None:
                    session["last_message"] = preview


def search_session_messages(
    *,
    project: str,
    agent: str,
    query: str,
    role: str = "any",
    include_archived: bool = False,
    after: str | None = None,
    before: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    db_path = get_sessions_db_path(project, agent)
    if not db_path.exists():
        return []

    q_lower = query.lower()
    page_size = min(1000, max(limit, 1) * 10)
    page_offset = 0
    skip = max(offset, 0)
    results: list[dict[str, Any]] = []

    while len(results) < limit:
        conn = sqlite3.connect(str(db_path))
        try:
            where_archived = "" if include_archived else "AND s.archived = 0"
            where_after = "AND h.created_at >= ?" if after else ""
            where_before = "AND h.created_at < ?" if before else ""
            params: list[Any] = [f"%{query}%"]
            if after:
                params.append(after)
            if before:
                params.append(before)
            params.extend([page_size, page_offset])
            cur = conn.execute(
                f"""
                SELECT h.id, h.session_id, h.created_at, h.msg_json, s.archived
                FROM history h
                JOIN sessions s ON s.session_id = h.session_id
                WHERE h.msg_json LIKE ?
                {where_after}
                {where_before}
                {where_archived}
                ORDER BY h.id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            )
            rows = list(cur.fetchall())
        finally:
            conn.close()

        if not rows:
            break

        for msg_id, session_id, created_at, msg_json, archived in rows:
            try:
                msg = json.loads(msg_json)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            msg_role = msg.get("role")
            if role != "any" and msg_role != role:
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            if q_lower not in content.lower():
                continue
            if skip:
                skip -= 1
                continue
            results.append(
                {
                    "session_id": session_id,
                    "message_id": msg_id,
                    "timestamp": created_at,
                    "role": msg_role,
                    "archived": bool(archived),
                    "content": content,
                }
            )
            if len(results) >= limit:
                break

        page_offset += page_size

    return results
