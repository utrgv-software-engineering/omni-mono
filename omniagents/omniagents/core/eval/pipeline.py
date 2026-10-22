from __future__ import annotations

import asyncio
import json
import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, cast

import yaml

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.session.history_db import load_history
from omniagents.core.evaluation import (
    build_eval_context,
    run_measures,
    final_assistant_text,
)
from omniagents.core.eval.metrics import compute_metrics
from omniagents.core.eval.telemetry import (
    consume_stream_events,
    scenario_timer,
    build_telemetry,
)
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


def _load_local_measures(
    cfg_path: Path,
    project_root: Path | None = None,
    eval_dir: Path | None = None,
) -> None:
    try:
        base_dir = cfg_path.parent
        candidates = [
            base_dir / "measures.py",
            base_dir / "evaluation" / "measures.py",
            base_dir / "eval" / "measures.py",
        ]

        prj_root = project_root

        # Fall back to upward search when project_root not provided
        if prj_root is None:
            cur = base_dir.resolve()
            while True:
                if (cur / "project.yml").is_file():
                    prj_root = cur
                    break
                if cur == cur.parent:
                    break
                cur = cur.parent

        if eval_dir is not None:
            candidates.append(Path(eval_dir).resolve() / "measures.py")
        elif prj_root:
            import yaml as _yaml

            proj = (
                _yaml.safe_load((prj_root / "project.yml").read_text(encoding="utf-8"))
                or {}
            )
            paths = proj.get("paths") or {}
            eval_paths = paths.get("evaluations", "evaluations")
            eval_dirs_list = (
                eval_paths if isinstance(eval_paths, list) else [eval_paths]
            )
            for ed in eval_dirs_list:
                ep = Path(ed)
                if not ep.is_absolute():
                    ep = (prj_root / ep).resolve()
                candidates.append(ep / "measures.py")
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


def _derive_label(raw: Optional[str]) -> str:
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
        for v in d.get("values") or []:
            v_name = v.get("name")
            v_label = v.get("label") or _derive_label(v_name)
            values.append({**v, "name": v_name, "label": v_label})
        out.append(
            {
                **{k: v for k, v in d.items() if k not in ("values",)},
                "name": name,
                "label": label,
                "values": values,
            }
        )
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


def _collect_judges_for_scenario(
    eval_cfg: Dict[str, Any], scenario: Dict[str, Any]
) -> List[tuple[str, Dict[str, Any]]]:
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

    from omniagents.core.eval.measure_tiers import measure_names

    measures = measure_names(scenario.get("measures"))
    derived = _derive_judge_keys_from_measures(measures)

    wanted = scenario.get("judges", "auto")
    selected_keys: List[str] = []
    seen: set[str] = set()

    def _add_from_iter(values) -> None:
        for value in values:
            if value is None:
                continue
            key = str(value)
            if key and key not in seen:
                seen.add(key)
                selected_keys.append(key)

    if (
        wanted is None
        or (isinstance(wanted, str) and wanted.lower() == "none")
        or wanted is False
    ):
        selected_keys = []
    elif (
        wanted is True
        or (isinstance(wanted, str) and wanted.lower() == "auto")
        or wanted == ""
    ):
        _add_from_iter(derived)
    elif isinstance(wanted, list):
        _add_from_iter(derived)
        _add_from_iter([str(x) for x in wanted])
    else:
        _add_from_iter(derived)
        _add_from_iter([wanted])

    results: List[tuple[str, Dict[str, Any]]] = []
    for k in selected_keys:
        cfg = judges_cfg.get(k)
        if cfg:
            results.append((k, cfg))
    return results


