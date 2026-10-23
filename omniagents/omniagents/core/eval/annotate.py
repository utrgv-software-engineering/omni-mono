from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List

from omniagents.core.paths import get_traces_dir


def _open_db(project: str) -> sqlite3.Connection:
    db_path = get_traces_dir() / f"{project}.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_traces_for_workflow(
    conn: sqlite3.Connection, workflow_name: str
) -> List[Dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT trace_id, group_id, workflow_name, metadata
        FROM traces
        WHERE workflow_name = ?
        """,
        (workflow_name,),
    )
    rows = []
    for r in cur:
        md = r["metadata"]
        try:
            meta = json.loads(md) if isinstance(md, str) else (md or {})
        except Exception:
            meta = {}
        rows.append(
            {
                "trace_id": r["trace_id"],
                "group_id": r["group_id"],
                "workflow_name": r["workflow_name"],
                "metadata": meta,
            }
        )
    return rows


def _category_id_to_name(conn: sqlite3.Connection, project: str) -> Dict[int, str]:
    cur = conn.execute(
        """
        SELECT id, name
        FROM failure_categories
        WHERE project = ?
        """,
        (project,),
    )
    return {int(r["id"]): str(r["name"]) for r in cur}


def _group_categories(
    conn: sqlite3.Connection, project: str, group_id: str
) -> List[int]:
    cur = conn.execute(
        """
        SELECT category_id
        FROM analysis_categories
        WHERE project = ? AND group_id = ?
        """,
        (project, group_id),
    )
    return [int(r["category_id"]) for r in cur]


def _scenario_id_from_meta(meta: Dict[str, Any]) -> str | None:
    try:
        return meta.get("omniagents", {}).get("scenario", {}).get("id")
    except Exception:
        return None


def _slugify(s: str) -> str:
    t = str(s).lower()
    try:
        t = t.encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass
    t = re.sub(r"[^a-z0-9]+", "_", t)
    t = t.strip("_")
    t = re.sub(r"_+", "_", t)
    return t or "category"


def review_labels(studio_project: str, workflow_name: str) -> Dict[str, List[str]]:
    """The failure categories reviewers gave each scenario's conversation in
    the traces of the run named ``workflow_name``, by scenario id."""
    labels: Dict[str, List[str]] = {}
    conn = _open_db(studio_project)
    try:
        id_to_group: Dict[str, str] = {}
        for trace in _load_traces_for_workflow(conn, workflow_name):
            sid = _scenario_id_from_meta(trace.get("metadata") or {})
            gid = trace.get("group_id")
            if sid and gid and sid not in id_to_group:
                id_to_group[sid] = gid
        id_name = _category_id_to_name(conn, studio_project)
        for sid, gid in id_to_group.items():
            names = [
                id_name[c]
                for c in _group_categories(conn, studio_project, gid)
                if c in id_name
            ]
            if names:
                labels[sid] = sorted({_slugify(n) for n in names})
    finally:
        conn.close()
    return labels
