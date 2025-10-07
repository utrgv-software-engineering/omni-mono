from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import yaml
import shutil


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
                os = data.get("optimizers")
                if isinstance(os, dict):
                    for k, v in os.items():
                        out[k] = v
                if isinstance(os, list):
                    for o in os:
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
            os = data.get("optimizers")
            if isinstance(os, dict):
                for k, v in os.items():
                    out[k] = v
            if isinstance(os, list):
                for o in os:
                    key = (o or {}).get("name") or (o or {}).get("key")
                    if key:
                        out[key] = o
    return out


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


def _filter_scenarios(items: List[Dict[str, Any]], select: str | None, exclude: str | None) -> List[Dict[str, Any]]:
    import fnmatch
    sel = [s.strip() for s in (select or "").split(",") if s.strip()]
    exc = [s.strip() for s in (exclude or "").split(",") if s.strip()]
    if not sel and not exc:
        return items

    def match_expr(it: Dict[str, Any], expr: str) -> bool:
        if ":" in expr:
            typ, pat = expr.split(":", 1)
            typ = typ.strip().lower()
            pat = pat.strip()
            if typ == "name":
                nm = str(it.get("name") or it.get("id") or "")
                return fnmatch.fnmatch(nm, pat)
            if typ == "tag" or typ == "tags":
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
                if i in keep:
                    keep.remove(i)
    return [items[i] for i in range(len(items)) if i in keep]