async def _run_judges(
    cfg_path: Path,
    eval_cfg: Dict[str, Any],
    scenario: Dict[str, Any],
    history: List[Dict[str, Any]],
    eval_dir: Path | None = None,
) -> Dict[str, Any]:
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

        # Use eval_dir when provided; fall back to cfg_path.parent
        eval_base = Path(eval_dir) if eval_dir else cfg_path.parent

        template_str = input_cfg.get("template") or ""
        template_file = input_cfg.get("template_file")
        if (not template_str) and template_file:
            template_str = read_text_file(eval_base, str(template_file))
        if not template_str:
            template_str = "{{ text }}"
        judge_prompt = render_template(
            template_str,
            text=text,
            history=history,
            history_json=json.dumps(history),
            scenario=scenario,
        )

        try:
            from agents import Runner as AgentsRunner
            from omniagents.core.agents.specs import AgentSpec
            from omniagents.core.agents.builder import _default_build_agent
        except Exception:
            continue
        instructions = agent_cfg.get("instructions") or ""
        instr_file = agent_cfg.get("instructions_file")
        if (not instructions) and instr_file:
            instructions = read_text_file(eval_base, str(instr_file))
        if not instructions:
            instructions = "You are an evaluation agent. Return ONLY a JSON object appropriate for this judge."
        model = agent_cfg.get("model", "gpt-4.1")
        output_type = agent_cfg.get("output_type", "dict")
        output_type_strict = bool(agent_cfg.get("output_type_strict", False))
        expect_dict = False
        base_out_type: Any
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
                from typing import Literal
                from typing_extensions import TypedDict

                schema = agent_cfg.get("output_schema") or {}
                props = schema.get("properties", {}) or {}

                _BASE_TYPE_MAP = {
                    "string": str,
                    "boolean": bool,
                    "bool": bool,
                    "number": float,
                    "float": float,
                    "integer": int,
                    "int": int,
                    "object": dict,
                    "array": list,
                }

                def _map_prop(prop_schema):
                    """Map a JSON Schema property to a Python type annotation,
                    respecting ``enum`` when present."""
                    prop_schema = prop_schema or {}
                    enum_values = prop_schema.get("enum")
                    if enum_values and isinstance(enum_values, list):
                        # Use Literal to constrain the value set
                        return Literal[tuple(enum_values)]
                    base = (prop_schema.get("type") or "").lower()
                    return _BASE_TYPE_MAP.get(base, str)

                ann = {k: _map_prop(v) for k, v in props.items()}
                td_name = (
                    f"Judge_{key.title().replace('-', '_').replace(' ', '')}_Output"
                )
                base_out_type = TypedDict(td_name, ann)  # type: ignore
            except Exception:
                pass

        try:
            get_instr = lambda: instructions
            spec = AgentSpec(
                name=agent_cfg.get("name") or f"Judge:{key}",
                welcome_text="",
                get_agent_instructions=get_instr,
                build_agent=None,
                available_tools=[],
                model_name=model,
                output_type=base_out_type,
                output_type_strict=output_type_strict,
                project_slug=None,
                agent_slug=f"judge_{key}",
            )
            judge_agent = await _default_build_agent({}, None, spec, None)
            # Judges may be configured with tools (judges.py allows it via
            # available_tools); pass workspace_root so any builtin tool
            # they gain works under strict mode. Anchored to cwd because
            # this judge runs post-scenario without a live workspace.
            judge_res = await AgentsRunner.run(
                cast(Any, judge_agent),
                judge_prompt,
                context={"workspace_root": os.getcwd()},
                max_turns=int(agent_cfg.get("max_turns", 1)),
            )
            val = getattr(judge_res, "final_output", None)
            if expect_dict and not isinstance(val, dict):
                s = str(val)
                try:
                    t = s.strip()
                    if t.startswith("```"):
                        t = t.strip("`")
                        if t.lower().startswith("json"):
                            t = t[4:]
                        t = t.strip()
                    if not (t.startswith("{") and t.endswith("}")):
                        i = t.find("{")
                        j = t.rfind("}")
                        if i != -1 and j != -1 and j > i:
                            t = t[i : j + 1]
                    val = json.loads(t)
                except Exception:
                    val = {"raw": s}
            metadata[key] = val
        except Exception:
            metadata[key] = {"error": "judge_failed"}
    return metadata


