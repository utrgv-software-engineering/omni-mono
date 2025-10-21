from __future__ import annotations

import json
import re
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from omniagents.core.paths import get_traces_dir


def _read_yaml(p: Path) -> Dict[str, Any]:
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _extract_ts_from_filename(p: Path) -> str | None:
    m = re.search(r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})", p.name)
    return m.group(1) if m else None


def _compute_workflow_name(
    agent_yaml: Path, scenarios_path: Path, override_name: str | None
) -> str:
    if override_name:
        return override_name
    cfg = _read_yaml(agent_yaml)
    tracing = cfg.get("tracing") or {}
    base_name = tracing.get("name") or agent_yaml.stem
    manifest_path = scenarios_path.parent / "manifest.json"
    ts = None
    sc_name = None
    if manifest_path.exists():
        try:
            md = json.loads(manifest_path.read_text(encoding="utf-8"))
            ts = md.get("timestamp") or ts
            sc_name = md.get("name") or sc_name
            wf_run = md.get("workflow_run") or None
            if wf_run:
                return str(wf_run)
        except Exception:
            pass
    if not ts:
        ts = _extract_ts_from_filename(scenarios_path) or ""
    if not sc_name:
        try:
            sc_name = scenarios_path.parent.name
        except Exception:
            sc_name = None
    if ts and sc_name:
        return f"{base_name}_scenarios_{sc_name}_{ts}"
    if ts:
        return f"{base_name}_scenarios_{ts}"
    return base_name


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


def annotate_scenarios(
    agent_yaml: Path,
    scenarios_path: Path,
    studio_project: str,
    workflow_name: str | None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Path]:
    cfg = _read_yaml(scenarios_path)
    scenarios = list(cfg.get("scenarios") or [])
    if not scenarios:
        return [], [], scenarios_path
    wf = _compute_workflow_name(agent_yaml, scenarios_path, workflow_name)
    conn = _open_db(studio_project)
    traces = _load_traces_for_workflow(conn, wf)
    id_to_group: Dict[str, str] = {}
    for t in traces:
        sid = _scenario_id_from_meta(t.get("metadata") or {})
        gid = t.get("group_id")
        if sid and gid and sid not in id_to_group:
            id_to_group[sid] = gid
    id_to_cats: Dict[str, List[str]] = {}
    id_name = _category_id_to_name(conn, studio_project)
    for sid, gid in id_to_group.items():
        cat_ids = _group_categories(conn, studio_project, gid)
        if cat_ids:
            names = [id_name.get(c) for c in cat_ids if c in id_name]
            names = [n for n in names if n]
            if names:
                slugs = sorted(set(_slugify(n) for n in names))
                id_to_cats[sid] = slugs
    annotated: List[Dict[str, Any]] = []
    for sc in scenarios:
        sid = str(sc.get("id") or "")
        cats = id_to_cats.get(sid, [])
        sc2 = dict(sc)
        sc2["failure_categories"] = list(cats)
        annotated.append(sc2)
    return annotated, scenarios, scenarios_path


def write_annotation_output(
    project_root: Path, input_path: Path, annotated: List[Dict[str, Any]]
) -> Path:
    existing: Dict[str, Any]
    if input_path.exists():
        try:
            existing_loaded = (
                yaml.safe_load(input_path.read_text(encoding="utf-8")) or {}
            )
            existing = existing_loaded if isinstance(existing_loaded, dict) else {}
        except Exception:
            existing = {}
    else:
        existing = {}
    data = dict(existing)
    data["scenarios"] = annotated
    input_path.parent.mkdir(parents=True, exist_ok=True)
    if input_path.exists():
        backup = input_path.with_suffix(
            input_path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )
        try:
            shutil.copy2(input_path, backup)
        except Exception:
            pass

    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    input_path.write_text(
        yaml.dump(data, sort_keys=False, Dumper=NoAliasDumper, allow_unicode=True),
        encoding="utf-8",
    )
    return input_path
