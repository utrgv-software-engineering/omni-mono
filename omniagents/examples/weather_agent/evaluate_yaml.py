from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import importlib.util
import sys
from typing import Any, Dict, List, Optional

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
from jinja2 import Environment

_DEBUG = False


def _deep_get(d: Any, path: str) -> Any:
    cur = d
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _apply_target(value: Optional[float], target: Dict[str, Any] | None) -> Optional[bool]:
    if value is None or not target:
        return None
    op = str(target.get("op", ">=")).strip()
    try:
        tgt = float(target.get("value"))
    except Exception:
        return None
    if op == ">=":
        return value >= tgt
    if op == ">":
        return value > tgt
    if op == "<=":
        return value <= tgt
    if op == "<":
        return value < tgt
    if op == "==":
        return abs(value - tgt) < 1e-12
    if op == "!=":
        return abs(value - tgt) >= 1e-12
    return None


def _compute_metrics(runs: List[Dict[str, Any]], metrics_cfg: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Index measure results per name
    per_measure: Dict[str, List[Dict[str, Any]]] = {}
    for r in runs:
        for m in r.get("measures", []) or []:
            per_measure.setdefault(m.get("name"), []).append(m)

    results: List[Dict[str, Any]] = []
    for mc in metrics_cfg or []:
        mtype = mc.get("type")
        name = mc.get("name") or mc.get("metric") or "metric"
        label = mc.get("label", name)
        res: Dict[str, Any] = {"name": name, "label": label, "type": mtype}

        if mtype == "pass_rate":
            src = mc.get("source_measure") or mc.get("source") or mc.get("measure")
            items = per_measure.get(src, [])
            total = len(items)
            passed = sum(1 for it in items if bool(it.get("passed")) is True)
            value = (passed / total) if total else None
            res.update({
                "source_measure": src,
                "value": value,
                "runs": total,
                "passed_runs": passed,
            })
            target = mc.get("target")
            if target is not None:
                res["target"] = target
                meets = _apply_target(value, target)
                res["meets_target"] = bool(meets) if meets is not None else False

        elif mtype == "rate":
            src = mc.get("from_measure") or mc.get("source_measure") or mc.get("measure")
            num_path = mc.get("numerator")
            den_path = mc.get("denominator")
            items = per_measure.get(src, [])
            num_sum = 0.0
            den_sum = 0.0
            for it in items:
                try:
                    n = _deep_get(it, num_path)
                    d = _deep_get(it, den_path)
                    if n is not None:
                        num_sum += float(n)
                    if d is not None:
                        den_sum += float(d)
                except Exception:
                    continue
            value = (num_sum / den_sum) if den_sum else None
            res.update({
                "source_measure": src,
                "value": value,
                "numerator_sum": num_sum,
                "denominator_sum": den_sum,
            })
            target = mc.get("target")
            if target is not None:
                res["target"] = target
                meets = _apply_target(value, target)
                res["meets_target"] = bool(meets) if meets is not None else False

        else:
            # Unknown metric type; echo config
            res.update({"value": None, "reason": f"unknown_metric_type:{mtype}"})

        results.append(res)

    return results


def _load_local_measures(cfg_path: Path) -> None:
    try:
        base_dir = cfg_path.parent
        measures_py = base_dir / "measures.py"
        if measures_py.is_file():
            mod_name = f"eval_measures_{base_dir.name}"
            spec = importlib.util.spec_from_file_location(mod_name, str(measures_py))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)  # type: ignore[attr-defined]
    except Exception:
        pass


def _render_template(template_str: str, **kwargs) -> str:
    try:
        env = Environment()
        tmpl = env.from_string(template_str)
        return str(tmpl.render(**kwargs))
    except Exception:
        return template_str


def _read_text_file(base: Path, rel_or_abs: str) -> str:
    p = Path(rel_or_abs)
    if not p.is_file():
        p = (base / rel_or_abs).resolve()
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except Exception:
        return ""