async def _run_scenario(
    agent_yaml: str,
    scenario: Dict[str, Any],
    defaults: Dict[str, Any],
    eval_cfg: Dict[str, Any],
    workflow_name: str | None = None,
    eval_dir: Path | None = None,
    project_root: Path | None = None,
) -> Dict[str, Any]:
    from .multi_turn import _apply_scenario_variables, _build_registries_for_eval

    _tool_reg, _sf_reg, _cap_reg = _build_registries_for_eval(
        Path(agent_yaml), project_root=project_root
    )
    spec = load_agent_spec_from_yaml(
        agent_yaml,
        tool_registry=_tool_reg if _tool_reg else None,
        server_functions_registry=_sf_reg if _sf_reg else None,
        capability_registry=_cap_reg if _cap_reg else None,
    )
    spec.use_safe_agent = False
    # Per-scenario session variables (e.g. additional_instructions).
    # Mirrors the launcher's start_run(variables=...) dispatch.
    _apply_scenario_variables(spec, scenario)
    # Anchor builtin filesystem tools to the eval invocation's cwd. Without
    # this, start_run raises WorkspaceRootRequiredError under strict mode
    # (no default_host_workspace_root is set on the eval-side AgentService).
    # Mirrors the judges path in _run_judges.
    if not isinstance(spec.context, dict):
        spec.context = {} if spec.context is None else {"_original": spec.context}
    spec.context.setdefault("workspace_root", os.getcwd())
    try:
        if "max_turns" in defaults:
            spec.max_turns = int(defaults["max_turns"])
    except Exception:
        pass
    agent = await create_unified_agent(spec)
    prompt = scenario.get("prompt", "")
    msgs = [{"role": "user", "content": prompt}]
    # Trace group_id under which this scenario's spans land, when studio
    # tracing is configured. Persisted into the result so consumers (Studio
    # "View Trace" link) can navigate from a result row to its trace.
    trace_group_id: Optional[str] = None
    try:
        stream_telemetry = {
            "usage": {
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
        }

        with scenario_timer() as elapsed_fn:
            if workflow_name:
                try:
                    from omniagents.core.tracing.local import trace as studio_trace

                    try:
                        import omniagents as _oa

                        _oa_version = getattr(_oa, "__version__", None)
                    except Exception:
                        _oa_version = None
                    try:
                        cfg = (
                            yaml.safe_load(Path(agent_yaml).read_text(encoding="utf-8"))
                            or {}
                        )
                        raw_dims = list(
                            (
                                (
                                    (cfg.get("evaluation") or {}).get(
                                        "synthetic_data_generation"
                                    )
                                    or {}
                                ).get("dimensions")
                            )
                            or []
                        )
                        dims_catalog = _catalog_with_labels(raw_dims)
                    except Exception:
                        dims_catalog = []
                    import hashlib as _hashlib
                    import json as _json

                    try:
                        _cat_blob = _json.dumps(
                            dims_catalog, sort_keys=True, ensure_ascii=False
                        )
                        catalog_id = _hashlib.sha1(
                            _cat_blob.encode("utf-8")
                        ).hexdigest()[:16]
                    except Exception:
                        catalog_id = None
                    import uuid as _uuid

                    try:
                        _sid = scenario.get("id") or scenario.get("name") or "scenario"
                        run_uid = f"{_sid}-{_uuid.uuid4().hex[:8]}"
                    except Exception:
                        run_uid = _uuid.uuid4().hex[:8]
                    # Include the scenario's expected outcome (e.g.,
                    # ``expect.doctor_answer``) so the trace is self-contained
                    # for review. The Studio review UI surfaces this so a
                    # reviewer reading a failing trace can see the reference
                    # answer side-by-side with the agent's response without
                    # leaving the page.
                    expected_block = scenario.get("expect")
                    meta = {
                        "omniagents": {
                            "run_type": "evaluation",
                            "scenario": {
                                "id": scenario.get("id"),
                                "name": scenario.get("name"),
                            },
                            "run": {"id": run_uid},
                            **({"expected": expected_block} if expected_block else {}),
                            "synthetic": {
                                "catalog_id": catalog_id,
                                "catalog": {"dimensions": dims_catalog},
                                "selection": {
                                    "mapping": scenario.get("dimensions", {})
                                },
                            },
                            "config": {"agent_yaml": str(agent_yaml)},
                            **(
                                {"omniagents_version": _oa_version}
                                if _oa_version
                                else {}
                            ),
                        }
                    }
                    gid = run_uid
                    trace_group_id = gid
                    with studio_trace(workflow_name, group_id=gid, metadata=meta):
                        result = agent.run_streamed(msgs)
                        stream_telemetry = await consume_stream_events(
                            result.stream_events()
                        )
                except Exception:
                    result = agent.run_streamed(msgs)
                    stream_telemetry = await consume_stream_events(
                        result.stream_events()
                    )
            else:
                result = agent.run_streamed(msgs)
                stream_telemetry = await consume_stream_events(result.stream_events())

        run_error_payload = None
        get_last_run_end = getattr(agent, "get_last_run_end", None)
        if callable(get_last_run_end):
            try:
                candidate = get_last_run_end()
            except Exception:
                candidate = None
            if isinstance(candidate, dict) and candidate.get("error"):
                run_error_payload = candidate

        session_id = getattr(agent, "_session_id", None)
        history = (
            load_history(
                session_id,
                project_slug=spec.project_slug,
                agent_slug=spec.agent_slug,
            )
            if session_id
            else []
        )

        from omniagents.core.eval.measure_tiers import parse_measures, tag_results

        measures, tier_by_name = parse_measures(scenario.get("measures"))
        judge_metadata = await _run_judges(
            Path(agent_yaml), eval_cfg, scenario, history, eval_dir=eval_dir
        )
        # Two separate metadata dicts (see core/eval/multi_turn.py for the same split):
        # - ctx_metadata includes the scenario so EvalContext.scenario / .expect resolve.
        # - result_metadata is what we return in the run dict; it stays as just judge
        #   results (callers check this dict's shape, e.g. tests/integration/core/eval/).
        ctx_metadata: Dict[str, Any] = {
            "scenario": scenario,
            **judge_metadata,
        }
        result_metadata: Dict[str, Any] = dict(judge_metadata)
        if run_error_payload is not None:
            ctx_metadata.setdefault("agent_error", run_error_payload)
            result_metadata.setdefault("agent_error", run_error_payload)
        ctx = build_eval_context(
            history=history, tools=spec.available_tools, metadata=ctx_metadata
        )
        measures_out = run_measures(ctx, measures=measures) if measures else []
        tag_results(measures_out, tier_by_name)
        if run_error_payload is not None:
            err_info = run_error_payload.get("error")
            if isinstance(err_info, dict):
                reason = (
                    err_info.get("message")
                    or err_info.get("type")
                    or "agent_run_failed"
                )
                details = err_info
            else:
                reason = str(err_info)
                details = {"message": str(err_info)}
            measures_out.append(
                {
                    "name": "_agent_error",
                    "passed": False,
                    "reason": reason,
                    "details": details,
                    "tier": "outcome",
                }
            )

        telemetry = build_telemetry(
            elapsed_fn=elapsed_fn,
            history=history,
            stream_result=stream_telemetry,
        )

        from .transcripts import export_transcript, resolve_project_root

        transcript_path = export_transcript(
            history,
            scenario,
            project_root=project_root or resolve_project_root(agent_yaml),
            session_id=session_id,
        )

        final_text = final_assistant_text(history)
        return {
            "scenario": scenario.get("name", "scenario"),
            "prompt": prompt,
            "session_id": session_id,
            "trace_group_id": trace_group_id,
            "final_assistant_text": final_text,
            # A run with no final answer is a different kind of failure from a
            # wrong one; it is recorded so aggregation can report it apart.
            "answered": bool((final_text or "").strip()),
            "measures": measures_out,
            "history_len": len(history),
            "metadata": result_metadata,
            "transcript_path": transcript_path,
            **telemetry,
        }
    finally:
        if hasattr(agent, "cleanup"):
            await agent.cleanup()


async def run_evaluation_async(
    compiled_yaml: Path,
    workflow_name: str | None = None,
    include_unmeasured: bool = False,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    concurrency: int = 5,
    eval_dir: Path | None = None,
    project_root: Path | None = None,
    eval_config: Any | None = None,
) -> Dict[str, Any]:
    # When an EvalConfig is provided, use it directly
    if eval_config is not None:
        cfg_path = eval_config.agent_path
        eval_cfg = eval_config.config
        eval_dir = eval_dir or eval_config.eval_dir
        project_root = project_root or eval_config.project_root
    else:
        cfg_path = Path(compiled_yaml).resolve()
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        eval_cfg = (raw or {}).get("evaluation") or {}
    defaults = eval_cfg.get("defaults") or {}
    runs_per = int(defaults.get("runs_per_prompt", 1))
    slice_by = defaults.get("slice_by")
    scenarios = eval_cfg.get("scenarios") or []
    metrics_cfg = eval_cfg.get("metrics") or []
    fc_map = _failure_category_map(eval_cfg)

    from omniagents.core.eval.measure_tiers import (
        DEFAULT_TIER,
        measure_names,
        parse_measures,
    )

    original_scenarios_count = len(scenarios or [])
    if scenarios:
        for sc in scenarios:
            try:
                raw = sc.get("measures")
                flat, tier_by_name = parse_measures(raw)
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
                        sname = str(m)
                        if sname not in tier_by_name:
                            flat.append(sname)
                            tier_by_name[sname] = DEFAULT_TIER
                if flat:
                    if isinstance(raw, dict):
                        # Preserve the tier-keyed shape so downstream
                        # tier-aware reads still work.
                        rebuilt: Dict[str, List[str]] = {}
                        for n in flat:
                            t = tier_by_name.get(n, DEFAULT_TIER)
                            rebuilt.setdefault(t, []).append(n)
                        sc["measures"] = rebuilt
                    else:
                        sc["measures"] = flat
            except Exception:
                continue

    if not include_unmeasured:
        scenarios = [sc for sc in scenarios if measure_names(sc.get("measures"))]
    included_scenarios_count = len(scenarios)
    skipped_unmeasured_count = max(
        0, original_scenarios_count - included_scenarios_count
    )

    _load_local_measures(cfg_path, project_root=project_root, eval_dir=eval_dir)

    from omniagents.core.evaluation.registry import _MEASURES as _REG_MEASURES

    discovered_measures = sorted(list((_REG_MEASURES or {}).keys()))
    unknown_counts: Dict[str, int] = {}
    unknown_scenarios = 0
    for sc in scenarios:
        sc_ms = measure_names(sc.get("measures"))
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

    sem = asyncio.Semaphore(concurrency)
    completed_count = 0

    async def _worker(sc, trial):
        nonlocal completed_count
        async with sem:
            r = await _run_scenario(
                str(cfg_path),
                sc,
                defaults,
                eval_cfg,
                workflow_name,
                eval_dir=eval_dir,
                project_root=project_root,
            )
            r["scenario_config"] = sc
            # Which repeat of this scenario the run is (0-based) and how many
            # there are, so repeats stay distinguishable after flattening.
            r["trial"] = trial
            r["repeats"] = runs_per

            # Update progress safely
            completed_count += 1
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
                    progress_cb(
                        {
                            "event": "progress",
                            "index": completed_count,
                            "total": total_runs,
                            "scenario": r.get("scenario"),
                            "failed": failed,
                            "passed": passed,
                            "skipped": (len(measures) == 0),
                        }
                    )
                except Exception:
                    pass
            return r

    tasks = []
    for sc in scenarios:
        for trial in range(runs_per):
            tasks.append(asyncio.create_task(_worker(sc, trial)))

    if tasks:
        all_runs = await asyncio.gather(*tasks)
    else:
        all_runs = []

    if slice_by:
        metrics = compute_metrics(all_runs, metrics_cfg, default_slice_by=slice_by)
    else:
        metrics = compute_metrics(all_runs, metrics_cfg)
    out: Dict[str, Any] = {
        "agent_yaml": str(cfg_path),
        "runs": all_runs,
        "metrics": metrics,
        "stats": {
            "repeats": runs_per,
            "answered": sum(1 for r in all_runs if r.get("answered", True)),
            "total_scenarios": original_scenarios_count,
            "included_scenarios": included_scenarios_count,
            "skipped_unmeasured": skipped_unmeasured_count,
            "measures_discovered_count": len(discovered_measures),
            "measures_discovered": discovered_measures,
            "unknown_measures": unknown_counts,
            "unknown_scenarios": unknown_scenarios,
        },
    }
    if eval_config is not None:
        try:
            from omniagents.core.eval.manifest import build_run_manifest

            out["manifest"] = build_run_manifest(eval_config, repeats=runs_per)
        except Exception:
            out["manifest"] = None
    return out


def _needs_extended_pipeline(eval_cfg: Dict[str, Any]) -> bool:
    """Check if evaluation needs the extended pipeline.

    Extended pipeline is needed for:
    - Environment configuration
    - Synthetic scenarios (type: synthetic)
    - Scripted multi-turn scenarios (prompts: [...], max_continuations,
      until_sentinel, continuation_prompt)
    """
    if eval_cfg.get("environment"):
        return True

    for sc in eval_cfg.get("scenarios") or []:
        # Synthetic scenario
        if sc.get("type") == "synthetic":
            return True
        # Scripted multi-turn — fixed list or any of the loop knobs
        if "prompts" in sc and isinstance(sc["prompts"], list):
            return True
        if (
            sc.get("max_continuations")
            or sc.get("until_sentinel")
            or sc.get("until_sql")
            or sc.get("continuation_prompt")
        ):
            return True

    return False


def run_evaluation(
    compiled_yaml: Path,
    tracing_cfg: Optional[Dict[str, Any]] = None,
    workflow_base_name: Optional[str] = None,
    include_unmeasured: bool = False,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    eval_config: Any | None = None,
) -> Dict[str, Any]:
    # When an EvalConfig is provided, use it directly
    if eval_config is not None:
        cfg_path = eval_config.agent_path
        eval_cfg = eval_config.config
        tracing_cfg = tracing_cfg or eval_config.tracing_config
    else:
        cfg_path = Path(compiled_yaml).resolve()
        raw_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        eval_cfg = raw_cfg.get("evaluation") or {}

    # Initialize tracing if configured (applies to all paths)
    workflow_name = None
    try:
        if tracing_cfg and str(tracing_cfg.get("provider", "")).lower() == "studio":
            from omniagents.core.tracing import local as studio_tracing
            from datetime import datetime

            base_name = workflow_base_name or tracing_cfg.get("name") or cfg_path.stem
            start_suffix = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            workflow_name = f"{base_name}_scenarios_{start_suffix}"
            project = tracing_cfg.get("project") or "agent_eval"
            settings = tracing_cfg.get("settings") or {}
            studio_tracing.init(
                project=str(project),
                name=workflow_name,
                replace_default=True,
                **settings,
            )
    except Exception:
        workflow_name = None

    concurrency = int((tracing_cfg or {}).get("concurrency") or 5)

    # Use extended pipeline for environment, synthetic, or multi-turn scenarios
    if _needs_extended_pipeline(eval_cfg):
        from omniagents.core.eval.multi_turn import (
            run_evaluation_with_environment_async,
        )

        return asyncio.run(
            run_evaluation_with_environment_async(
                compiled_yaml,
                workflow_name=workflow_name,
                include_unmeasured=include_unmeasured,
                progress_cb=progress_cb,
                concurrency=concurrency,
                eval_config=eval_config,
            )
        )

    return asyncio.run(
        run_evaluation_async(
            compiled_yaml,
            workflow_name=workflow_name,
            include_unmeasured=include_unmeasured,
            progress_cb=progress_cb,
            concurrency=concurrency,
            eval_config=eval_config,
        )
    )
