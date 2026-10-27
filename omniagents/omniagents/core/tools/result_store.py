"""Session-scoped original tool results; model previews are separate derivatives."""

from __future__ import annotations
import json
import os
import re
import sqlite3
import time
from .output_budget import output_dir, save_output, read_output

MAX_INLINE_BYTES = 1_000_000


def json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _connect(scope, session_id):
    directory = output_dir(scope, session_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "results.sqlite3"
    # Create privately before SQLite opens it (also on a permissive umask).
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS results (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, tool TEXT, call_id TEXT, created REAL, bytes INTEGER, value TEXT)"
    )
    return db


def retain(scope, session_id, tool, call_id, value):
    value = json_value(value)
    serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
    text = value if isinstance(value, str) else serialized
    reference = save_output(scope, session_id, text)
    db = _connect(scope, session_id)
    try:
        with db:
            db.execute(
                "INSERT INTO results(id,tool,call_id,created,bytes,value) VALUES(?,?,?,?,?,?)",
                (
                    reference,
                    tool,
                    call_id,
                    time.time(),
                    len(serialized.encode()),
                    serialized,
                ),
            )
    finally:
        db.close()
    return {
        "id": reference,
        "status": "completed",
        "completeness": "tool_return_value",
        "retention": "saved",
        "value": value,
    }


def list_results(scope, session_id, after=0, limit=50):
    if (
        type(after) is not int
        or after < 0
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("after must be nonnegative and limit must be 1..100")
    db = _connect(scope, session_id)
    try:
        rows = db.execute(
            "SELECT seq,id,tool,call_id,created,bytes FROM results WHERE seq > ? ORDER BY seq LIMIT ?",
            (after, limit + 1),
        ).fetchall()
        return {
            "results": [dict(row) for row in rows[:limit]],
            "next_after": rows[limit - 1]["seq"] if len(rows) > limit else None,
        }
    finally:
        db.close()


def get_result(scope, session_id, reference):
    if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{32}", reference):
        raise ValueError("Result is unavailable in this session")
    db = _connect(scope, session_id)
    try:
        row = db.execute(
            "SELECT bytes FROM results WHERE id=?", (reference,)
        ).fetchone()
        if row is None:
            raise ValueError("Result is unavailable in this session")
        result = {
            "id": reference,
            "status": "completed",
            "completeness": "tool_return_value",
            "retention": "saved",
        }
        if row["bytes"] > MAX_INLINE_BYTES:
            return {
                **result,
                "inline": False,
                "bytes": row["bytes"],
                "read": "session.results.read({reference, offset, limit})",
            }
        value = db.execute(
            "SELECT value FROM results WHERE id=?", (reference,)
        ).fetchone()
        return {**result, "value": json.loads(value["value"])}
    finally:
        db.close()
