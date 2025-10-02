from __future__ import annotations

import asyncio
import json
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

import yaml

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.session.history_db import load_history
from omniagents.core.evaluation import (
    build_eval_context,
    run_measures,
    final_assistant_text,
    parse_tool_calls,
    parse_tool_outputs,
    link_outputs,
)
from omniagents.core.eval.metrics import compute_metrics
from omniagents.core.utils.jinja import render_template, read_text_file


def _failure_category_map(eval_cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    cats = eval_cfg.get("failure_categories") or []
    if isinstance(cats, dict):
        for k, v in cats.items():
            if isinstance(v, dict):
                ms = v.get("measures") or []
            elif isinstance(v, list):
                ms = v
            else:
                ms = []
            out[str(k)] = [str(m) for m in (ms or [])]
    elif isinstance(cats, list):
        for c in cats:
            if not isinstance(c, dict):
                continue
            name = c.get("name") or c.get("key") or c.get("id")
            if not name:
                continue
            ms = c.get("measures") or []
            out[str(name)] = [str(m) for m in (ms or [])]
    return out


def _load_local_measures(cfg_path: Path) -> None:
    try:
        base_dir = cfg_path.parent
        candidates = [
            base_dir / "measures.py",
            base_dir / "evaluation" / "measures.py",
            base_dir / "eval" / "measures.py",
        ]
        try:
            agents_manifest = base_dir / "agents.json"
            if agents_manifest.is_file():
                import json as _json
                data = _json.loads(agents_manifest.read_text(encoding="utf-8"))
                items = (data.get("agents") or [])
                agent_file = None
                if isinstance(items, list) and items:
                    try:
                        agent_file = Path((items[0] or {}).get("file")) if isinstance(items[0], dict) else None
                    except Exception:
                        agent_file = None
                prj_root = None
                if agent_file:
                    cur = agent_file.resolve().parent
                    while True:
                        if (cur / "project.yml").is_file():
                            prj_root = cur
                            break
                        if cur == cur.parent:
                            break
                        cur = cur.parent
                if prj_root:
                    import yaml as _yaml
                    proj = _yaml.safe_load((prj_root / "project.yml").read_text(encoding="utf-8")) or {}
                    paths = proj.get("paths") or {}
                    eval_paths = paths.get("evaluations", "evaluations")
                    eval_dirs = eval_paths if isinstance(eval_paths, list) else [eval_paths]
                    for ed in eval_dirs:
                        ep = Path(ed)
                        if not ep.is_absolute():
                            ep = (prj_root / ep).resolve()
                        candidates.append(ep / "measures.py")
        except Exception:
            pass
        for py in candidates:
            if py.is_file():
                mod_name = f"eval_measures_{py.parent.name}"
                spec = importlib.util.spec_from_file_location(mod_name, str(py))
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    import sys as _sys
                    _sys.modules[mod_name] = module
                    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    except Exception:
        pass


def _derive_label(raw: str) -> str:
    try:
        t = str(raw or "").strip().replace("_", " ")
        if t.lower() in ("non existent", "non_existent"):
            return "Non-existent"
        return t[:1].upper() + t[1:]
    except Exception:
        return str(raw)


def _catalog_with_labels(dimensions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in dimensions or []:
        name = d.get("name")
        label = d.get("label") or _derive_label(name)
        values = []
        for v in (d.get("values") or []):
            v_name = v.get("name")
            v_label = v.get("label") or _derive_label(v_name)
            values.append({**v, "name": v_name, "label": v_label})
        out.append({**{k: v for k, v in d.items() if k not in ("values",)}, "name": name, "label": label, "values": values})
    return out


def _derive_judge_keys_from_measures(measures: List[str]) -> List[str]:
    keys: List[str] = []
    for m in measures or []:
        if isinstance(m, str) and m.endswith("_agent"):
            base = m[: -len("_agent")]
            if base:
                keys.append(base)
    seen = set()
    uniq: List[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def _collect_judges_for_scenario(eval_cfg: Dict[str, Any], scenario: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    judges_cfg = eval_cfg.get("judges") or {}
    if isinstance(judges_cfg, list):
        tmp: Dict[str, Any] = {}
        for j in judges_cfg:
            key = (j or {}).get("key") or (j or {}).get("name")
            if key:
                tmp[key] = j
        judges_cfg = tmp
    if not isinstance(judges_cfg, dict):
        judges_cfg = {}

    measures = list(scenario.get("measures") or [])
    derived = _derive_judge_keys_from_measures(measures)

    wanted = scenario.get("judges", "auto")
    selected_keys: List[str] = []
    if wanted is None or (isinstance(wanted, str) and wanted.lower() == "none") or wanted is False:
        selected_keys = []
    elif wanted is True or (isinstance(wanted, str) and wanted.lower() == "auto") or wanted == "":
        selected_keys = list(derived)
    elif isinstance(wanted, list):
        selected_keys = list({*derived, *[str(x) for x in wanted]})
    else:
        selected_keys = list({*derived, str(wanted)})

    results: List[tuple[str, Dict[str, Any]]] = []
    for k in selected_keys:
        cfg = judges_cfg.get(k)
        if cfg:
            results.append((k, cfg))
    return results


async def _run_judges(cfg_path: Path, eval_cfg: Dict[str, Any], scenario: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    judges = _collect_judges_for_scenario(eval_cfg, scenario)
    if not judges:
        return metadata

    for key, jcfg in judges:
        agent_cfg = (jcfg or {}).get("agent") or {}
        input_cfg = (jcfg or {}).get("input") or {}
        source = str(input_cfg.get("source", "final_assistant_text")).strip()
        text = ""
        try:
            if source == "final_assistant_text":
                text = final_assistant_text(history) or ""
            elif source == "history":
                text = json.dumps(history)
            else:
                text = final_assistant_text(history) or ""
        except Exception:
            text = ""

        template_str = input_cfg.get("template") or ""
        template_file = input_cfg.get("template_file")
        if (not template_str) and template_file:
            template_str = read_text_file(cfg_path.parent, str(template_file))
        if not template_str:
            template_str = "{{ text }}"
        judge_prompt = render_template(template_str, text=text, history=history, history_json=json.dumps(history))

        try:
            from agents import Runner as AgentsRunner
            from omniagents.core.agents.specs import AgentSpec
            from omniagents.core.agents.builder import _default_build_agent
        except Exception:
            continue
        instructions = agent_cfg.get("instructions") or ""
        instr_file = agent_cfg.get("instructions_file")
        if (not instructions) and instr_file:
            instructions = read_text_file(cfg_path.parent, str(instr_file))
        if not instructions:
            instructions = "You are an evaluation agent. Return ONLY a JSON object appropriate for this judge."
        model = agent_cfg.get("model", "gpt-4o-mini")
        output_type = agent_cfg.get("output_type", "dict")
        output_type_strict = bool(agent_cfg.get("output_type_strict", False))
        expect_dict = False
        if isinstance(output_type, str) and output_type.lower() == "dict":
            base_out_type = dict
            expect_dict = True
        elif isinstance(output_type, str) and output_type.lower() == "str":
            base_out_type = str
        elif output_type in (dict, str, list):
            base_out_type = output_type
            if output_type is dict:
                expect_dict = True
        else:
            base_out_type = dict
            expect_dict = True

        if expect_dict and isinstance(agent_cfg.get("output_schema"), dict):
            try:
                from typing_extensions import TypedDict
                schema = agent_cfg.get("output_schema") or {}
                props = schema.get("properties", {}) or {}
                def _map_js_type(t):
                    t = (t or '').lower()
                    return {
                        'string': str,
                        'boolean': bool,
                        'bool': bool,
                        'number': float,
                        'float': float,
                        'integer': int,
                        'int': int,
                        'object': dict,
                        'array': list,
                    }.get(t, str)
                ann = {k: _map_js_type((v or {}).get('type')) for k, v in props.items()}
                td_name = f"Judge_{key.title().replace('-', '_').replace(' ', '')}_Output"
                base_out_type = TypedDict(td_name, ann)  # type: ignore
            except Exception:
                pass

        try:
            get_instr = lambda: instructions
            spec = AgentSpec(
                name=agent_cfg.get("name") or f"Judge:{key}",
                header_title=agent_cfg.get("name") or f"Judge:{key}",
                page_title=agent_cfg.get("name") or f"Judge:{key}",
                welcome_text="",
                client_storage_prefix=f"judge_{key}.",
                get_agent_instructions=get_instr,
                build_agent=None,
                available_tools=[],
                model_name=model,
                output_type=base_out_type,
                output_type_strict=output_type_strict,
            )
            judge_agent = await _default_build_agent({}, None, spec, None)
            judge_res = await AgentsRunner.run(judge_agent, judge_prompt, max_turns=int(agent_cfg.get("max_turns", 1)))
            val = getattr(judge_res, "final_output", None)
            if expect_dict and not isinstance(val, dict):
                s = str(val)
                try:
                    t = s.strip()
                    if t.startswith("```"):
                        t = t.strip('`')
                        if t.lower().startswith("json"):
                            t = t[4:]
                        t = t.strip()
                    if not (t.startswith("{") and t.endswith("}")):
                        i = t.find("{")
                        j = t.rfind("}")
                        if i != -1 and j != -1 and j > i:
                            t = t[i:j+1]
                    val = json.loads(t)
                except Exception:
                    val = {"raw": s}
            metadata[key] = val
        except Exception:
            metadata[key] = {"error": "judge_failed"}
    return metadata


async def _run_scenario(agent_yaml: str, scenario: Dict[str, Any], defaults: Dict[str, Any], eval_cfg: Dict[str, Any], workflow_name: str | None = None) -> Dict[str, Any]:
    spec = load_agent_spec_from_yaml(agent_yaml)
    spec.use_safe_agent = False
    try:
        if "max_turns" in defaults:
            spec.max_turns = int(defaults.get("max_turns", spec.max_turns))
    except Exception:
        pass
    agent = await create_unified_agent(spec)
    prompt = scenario.get("prompt", "")
    msgs = [{"role": "user", "content": prompt}]
    if workflow_name:
        try:
            from agentio import trace as agentio_trace
            try:
                import omniagents as _oa
                _oa_version = getattr(_oa, "__version__", None)
            except Exception:
                _oa_version = None
            try:
                cfg = yaml.safe_load(Path(agent_yaml).read_text(encoding="utf-8")) or {}
                raw_dims = list((((cfg.get("evaluation") or {}).get("synthetic_data_generation") or {}).get("dimensions")) or [])
                dims_catalog = _catalog_with_labels(raw_dims)
            except Exception:
                dims_catalog = []
            import hashlib as _hashlib
            import json as _json
            try:
                _cat_blob = _json.dumps(dims_catalog, sort_keys=True, ensure_ascii=False)
                catalog_id = _hashlib.sha1(_cat_blob.encode("utf-8")).hexdigest()[:16]
            except Exception:
                catalog_id = None
            import uuid as _uuid
            try:
                _sid = scenario.get("id") or scenario.get("name") or "scenario"
                run_uid = f"{_sid}-{_uuid.uuid4().hex[:8]}"
            except Exception:
                run_uid = _uuid.uuid4().hex[:8]
            meta = {
                "omniagents": {
                    "run_type": "evaluation",
                    "scenario": {"id": scenario.get("id"), "name": scenario.get("name")},
                    "run": {"id": run_uid},
                    "synthetic": {"catalog_id": catalog_id, "catalog": {"dimensions": dims_catalog}, "selection": {"mapping": scenario.get("dimensions", {})}},
                    "config": {"agent_yaml": str(agent_yaml)},
                    **({"omniagents_version": _oa_version} if _oa_version else {}),
                }
            }
            gid = run_uid
            with agentio_trace(workflow_name, group_id=gid, metadata=meta):
                result = agent.run_streamed(msgs)
                async for _ in result.stream_events():
                    pass
        except Exception:
            result = agent.run_streamed(msgs)
            async for _ in result.stream_events():
                pass
    else:
        result = agent.run_streamed(msgs)
        async for _ in result.stream_events():
            pass

    session_id = getattr(agent, "_session_id", None)
    history = load_history(session_id) if session_id else []

    measures = scenario.get("measures") or []
    metadata = await _run_judges(Path(agent_yaml), eval_cfg, scenario, history)
    ctx = build_eval_context(history=history, tools=spec.available_tools, metadata=metadata)
    measures_out = run_measures(ctx, measures=measures) if measures else []

    return {
        "scenario": scenario.get("name", "scenario"),
        "prompt": prompt,
        "session_id": session_id,
        "final_assistant_text": final_assistant_text(history),
        "measures": measures_out,
        "history_len": len(history),
        "metadata": metadata,
    }


async def run_evaluation_async(compiled_yaml: Path, workflow_name: str | None = None, include_unmeasured: bool = False, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    cfg_path = Path(compiled_yaml).resolve()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    eval_cfg = (raw or {}).get("evaluation") or {}
    defaults = eval_cfg.get("defaults") or {}
    runs_per = int(defaults.get("runs_per_prompt", 1))
    scenarios = eval_cfg.get("scenarios") or []
    metrics_cfg = eval_cfg.get("metrics") or []
    fc_map = _failure_category_map(eval_cfg)

    original_scenarios_count = len(scenarios or [])
    if scenarios:
        for sc in scenarios:
            try:
                sc_measures = list(sc.get("measures") or [])
                sc_fc = sc.get("failure_categories") or []
                fc_names: List[str] = []
                for fc in sc_fc:
                    if isinstance(fc, str):
                        fc_names.append(fc)
                    elif isinstance(fc, dict):
                        nm = fc.get("name") or fc.get("key") or fc.get("id")
                        if nm:
                            fc_names.append(str(nm))
                for nm in fc_names:
                    for m in fc_map.get(nm, []):
                        sc_measures.append(m)
                if sc_measures:
                    seen = set()
                    dedup: List[str] = []
                    for m in sc_measures:
                        s = str(m)
                        if s not in seen:
                            seen.add(s)
                            dedup.append(s)
                    sc["measures"] = dedup
            except Exception:
                continue

    if not include_unmeasured:
        scenarios = [sc for sc in scenarios if (sc.get("measures") or [])]
    included_scenarios_count = len(scenarios)
    skipped_unmeasured_count = max(0, original_scenarios_count - included_scenarios_count)

    _load_local_measures(cfg_path)

    from omniagents.core.evaluation.registry import _MEASURES as _REG_MEASURES
    discovered_measures = sorted(list((_REG_MEASURES or {}).keys()))
    unknown_counts: Dict[str, int] = {}
    unknown_scenarios = 0
    for sc in scenarios:
        sc_ms = [str(m) for m in (sc.get("measures") or [])]
        unknown = [m for m in sc_ms if m not in _REG_MEASURES]
        if unknown:
            unknown_scenarios += 1
            for m in unknown:
                unknown_counts[m] = unknown_counts.get(m, 0) + 1

    all_runs: List[Dict[str, Any]] = []
    total_runs = len(scenarios) * max(1, runs_per)
    if progress_cb:
        try:
            progress_cb({"event": "start", "total": total_runs})
        except Exception:
            pass
    idx = 0
    for sc in scenarios:
        for _ in range(runs_per):
            r = await _run_scenario(str(cfg_path), sc, defaults, eval_cfg, workflow_name)
            r["scenario_config"] = sc
            all_runs.append(r)
            idx += 1
            if progress_cb:
                try:
                    measures = r.get("measures") or []
                    failed = 0
                    passed = 0
                    for m in measures:
                        pv = m.get("passed")
                        if pv is True:
                            passed += 1
                        elif pv is False:
                            failed += 1
                    progress_cb({
                        "event": "progress",
                        "index": idx,
                        "total": total_runs,
                        "scenario": r.get("scenario"),
                        "failed": failed,
                        "passed": passed,
                        "skipped": (len(measures) == 0),
                    })
                except Exception:
                    pass

    metrics = compute_metrics(all_runs, metrics_cfg)
    out = {
        "agent_yaml": str(cfg_path),
        "runs": all_runs,
        "metrics": metrics,
        "stats": {
            "total_scenarios": original_scenarios_count,
            "included_scenarios": included_scenarios_count,
            "skipped_unmeasured": skipped_unmeasured_count,
            "measures_discovered_count": len(discovered_measures),
            "measures_discovered": discovered_measures,
            "unknown_measures": unknown_counts,
            "unknown_scenarios": unknown_scenarios,
        },
    }
    return out


def run_evaluation(compiled_yaml: Path, tracing_cfg: Optional[Dict[str, Any]] = None, workflow_base_name: Optional[str] = None, include_unmeasured: bool = False, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    use_agentio = False
    name = None
    try:
        if tracing_cfg and str(tracing_cfg.get("provider", "")).lower() == "agentio":
            use_agentio = True
            import agentio
            from datetime import datetime
            base_name = workflow_base_name or tracing_cfg.get("name") or Path(compiled_yaml).stem
            start_suffix = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            name = f"{base_name}_scenarios_{start_suffix}"
            project = tracing_cfg.get("project") or "agent_eval"
            settings = tracing_cfg.get("settings") or {}
            agentio.init(project=str(project), name=name, replace_default=True, **settings)
    except Exception:
        use_agentio = False
        name = None
    if use_agentio:
        return asyncio.run(run_evaluation_async(compiled_yaml, workflow_name=name, include_unmeasured=include_unmeasured, progress_cb=progress_cb))
    return asyncio.run(run_evaluation_async(compiled_yaml, include_unmeasured=include_unmeasured, progress_cb=progress_cb))
