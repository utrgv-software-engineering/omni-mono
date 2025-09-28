from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from datetime import datetime


def _read_yaml(p: Path) -> Dict[str, Any]:
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _extract_ts_from_filename(p: Path) -> str | None:
    m = re.search(r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})", p.name)
    return m.group(1) if m else None


def _compute_workflow_name(agent_yaml: Path, scenarios_path: Path, override_name: str | None) -> str:
    if override_name:
        return override_name
    cfg = _read_yaml(agent_yaml)
    tracing = cfg.get("tracing") or {}
    base_name = tracing.get("name") or agent_yaml.stem
    ts = _extract_ts_from_filename(scenarios_path) or ""
    if ts:
        return f"{base_name}_scenarios_{ts}"
    return base_name


def _open_db(project: str) -> sqlite3.Connection:
    db_path = Path.home() / ".agentio" / f"{project}.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_traces_for_workflow(conn: sqlite3.Connection, workflow_name: str) -> List[Dict[str, Any]]:
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
        rows.append({
            "trace_id": r["trace_id"],
            "group_id": r["group_id"],
            "workflow_name": r["workflow_name"],
            "metadata": meta,
        })
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


def _group_categories(conn: sqlite3.Connection, project: str, group_id: str) -> List[int]:
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
        return (
            meta.get("omniagents", {})
            .get("scenario", {})
            .get("id")
        )
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


def _prune(
    agent_yaml: Path,
    scenarios_path: Path,
    project: str,
    workflow_name: str | None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cfg = _read_yaml(scenarios_path)
    scenarios = list(cfg.get("scenarios") or [])
    if not scenarios:
        return [], []
    wf = _compute_workflow_name(agent_yaml, scenarios_path, workflow_name)
    conn = _open_db(project)
    traces = _load_traces_for_workflow(conn, wf)
    id_to_group: Dict[str, str] = {}
    for t in traces:
        sid = _scenario_id_from_meta(t.get("metadata") or {})
        gid = t.get("group_id")
        if sid and gid and sid not in id_to_group:
            id_to_group[sid] = gid
    id_to_cats: Dict[str, List[str]] = {}
    id_name = _category_id_to_name(conn, project)
    for sid, gid in id_to_group.items():
        cat_ids = _group_categories(conn, project, gid)
        if cat_ids:
            names = [id_name.get(c) for c in cat_ids if c in id_name]
            names = [n for n in names if n]
            if names:
                slugs = sorted(set(_slugify(n) for n in names))
                id_to_cats[sid] = slugs
    pruned: List[Dict[str, Any]] = []
    for sc in scenarios:
        sid = str(sc.get("id") or "")
        cats = id_to_cats.get(sid)
        if cats:
            sc2 = dict(sc)
            sc2["failure_categories"] = cats
            pruned.append(sc2)
    return pruned, scenarios


def main() -> None:
    p = argparse.ArgumentParser(description="Prune scenarios to those with categorized failures and annotate them")
    p.add_argument("--project", "-p", required=True, help="Path to project.yml for dbt-style layout")
    p.add_argument("--input", "-i", default="artifacts/eval/generated_scenarios.yaml", help="Path to generated scenarios YAML")
    p.add_argument("--output", "-o", default="artifacts/eval/pruned_scenarios.yaml", help="Where to write pruned scenarios YAML")
    p.add_argument("--agentio-project", default=None, help="AgentIO project name; defaults from compiled tracing.project")
    p.add_argument("--workflow-name", default=None, help="Explicit workflow name; otherwise derived from input filename timestamp")
    args = p.parse_args()

    try:
        from tools.project_compile import compile_project
        compiled_cfg_path, _graph = compile_project(str(args.project), select=None, exclude=None)
        agent_yaml = Path(compiled_cfg_path).resolve()
    except Exception as e:
        raise SystemExit(f"Failed to compile project: {e}")

    in_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()

    cfg = _read_yaml(agent_yaml)
    tracing = cfg.get("tracing") or {}
    agentio_project = args.agentio_project or tracing.get("project") or "default"

    pruned, original = _prune(agent_yaml, in_path, agentio_project, args.workflow_name)

    ts = _extract_ts_from_filename(in_path) or datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    if out_path.suffix:
        out_path = out_path.with_name(f"{out_path.stem}_{ts}{out_path.suffix}")
    else:
        out_path = out_path.with_name(f"{out_path.name}_{ts}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"scenarios": pruned}
    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True
    out_path.write_text(yaml.dump(data, sort_keys=False, Dumper=NoAliasDumper, allow_unicode=True), encoding="utf-8")
    print(f"Kept {len(pruned)} of {len(original)} scenarios; wrote {out_path}")


if __name__ == "__main__":
    main()
