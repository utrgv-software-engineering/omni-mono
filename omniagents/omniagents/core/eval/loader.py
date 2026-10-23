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

from omniagents.core.eval.layout import (
    JUDGE_FILE,
    OPTIMIZER_FILE,
    EvalLayout,
    check_layout,
    evaluations_dir,
)

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

    judge_sources: Dict[str, Path] = field(default_factory=dict)
    """Per-judge YAML source path. Used to resolve a judge's relative paths
    (``instructions_file``, ``template_file``) and to locate the file an
    editor would write back to. Keys match ``config['judges']``; the path is
    the YAML the merge picked the judge up from (last-write-wins, mirroring
    merge order)."""

    select: Optional[str] = None
    """Scenario selector the config was loaded with (``name:foo*,tag:smoke``), if any."""

    exclude: Optional[str] = None
    """Scenario exclusion the config was loaded with, if any."""

    split: Optional[str] = None
    """Holdout split the scenarios were restricted to (``train``/``dev``/``test``), if any."""

    holdout_manifest: Dict[str, Any] = field(default_factory=dict)
    """The holdout's manifest (seed, strata, counts) when ``split`` is set."""


# ---------------------------------------------------------------------------
# YAML / dict helpers (extracted from compiler.py)
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ensure_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


# ---------------------------------------------------------------------------
# Scenario collection
# ---------------------------------------------------------------------------


def load_scenarios_file(base_dir: Path, rel_or_abs: str) -> List[Dict[str, Any]]:
    """The scenarios in a scenarios file (a ``scenarios:`` list or a bare
    list), with its includes expanded relative to it. Each scenario carries
    the file it is written in as ``_source_path``, which runs record so a
    review can find it. A file that is named but missing, or does not
    parse, raises: a suite silently missing scenarios measures less than
    it claims."""
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = (base_dir / rel_or_abs).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Scenarios file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Scenarios file {p} does not parse: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("scenarios")
    return _expand_scenarios(p, data if isinstance(data, list) else [])


def _expand_scenarios(source: Path, scenarios: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in scenarios or []:
        if isinstance(item, str):
            out.extend(load_scenarios_file(source.parent, item))
            continue
        if isinstance(item, dict) and ("include" in item or "file" in item):
            val = item.get("include") if "include" in item else item.get("file")
            for included in val if isinstance(val, list) else [val]:
                if isinstance(included, str):
                    out.extend(load_scenarios_file(source.parent, included))
            continue
        if isinstance(item, dict):
            item.setdefault("_source_path", str(source))
            out.append(item)
    return out


def _collect_scenarios(files: List[Path]) -> List[Dict[str, Any]]:
    scenarios: List[Dict[str, Any]] = []
    for path in files:
        scenarios.extend(load_scenarios_file(path.parent, str(path)))
    return scenarios


# ---------------------------------------------------------------------------
# Judges and optimizers: one folder each, named by its key
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
            # Files named in the referenced file are relative to it.
            for block, key in (
                ("agent", "instructions_file"),
                ("input", "template_file"),
            ):
                section = data.get(block)
                value = section.get(key) if isinstance(section, dict) else None
                if (
                    isinstance(value, str)
                    and value.strip()
                    and not Path(value).is_absolute()
                ):
                    section[key] = str((p.parent / value).resolve())
            if "agent" in data and isinstance(data["agent"], dict):
                jc.setdefault("agent", data["agent"])
            else:
                for k in ("agent", "input"):
                    if k in data and isinstance(data[k], dict):
                        jc.setdefault(k, data[k])
    return jc


def _collect_folders(
    root: Path, filename: str
) -> Dict[str, Tuple[Dict[str, Any], Path]]:
    """``<root>/<key>/<filename>`` for each key: the file's settings and path.
    A file that does not parse raises, naming it."""
    out: Dict[str, Tuple[Dict[str, Any], Path]] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob(f"*/{filename}")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} does not parse: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a mapping of settings")
        out[path.parent.name] = (data, path)
    return out


def _resolve_files(
    what: str, cfg: Dict[str, Any], source: Path, fields: Tuple[Tuple[str, str], ...]
) -> None:
    """Make the files ``cfg`` names absolute, resolved against ``source``,
    in place. A named file that is missing raises: a judge or optimizer
    without its instructions would run generic ones and quietly measure
    something else."""
    for block, key_name in fields:
        section = cfg.get(block)
        value = section.get(key_name) if isinstance(section, dict) else None
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value)
        path = path if path.is_absolute() else (source.parent / path).resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f"{what}: {key_name} not found: {path} "
                f"(relative paths are resolved from {source})"
            )
        section[key_name] = str(path)


_JUDGE_FILES = (("agent", "instructions_file"), ("input", "template_file"))
_OPTIMIZER_FILES = (
    ("agent", "instructions_file"),
    ("proposals", "instructions_template_file"),
    ("proposals", "template_template_file"),
)