def _derive_judge_keys_from_measures(measures: List[str]) -> List[str]:
    keys: List[str] = []
    for m in measures or []:
        if isinstance(m, str) and m.endswith("_agent"):
            base = m[: -len("_agent")]
            if base:
                keys.append(base)
    # unique preserve order
    seen = set()
    uniq: List[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def _collect_judges_for_scenario(eval_cfg: Dict[str, Any], scenario: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    judges_cfg = eval_cfg.get("judges") or {}
    # Normalize list form for backward compat
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
        # Any other string: treat as single key plus derived
        selected_keys = list({*derived, str(wanted)})

    results: List[tuple[str, Dict[str, Any]]] = []
    for k in selected_keys:
        cfg = judges_cfg.get(k)
        if cfg:
            results.append((k, cfg))
    return results


async def _run_judges(cfg_path: Path, eval_cfg: Dict[str, Any], scenario: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    if _DEBUG:
        print(f"[dbg] judges: start scenario={scenario.get('name')}")
    metadata: Dict[str, Any] = {}
    judges = _collect_judges_for_scenario(eval_cfg, scenario)
    if not judges:
        if _DEBUG:
            print("[dbg] judges: none selected")
        return metadata

    for key, jcfg in judges:
        if _DEBUG:
            print(f"[dbg] judge: {key}")
        agent_cfg = (jcfg or {}).get("agent") or {}
        input_cfg = (jcfg or {}).get("input") or {}

        # Build input text
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
            template_str = _read_text_file(cfg_path.parent, str(template_file))
        if not template_str:
            # Fallback simple wrapper
            template_str = "{{ text }}"
        judge_prompt = _render_template(template_str, text=text, history=history, history_json=json.dumps(history))
        if _DEBUG:
            _jp = judge_prompt if len(judge_prompt) < 400 else judge_prompt[:400] + "..."
            print(f"[dbg] judge_prompt: {len(judge_prompt)} chars\n{_jp}")

        # Build judge agent
        try:
            from agents import Runner as AgentsRunner
            from omniagents.core.agents.specs import AgentSpec
            from omniagents.core.agents.builder import _default_build_agent
        except Exception:
            continue
        instructions = agent_cfg.get("instructions") or ""
        instr_file = agent_cfg.get("instructions_file")
        if (not instructions) and instr_file:
            instructions = _read_text_file(cfg_path.parent, str(instr_file))
        if not instructions:
            instructions = (
                "You are an evaluation agent.\n"
                "Return ONLY a JSON object appropriate for this judge."
            )
        model = agent_cfg.get("model", "gpt-4o-mini")
        output_type = agent_cfg.get("output_type", "dict")
        output_type_strict = bool(agent_cfg.get("output_type_strict", False))
        expect_dict = False
        if isinstance(output_type, str) and output_type.lower() == "dict":
            base_out_type = dict  # type: ignore[assignment]
            expect_dict = True
        elif isinstance(output_type, str) and output_type.lower() == "str":
            base_out_type = str  # type: ignore[assignment]
        elif output_type in (dict, str, list):
            base_out_type = output_type  # type: ignore[assignment]
            if output_type is dict:
                expect_dict = True
        else:
            base_out_type = dict  # sensible default
            expect_dict = True

        # If YAML provides an explicit JSON schema for the judge output, build a TypedDict dynamically
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
                base_out_type = TypedDict(td_name, ann)  # type: ignore[assignment]
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
                output_type=base_out_type,  # type: ignore[arg-type]
                output_type_strict=output_type_strict,
            )
            judge_agent = await _default_build_agent({}, None, spec, None)
            if _DEBUG:
                print(f"[dbg] running judge model={model} output_type={spec.output_type} strict={spec.output_type_strict}")
            judge_res = await AgentsRunner.run(judge_agent, judge_prompt, max_turns=int(agent_cfg.get("max_turns", 1)))
            val = getattr(judge_res, "final_output", None)
            # Parse JSON into dict if model wrapped response in fences
            if expect_dict and not isinstance(val, dict):
                s = str(val)
                try:
                    t = s.strip()
                    if t.startswith("```"):
                        t = t.strip('`')
                        if t.lower().startswith("json"):
                            t = t[4:]
                        t = t.strip()
                    # Fallback: extract JSON object substring
                    if not (t.startswith("{") and t.endswith("}")):
                        i = t.find("{")
                        j = t.rfind("}")
                        if i != -1 and j != -1 and j > i:
                            t = t[i:j+1]
                    val = json.loads(t)
                except Exception:
                    val = {"raw": s}
            metadata[key] = val
            if _DEBUG:
                print(f"[dbg] judge_result: {val}")
        except Exception:
            metadata[key] = {"error": "judge_failed"}
            if _DEBUG:
                print("[dbg] judge_error: judge_failed")

    return metadata


async def _run_scenario(agent_yaml: str, scenario: Dict[str, Any], defaults: Dict[str, Any], eval_cfg: Dict[str, Any]) -> Dict[str, Any]:
    if _DEBUG:
        print(f"[dbg] scenario: {scenario.get('name')} prompt={scenario.get('prompt')}")
    spec = load_agent_spec_from_yaml(agent_yaml)
    # Disable approvals for evaluation
    spec.use_safe_agent = False
    try:
        if "max_turns" in defaults:
            spec.max_turns = int(defaults.get("max_turns", spec.max_turns))
    except Exception:
        pass

    agent = await create_unified_agent(spec)

    prompt = scenario.get("prompt", "")

    msgs = [{"role": "user", "content": prompt}]
    result = agent.run_streamed(msgs)
    async for _ in result.stream_events():
        pass

    session_id = getattr(agent, "_session_id", None)
    history = load_history(session_id) if session_id else []
    if _DEBUG:
        print(f"[dbg] history_len={len(history)} session={session_id}")
        calls = parse_tool_calls(history)
        outs = parse_tool_outputs(history)
        links = link_outputs(calls, outs)
        names = [c.get("name") for c in calls]
        print(f"[dbg] tool_calls: {names}")
        for c in calls:
            nm = c.get("name")
            args = c.get("args")
            cid = c.get("call_id")
            args_s = json.dumps(args) if isinstance(args, dict) else str(args)
            if len(args_s) > 200:
                args_s = args_s[:200] + "..."
            print(f"[dbg]  call {nm} args={args_s}")
            if cid and cid in links and links[cid].get("output"):
                out_json = links[cid]["output"].get("output_json")
                out_s = json.dumps(out_json) if isinstance(out_json, dict) else str(out_json)
                if len(out_s) > 200:
                    out_s = out_s[:200] + "..."
                print(f"[dbg]   output {nm}={out_s}")
        last = final_assistant_text(history) or ""
        if last:
            lshow = last if len(last) < 400 else last[:400] + "..."
            print(f"[dbg] assistant: {lshow}")

    measures = scenario.get("measures") or []
    # Judges: optional judge agents provide metadata consumed by some measures
    metadata = await _run_judges(Path(agent_yaml), eval_cfg, scenario, history)
    ctx = build_eval_context(history=history, tools=spec.available_tools, metadata=metadata)
    measures_out = run_measures(ctx, measures=measures) if measures else []
    if _DEBUG:
        for m in measures_out:
            print(f"[dbg] measure {m.get('name')}: {json.dumps(m)[:500]}")

    return {
        "scenario": scenario.get("name", "scenario"),
        "prompt": prompt,
        "session_id": session_id,
        "measures": measures_out,
        "history_len": len(history),
        "metadata": metadata,
    }


async def _main_async(args) -> Dict[str, Any]:
    cfg_path = Path(args.config).resolve()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    eval_cfg = (raw or {}).get("evaluation") or {}

    defaults = eval_cfg.get("defaults") or {}
    runs_per = int(defaults.get("runs_per_prompt", 1))
    scenarios = eval_cfg.get("scenarios") or []
    metrics_cfg = eval_cfg.get("metrics") or []

    # Ensure local measures are registered (examples often define ./measures.py)
    _load_local_measures(cfg_path)
    if _DEBUG:
        print(f"[dbg] loaded config={cfg_path}")
        print(f"[dbg] scenarios={len(scenarios)} metrics={len(metrics_cfg)} runs_per_prompt={runs_per}")

    all_runs: List[Dict[str, Any]] = []
    for sc in scenarios:
        for _ in range(runs_per):
            r = await _run_scenario(str(cfg_path), sc, defaults, eval_cfg)
            r["scenario_config"] = sc
            all_runs.append(r)

    metrics = _compute_metrics(all_runs, metrics_cfg)
    if _DEBUG:
        for mt in metrics:
            print(f"[dbg] metric {mt.get('name')}: value={mt.get('value')} meets={mt.get('meets_target')}")

    out = {
        "agent_yaml": str(cfg_path),
        "runs": all_runs,
        "metrics": metrics,
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate agent using evaluation block in YAML")
    parser.add_argument("--config", "-c", required=True, help="Path to agent YAML")
    parser.add_argument("--output", "-o", default="", help="Write JSON to this path")
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug prints")
    args = parser.parse_args()

    # If no --output provided, try to read evaluation.output from YAML
    yaml_output: str = ""
    try:
        raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
        yaml_output = ((raw or {}).get("evaluation") or {}).get("output") or ""
    except Exception:
        yaml_output = ""

    global _DEBUG
    _DEBUG = bool(args.debug)

    data = asyncio.run(_main_async(args))
    text = json.dumps(data, indent=2)

    metrics = data.get("metrics", []) or []
    total = len(metrics)
    failed = 0
    print("\nMetrics:")
    for mt in metrics:
        name = mt.get("label") or mt.get("name") or "metric"
        value = mt.get("value")
        target = mt.get("target")
        has_target = target is not None
        meets = bool(mt.get("meets_target")) if has_target else None
        status = "PASS" if meets is True else ("FAIL" if has_target else "INFO")
        if isinstance(value, (int, float)):
            vstr = f"{value:.3f}"
        else:
            vstr = str(value)
        tstr = ""
        if has_target:
            op = str(target.get("op", ">=") )
            tstr = f" target {op} {target.get('value')}"
        print(f"[{status}] {name}: {vstr}{tstr}")
        if has_target and not meets:
            failed += 1
    print(f"Summary: {total} metrics, {failed} failing targets")

    cfg_dir = Path(args.config).resolve().parent
    out_arg = str(args.output or "").strip()
    if out_arg:
        final_out = Path(out_arg)
    else:
        yout = str(yaml_output or "").strip()
        final_out = Path(yout) if yout else Path("")

    if str(final_out):
        if not final_out.is_absolute():
            if not out_arg:
                final_out = (cfg_dir / final_out).resolve()
            else:
                final_out = (Path.cwd() / final_out).resolve()
        final_out.parent.mkdir(parents=True, exist_ok=True)
        final_out.write_text(text, encoding="utf-8")
    else:
        print(text)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