def compile_project(project_path: str, select: str | None = None, exclude: str | None = None, agent_key: Optional[str] = None) -> Tuple[Path, Dict[str, Any]]:
    p = Path(project_path).resolve()
    if p.is_file() and p.name != "project.yml":
        raise FileNotFoundError(f"Expected 'project.yml' but got '{p.name}'. Rename to project.yml.")
    base_dir = p.parent if p.is_file() else p
    if not p.is_file():
        proj_file = base_dir / "project.yml"
        if not proj_file.is_file():
            raise FileNotFoundError(f"Could not find project.yml in directory: {base_dir}")
    proj = _read_yaml(p if p.is_file() else (base_dir / "project.yml")) or {}
    paths = proj.get("paths") or {}
    eval_dir = base_dir / str(paths.get("evaluations", "evaluations"))
    agents_dir = base_dir / str(paths.get("agents", "agents"))
    target_dir = base_dir / str(paths.get("target", "target"))
    tools_dir = base_dir / str(paths.get("tools", "tools"))
    guardrails_dir = base_dir / str(paths.get("guardrails", "guardrails"))
    server_functions_dir = base_dir / str(paths.get("server_functions", "server_functions"))

    def _uniq(ps: List[Path]) -> List[Path]:
        seen = set()
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

    scenarios_sources = _uniq([
        base_dir / "evaluations" / "scenarios.yml",
        eval_dir / "scenarios.yml",
        eval_dir / "scenarios",
    ])
    judges_sources = _uniq([
        eval_dir / "judges",
    ])
    optimizers_sources = _uniq([
        eval_dir / "optimizers",
    ])
    metrics_sources = _uniq([
        base_dir / "evaluations" / "metrics.yml",
        eval_dir / "metrics.yml",
        eval_dir / "metrics",
    ])
    base_eval_sources = _uniq([
        base_dir / "evaluations" / "evaluation.yml",
        eval_dir / "evaluation.yml",
        eval_dir,
    ])
    # Resolve agent path via discovery using runtime
    from .runtime import load_project, resolve_agent
    prj = load_project(str(p))
    _key, agent_path = resolve_agent(prj, agent_key=(str(agent_key) if agent_key else None))

    base_eval = _collect_base_eval([s for s in base_eval_sources if s.exists()])
    scenarios = _collect_scenarios([s for s in scenarios_sources if s.exists()])
    judges = _collect_judges([s for s in judges_sources if s.exists()])
    optimizers = _collect_optimizers([s for s in optimizers_sources if s.exists()])
    metrics = _collect_metrics([s for s in metrics_sources if s.exists()])

    if select or exclude:
        scenarios = _filter_scenarios(scenarios, select, exclude)

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

    agent_cfg = _read_yaml(agent_path) or {}
    agent_out = dict(agent_cfg)
    # Inject project-level tracing (strict project-level ownership)
    try:
        if isinstance(proj.get("tracing"), dict):
            agent_out["tracing"] = dict(proj.get("tracing") or {})
    except Exception:
        pass
    agent_out["evaluation"] = compiled_eval

    target_dir.mkdir(parents=True, exist_ok=True)
    compiled_yaml = target_dir / "evaluation_compiled.yml"
    compiled_yaml.write_text(yaml.safe_dump(agent_out, sort_keys=False), encoding="utf-8")

    try:
        instr = agent_cfg.get("instructions_file")
        if isinstance(instr, str) and instr.strip():
            src_instr = (agent_path.parent / instr).resolve()
            dst_instr = (target_dir / Path(instr).name)
            if src_instr.is_file():
                if not (dst_instr.exists() or dst_instr.is_symlink()):
                    try:
                        dst_instr.symlink_to(src_instr)
                    except Exception:
                        shutil.copy2(src_instr, dst_instr)
    except Exception:
        pass

    try:
        src_tools = (agent_path.parent / "tools").resolve()
        dst_tools = (target_dir / "tools")
        if src_tools.is_dir():
            if not (dst_tools.exists() or dst_tools.is_symlink()):
                try:
                    dst_tools.symlink_to(src_tools)
                except Exception:
                    shutil.copytree(src_tools, dst_tools)
        else:
            # If agent-local tools not present, link project tools to standard 'tools' for loader compatibility
            if tools_dir.is_dir() and not (dst_tools.exists() or dst_tools.is_symlink()):
                try:
                    dst_tools.symlink_to(tools_dir.resolve())
                except Exception:
                    try:
                        shutil.copytree(tools_dir.resolve(), dst_tools)
                    except Exception:
                        pass
    except Exception:
        pass

    # Also link project-level shared assets if present
    try:
        if tools_dir.is_dir():
            dst = (target_dir / "project_tools")
            if not (dst.exists() or dst.is_symlink()):
                try:
                    dst.symlink_to(tools_dir.resolve())
                except Exception:
                    shutil.copytree(tools_dir.resolve(), dst)
    except Exception:
        pass

    try:
        if guardrails_dir.is_dir():
            dst = (target_dir / "guardrails")
            if not (dst.exists() or dst.is_symlink()):
                try:
                    dst.symlink_to(guardrails_dir.resolve())
                except Exception:
                    shutil.copytree(guardrails_dir.resolve(), dst)
    except Exception:
        pass

    try:
        if server_functions_dir.is_dir():
            dst = (target_dir / "server_functions")
            if not (dst.exists() or dst.is_symlink()):
                try:
                    dst.symlink_to(server_functions_dir.resolve())
                except Exception:
                    shutil.copytree(server_functions_dir.resolve(), dst)
    except Exception:
        pass

    try:
        src_measures = (eval_dir / "measures.py")
        if src_measures.is_file():
            dst_measures = (target_dir / "measures.py")
            if not (dst_measures.exists() or dst_measures.is_symlink()):
                try:
                    dst_measures.symlink_to(src_measures.resolve())
                except Exception:
                    shutil.copy2(src_measures.resolve(), dst_measures)
    except Exception:
        pass

    # Agents manifest for runtime tooling
    try:
        from .runtime import load_project
        prj = load_project(str(p))
        manifest = {
            "entrypoint": prj.entrypoint,
            "agents": [{"key": k, "file": str(v)} for k, v in prj.agents_map.items()],
        }
        (target_dir / "agents.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception:
        pass

    graph = {
        "project_name": proj.get("name") or base_dir.name,
        "agent_file": str(agent_path),
        "compiled_file": str(compiled_yaml),
        "resources": {
            "scenarios": [s.get("name") or s.get("id") for s in scenarios],
            "judges": list(judges.keys()),
            "metrics": [m.get("name") or m.get("label") for m in metrics],
        },
    }
    (target_dir / "graph.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")

    return compiled_yaml, graph