def _collect_judges(root: Path) -> Tuple[Dict[str, Any], Dict[str, Path]]:
    judges: Dict[str, Any] = {}
    sources: Dict[str, Path] = {}
    for key, (cfg, path) in _collect_folders(root, JUDGE_FILE).items():
        cfg = _resolve_judge_agent_ref(path.parent, cfg)
        _resolve_files(f"Judge {key!r}", cfg, path, _JUDGE_FILES)
        judges[key] = cfg
        sources[key] = path
    return judges, sources


def _collect_optimizers(root: Path) -> Dict[str, Any]:
    optimizers: Dict[str, Any] = {}
    for key, (cfg, path) in _collect_folders(root, OPTIMIZER_FILE).items():
        _resolve_files(f"Optimizer {key!r}", cfg, path, _OPTIMIZER_FILES)
        optimizers[key] = cfg
    return optimizers


def _evaluation_settings(path: Path) -> Dict[str, Any]:
    """``evaluation.yml``'s settings (under ``evaluation:`` or bare)."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} does not parse: {exc}") from exc
    if not isinstance(data, dict):
        return {}
    block = data.get("evaluation")
    return dict(block) if isinstance(block, dict) else dict(data)


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
                from omniagents.core.eval.measure_tiers import measure_names

                ms = measure_names(it.get("measures"))
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
# Main entry point
# ---------------------------------------------------------------------------


def load_eval_config(
    project_path: str,
    select: str | None = None,
    exclude: str | None = None,
    agent_key: Optional[str] = None,
    split: Optional[str] = None,
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
    split:
        Restrict scenarios to a holdout split (``train``, ``dev`` or ``test``)
        created by ``omniagents eval scenarios holdout``. Applied after
        ``select``/``exclude``.
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
    eval_dir = evaluations_dir(base_dir, proj)
    layout = EvalLayout(eval_dir, base_dir.resolve())
    check_layout(layout)

    # Resolve agent via the project runtime
    prj = load_project(str(p))
    _key, agent_path = resolve_agent(
        prj, agent_key=(str(agent_key) if agent_key else None)
    )

    settings = _evaluation_settings(layout.evaluation_file)
    scenarios = _collect_scenarios(layout.scenario_files())
    judges, judge_sources = _collect_judges(layout.judges_dir)
    optimizers = _collect_optimizers(layout.optimizers_dir)
    metrics = [m for m in settings.get("metrics") or [] if m is not None]

    # Filter
    if select or exclude:
        scenarios = filter_scenarios(scenarios, select, exclude)
    holdout_manifest: Dict[str, Any] = {}
    if split:
        from omniagents.core.eval.holdout import load_holdout, scenario_key

        names, holdout_manifest = load_holdout(layout, str(split))
        wanted = set(names)
        scenarios = [sc for sc in scenarios if scenario_key(sc) in wanted]

    # Validate
    _validate_graph_resources(scenarios, metrics, judges)

    compiled_eval: Dict[str, Any] = dict(settings)
    for key, value in (
        ("judges", judges),
        ("optimizers", optimizers),
        ("metrics", metrics),
        ("scenarios", scenarios),
    ):
        if value:
            compiled_eval[key] = value
        else:
            compiled_eval.pop(key, None)

    # Read agent config
    agent_cfg = _read_yaml(agent_path) or {}

    # Tracing config — the loader's product-derived defaults with the
    # manifest's declared `tracing:` block merged over them. Agent runs
    # default to no tracing, but an evaluation records traces locally
    # unless the manifest names a provider: Studio's review screens read
    # them.
    tracing_config: Dict[str, Any] = dict(prj.tracing_defaults or {})
    declared_tracing = proj.get("tracing") if isinstance(proj, dict) else None
    if not (isinstance(declared_tracing, dict) and declared_tracing.get("provider")):
        tracing_config["provider"] = "studio"

    return EvalConfig(
        config=compiled_eval,
        agent_config=agent_cfg,
        agent_path=agent_path.resolve(),
        eval_dir=eval_dir,
        project_root=base_dir.resolve(),
        tracing_config=tracing_config,
        project_config=proj,
        judge_sources=judge_sources,
        select=(str(select) if select else None),
        exclude=(str(exclude) if exclude else None),
        split=(str(split) if split else None),
        holdout_manifest=holdout_manifest,
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

    product_section = (
        eval_config.project_config.get("product")
        if isinstance(eval_config.project_config, dict)
        else None
    )
    if product_section is not None:
        from omniagents.core.product import product_info_from_mapping

        project_name = product_info_from_mapping(product_section).slug
    else:
        project_name = eval_config.project_root.name

    return {
        "project_name": project_name,
        "agent_file": str(eval_config.agent_path),
        "resources": {
            "scenarios": scenario_names,
            "judges": list(judges.keys()),
            "metrics": metric_names,
        },
    }
