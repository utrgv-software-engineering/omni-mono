"""Runtime evaluation config loader.

Replaces the compile-then-read-from-target pattern with direct loading from
the ``evaluations/`` directory.  No files are written to disk -- the merged
config is returned as an :class:`EvalConfig` dataclass ready for use by the
eval pipeline, scenario generator, judges, and optimizers.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


# ---------------------------------------------------------------------------
# Data class returned to callers
# ---------------------------------------------------------------------------


@dataclass
class EvalConfig:
    """Fully resolved evaluation configuration."""

    config: Dict[str, Any]
    """Merged evaluation dict (scenarios, judges, metrics, optimizers, etc.)."""

    agent_config: Dict[str, Any]
    """The raw agent YAML dict (without injected ``evaluation`` key)."""

    agent_path: Path
    """Absolute path to the agent YAML file on disk."""

    eval_dir: Path
    """Absolute path to the ``evaluations/`` directory (base for relative paths)."""

    project_root: Path
    """Absolute path to the project root (where ``project.yml`` lives)."""

    tracing_config: Dict[str, Any] = field(default_factory=dict)
    """Project-level tracing configuration."""

    project_config: Dict[str, Any] = field(default_factory=dict)
    """Raw ``project.yml`` dict."""


# ---------------------------------------------------------------------------
# YAML / dict helpers (extracted from compiler.py)
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _merge_dict(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _ensure_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


# ---------------------------------------------------------------------------
# Scenario collection
# ---------------------------------------------------------------------------


def _load_scenarios_file(base_dir: Path, rel_or_abs: str) -> List[Dict[str, Any]]:
    try:
        p = Path(rel_or_abs)
        if not p.is_absolute():
            p = (base_dir / rel_or_abs).resolve()
        if not p.is_file():
            return []
        data = _read_yaml(p)
        if isinstance(data, dict):
            sc = data.get("scenarios")
            if isinstance(sc, list):
                return _expand_scenarios(p.parent, sc)
            return []
        if isinstance(data, list):
            return _expand_scenarios(p.parent, data)
        return []
    except Exception:
        return []


def _expand_scenarios(base_dir: Path, scenarios: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in scenarios or []:
        if isinstance(item, str):
            out.extend(_load_scenarios_file(base_dir, item))
            continue
        if isinstance(item, dict) and ("include" in item or "file" in item):
            val = item.get("include") if "include" in item else item.get("file")
            if isinstance(val, list):
                for v in val:
                    out.extend(_load_scenarios_file(base_dir, str(v)))
            elif isinstance(val, str):
                out.extend(_load_scenarios_file(base_dir, val))
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _collect_scenarios(paths: List[Path]) -> List[Dict[str, Any]]:
    all_items: List[Any] = []
    for p in paths:
        if p.is_file():
            data = _read_yaml(p)
            if isinstance(data, dict):
                items = data.get("scenarios")
                if isinstance(items, list):
                    all_items.extend(items)
            elif isinstance(data, list):
                all_items.extend(data)
            continue
        if not p.is_dir():
            continue
        for y in sorted(list(p.rglob("*.yml"))):
            data = _read_yaml(y)
            if isinstance(data, dict):
                items = data.get("scenarios")
                if isinstance(items, list):
                    all_items.extend(items)
            elif isinstance(data, list):
                all_items.extend(data)
    return _expand_scenarios(Path.cwd(), all_items)


# ---------------------------------------------------------------------------
# Judge / optimizer / metric collection
# ---------------------------------------------------------------------------


def _resolve_judge_agent_ref(base: Path, jcfg: Dict[str, Any]) -> Dict[str, Any]:
    jc = dict(jcfg or {})
    ref = jc.get("agent_ref") or jc.get("ref")
    if isinstance(ref, str):
        p = Path(ref)
        if not p.is_absolute():
            p = (base / p).resolve()
        data = _read_yaml(p)
        if isinstance(data, dict):
            if "agent" in data and isinstance(data["agent"], dict):
                jc.setdefault("agent", data["agent"])
            else:
                for k in ("agent", "input"):
                    if k in data and isinstance(data[k], dict):
                        jc.setdefault(k, data[k])
    return jc


def _collect_judges(paths: List[Path]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for p in paths:
        if p.is_file():
            data = _read_yaml(p)
            if isinstance(data, dict):
                js = data.get("judges")
                if isinstance(js, dict):
                    for k, v in js.items():
                        out[k] = _resolve_judge_agent_ref(p.parent, v)
                if isinstance(js, list):
                    for j in js:
                        key = (j or {}).get("name") or (j or {}).get("key")
                        if key:
                            out[key] = _resolve_judge_agent_ref(p.parent, j)
            continue
        if not p.is_dir():
            continue
        for y in sorted(list(p.rglob("*.yml"))):
            data = _read_yaml(y)
            if not isinstance(data, dict):
                continue
            js = data.get("judges")
            if isinstance(js, dict):
                for k, v in js.items():
                    out[k] = _resolve_judge_agent_ref(y.parent, v)
            if isinstance(js, list):
                for j in js:
                    key = (j or {}).get("name") or (j or {}).get("key")
                    if key:
                        out[key] = _resolve_judge_agent_ref(y.parent, j)
    return out


def _collect_optimizers(paths: List[Path]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for p in paths:
        if p.is_file():
            data = _read_yaml(p)
            if isinstance(data, dict):
                os_ = data.get("optimizers")
                if isinstance(os_, dict):
                    for k, v in os_.items():
                        out[k] = v
                if isinstance(os_, list):
                    for o in os_:
                        key = (o or {}).get("name") or (o or {}).get("key")
                        if key:
                            out[key] = o
            continue
        if not p.is_dir():
            continue
        for y in sorted(list(p.rglob("*.yml"))):
            data = _read_yaml(y)
            if not isinstance(data, dict):
                continue
            os_ = data.get("optimizers")
            if isinstance(os_, dict):
                for k, v in os_.items():
                    out[k] = v
            if isinstance(os_, list):
                for o in os_:
                    key = (o or {}).get("name") or (o or {}).get("key")
                    if key:
                        out[key] = o
    return out


def _collect_metrics(paths: List[Path]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in paths:
        if p.is_file():
            data = _read_yaml(p)
            if isinstance(data, dict):
                ms = data.get("metrics")
                if isinstance(ms, list):
                    out.extend(ms)
            continue
        if not p.is_dir():
            continue
        for y in sorted(list(p.rglob("*.yml"))):
            data = _read_yaml(y)
            if not isinstance(data, dict):
                continue
            ms = data.get("metrics")
            if isinstance(ms, list):
                out.extend(ms)
    return out


def _collect_base_eval(paths: List[Path]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for p in paths:
        if not p.exists():
            continue
        if p.is_file():
            data = _read_yaml(p)
            if isinstance(data, dict):
                if isinstance(data.get("evaluation"), dict):
                    result = _merge_dict(result, data["evaluation"])
                else:
                    result = _merge_dict(result, data)
            continue
        for y in sorted(list(p.rglob("*.yml"))):
            data = _read_yaml(y)
            if isinstance(data, dict) and isinstance(data.get("evaluation"), dict):
                result = _merge_dict(result, data["evaluation"])
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_graph_resources(
    scenarios: List[Dict[str, Any]],
    metrics: List[Dict[str, Any]],
    judges: Dict[str, Any],
) -> Tuple[List[str], List[str], List[str]]:
    scenario_names: List[str] = []
    seen_scenarios: set[str] = set()
    for idx, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ValueError(f"Scenario {idx} invalid type: {type(scenario).__name__}")
        identifier = scenario.get("name") or scenario.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"Scenario {idx} missing 'name' or 'id'")
        token = identifier.strip()
        if token in seen_scenarios:
            raise ValueError(f"Duplicate scenario identifier '{token}'")
        seen_scenarios.add(token)
        scenario_names.append(token)
        if isinstance(scenario.get("name"), str):
            scenario["name"] = token
        elif isinstance(scenario.get("id"), str):
            scenario["id"] = token
    metric_names: List[str] = []
    seen_metrics: set[str] = set()
    for idx, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            raise ValueError(f"Metric {idx} invalid type: {type(metric).__name__}")
        identifier = metric.get("name") or metric.get("label")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"Metric {idx} missing 'name' or 'label'")
        token = identifier.strip()
        if token in seen_metrics:
            raise ValueError(f"Duplicate metric identifier '{token}'")
        seen_metrics.add(token)
        metric_names.append(token)
        if isinstance(metric.get("name"), str):
            metric["name"] = token
        elif isinstance(metric.get("label"), str):
            metric["label"] = token
    judge_keys: List[str] = []
    invalid_judges: List[str] = []
    for key in judges.keys():
        if not isinstance(key, str) or not key.strip():
            invalid_judges.append(str(key))
        else:
            judge_keys.append(key)
    if invalid_judges:
        raise ValueError(
            "Judge keys must be non-empty strings: " + ", ".join(invalid_judges)
        )
    return scenario_names, metric_names, judge_keys


# ---------------------------------------------------------------------------
# Scenario filtering (public -- used by CLI and Studio API)
# ---------------------------------------------------------------------------


def filter_scenarios(
    items: List[Dict[str, Any]], select: str | None, exclude: str | None
) -> List[Dict[str, Any]]:
    """Filter a scenario list by select/exclude expressions.

    Supports ``id:``, ``name:``, ``tag:``/``tags:``, ``measure:`` prefixes
    with fnmatch glob patterns.  Multiple expressions are comma-separated.
    """
    sel = [s.strip() for s in (select or "").split(",") if s.strip()]
    exc = [s.strip() for s in (exclude or "").split(",") if s.strip()]
    if not sel and not exc:
        return items

    def match_expr(it: Dict[str, Any], expr: str) -> bool:
        if ":" in expr:
            typ, pat = expr.split(":", 1)
            typ = typ.strip().lower()
            pat = pat.strip()
            if typ == "id":
                scenario_id = str(it.get("id") or "")
                return fnmatch.fnmatch(scenario_id, pat)
            if typ == "name":
                nm = str(it.get("name") or it.get("id") or "")
                return fnmatch.fnmatch(nm, pat)
            if typ in ("tag", "tags"):
                tags = [str(t) for t in _ensure_list(it.get("tags"))]
                return any(fnmatch.fnmatch(t, pat) for t in tags)
            if typ == "measure":
                ms = [str(m) for m in _ensure_list(it.get("measures"))]
                return any(fnmatch.fnmatch(m, pat) for m in ms)
        return fnmatch.fnmatch(str(it.get("name") or ""), expr)

    keep = set(range(len(items)))
    if sel:
        sel_idx = set()
        for i, it in enumerate(items):
            if any(match_expr(it, s) for s in sel):
                sel_idx.add(i)
        keep &= sel_idx
    if exc:
        for i, it in enumerate(items):
            if any(match_expr(it, e) for e in exc):
                keep.discard(i)
    return [items[i] for i in range(len(items)) if i in keep]


# ---------------------------------------------------------------------------
# De-duplication helper for source paths
# ---------------------------------------------------------------------------


def _uniq(ps: List[Path]) -> List[Path]:
    seen: set[str] = set()
    out: List[Path] = []
    for x in ps:
        try:
            key = str(x.resolve())
        except Exception:
            key = str(x)
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def load_eval_config(
    project_path: str,
    select: str | None = None,
    exclude: str | None = None,
    agent_key: Optional[str] = None,
) -> EvalConfig:
    """Load and merge evaluation config at runtime.

    This replaces ``compile_project()`` from ``compiler.py``.  Instead of
    writing a merged YAML to ``target/``, it returns the merged config in
    memory as an :class:`EvalConfig`.

    Parameters
    ----------
    project_path:
        Path to ``project.yml`` or the directory containing it.
    select:
        Comma-separated scenario selectors (e.g. ``"name:foo*,tag:smoke"``).
    exclude:
        Comma-separated scenario exclusions.
    agent_key:
        Agent key to resolve.  ``None`` uses the project entrypoint.
    """
    from omniagents.core.project.runtime import load_project, resolve_agent

    p = Path(project_path).resolve()
    if p.is_file() and p.name != "project.yml":
        raise FileNotFoundError(
            f"Expected 'project.yml' but got '{p.name}'. Rename to project.yml."
        )
    base_dir = p.parent if p.is_file() else p
    if not p.is_file():
        proj_file = base_dir / "project.yml"
        if not proj_file.is_file():
            raise FileNotFoundError(
                f"Could not find project.yml in directory: {base_dir}"
            )

    proj = _read_yaml(p if p.is_file() else (base_dir / "project.yml")) or {}
    paths = proj.get("paths") or {}
    eval_dir = (base_dir / str(paths.get("evaluations", "evaluations"))).resolve()

    # Resolve agent via the project runtime
    prj = load_project(str(p))
    _key, agent_path = resolve_agent(
        prj, agent_key=(str(agent_key) if agent_key else None)
    )

    # Source paths for each resource type
    scenarios_sources = _uniq(
        [
            base_dir / "evaluations" / "scenarios.yml",
            eval_dir / "scenarios.yml",
            eval_dir / "scenarios",
        ]
    )
    judges_sources = _uniq([eval_dir / "judges"])
    optimizers_sources = _uniq([eval_dir / "optimizers"])
    metrics_sources = _uniq(
        [
            base_dir / "evaluations" / "metrics.yml",
            eval_dir / "metrics.yml",
            eval_dir / "metrics",
        ]
    )
    base_eval_sources = _uniq(
        [
            base_dir / "evaluations" / "evaluation.yml",
            eval_dir / "evaluation.yml",
            eval_dir,
        ]
    )

    # Collect everything
    base_eval = _collect_base_eval([s for s in base_eval_sources if s.exists()])
    scenarios = _collect_scenarios([s for s in scenarios_sources if s.exists()])
    judges = _collect_judges([s for s in judges_sources if s.exists()])
    optimizers = _collect_optimizers([s for s in optimizers_sources if s.exists()])
    metrics = _collect_metrics([s for s in metrics_sources if s.exists()])

    # Filter
    if select or exclude:
        scenarios = filter_scenarios(scenarios, select, exclude)

    # Validate
    _validate_graph_resources(scenarios, metrics, judges)

    # Merge into single eval config dict
    compiled_eval: Dict[str, Any] = {}
    compiled_eval = _merge_dict(compiled_eval, base_eval)
    if judges:
        compiled_eval["judges"] = judges
    if optimizers:
        compiled_eval["optimizers"] = optimizers
    if metrics:
        compiled_eval["metrics"] = metrics
    if scenarios:
        compiled_eval["scenarios"] = scenarios

    # Read agent config
    agent_cfg = _read_yaml(agent_path) or {}

    # Extract tracing config
    tracing_config: Dict[str, Any] = {}
    try:
        if isinstance(proj.get("tracing"), dict):
            tracing_config = dict(proj.get("tracing") or {})
    except Exception:
        pass

    return EvalConfig(
        config=compiled_eval,
        agent_config=agent_cfg,
        agent_path=agent_path.resolve(),
        eval_dir=eval_dir,
        project_root=base_dir.resolve(),
        tracing_config=tracing_config,
        project_config=proj,
    )


# ---------------------------------------------------------------------------
# Lightweight introspection (replaces graph.json generation)
# ---------------------------------------------------------------------------


def introspect_resources(eval_config: EvalConfig) -> Dict[str, Any]:
    """Return a resource summary dict (equivalent to the old ``graph.json``).

    This can be used by the ``ls`` command and Studio UI to enumerate
    scenarios, judges, and metrics without writing anything to disk.
    """
    cfg = eval_config.config
    scenarios = cfg.get("scenarios") or []
    judges = cfg.get("judges") or {}
    metrics = cfg.get("metrics") or []

    scenario_names = []
    for sc in scenarios:
        name = (sc.get("name") or sc.get("id") or "") if isinstance(sc, dict) else ""
        if name:
            scenario_names.append(name)

    metric_names = []
    for m in metrics:
        name = (m.get("name") or m.get("label") or "") if isinstance(m, dict) else ""
        if name:
            metric_names.append(name)

    return {
        "project_name": eval_config.project_config.get("name")
        or eval_config.project_root.name,
        "agent_file": str(eval_config.agent_path),
        "resources": {
            "scenarios": scenario_names,
            "judges": list(judges.keys()),
            "metrics": metric_names,
        },
    }
