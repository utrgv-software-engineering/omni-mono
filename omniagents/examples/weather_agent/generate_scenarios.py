from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple
import itertools
import math
from datetime import datetime
import re

import yaml
from jinja2 import Environment
from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.session.history_db import load_history
from omniagents.core.evaluation import final_assistant_text
import agentio


def _read_yaml(p: Path) -> Dict[str, Any]:
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _read_text_file(base: Path, rel_or_abs: str) -> str:
    p = Path(rel_or_abs)
    if not p.is_file():
        p = (base / rel_or_abs).resolve()
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except Exception:
        return ""


def _render_template(template_str: str, **kwargs) -> str:
    try:
        env = Environment()
        def _tojson(value, indent=None, sort_keys=False):
            return json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=bool(sort_keys))
        env.filters["tojson"] = _tojson
        tmpl = env.from_string(template_str)
        return str(tmpl.render(**kwargs))
    except Exception:
        return template_str


def _choose_value(dimension: Dict[str, Any]) -> Dict[str, Any]:
    vals = list(dimension.get("values") or [])
    if not vals:
        return {"name": "unknown", "description": ""}
    return random.choice(vals)


def _build_persona_tuple(dimensions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in dimensions:
        chosen = _choose_value(d)
        out.append(
            {
                "dimension": d.get("name") or "dimension",
                "value": chosen.get("name") or "value",
                "description": chosen.get("description") or "",
                "examples": chosen.get("examples") or [],
            }
        )
    return out


def _summarize_persona(selected: List[Dict[str, Any]]) -> str:
    parts = []
    for it in selected:
        dim = str(it.get("dimension", ""))
        val = str(it.get("value", ""))
        desc = str(it.get("description", ""))
        if desc:
            parts.append(f"{dim}={val} — {desc}")
        else:
            parts.append(f"{dim}={val}")
    return "\n".join(parts)


def _slugify(s: str) -> str:
    import re
    t = s.lower().strip()
    t = re.sub(r"[^a-z0-9\-\s_]+", "", t)
    t = re.sub(r"[\s_]+", "_", t)
    t = re.sub(r"_+", "_", t)
    if not t:
        t = "scenario"
    return t[:80]


def _derive_label(raw: str) -> str:
    try:
        t = str(raw or "").strip()
        t = t.replace("_", " ")
        if t.lower() == "non existent" or t.lower() == "non_existent":
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
            values.append({
                **v,
                "name": v_name,
                "label": v_label,
            })
        out.append({
            **{k: v for k, v in d.items() if k not in ("values",)},
            "name": name,
            "label": label,
            "values": values,
        })
    return out


def _ensure_unique_name(name: str, used: Dict[str, int]) -> str:
    base = name
    if base not in used:
        used[base] = 1
        return base
    idx = used[base]
    while True:
        candidate = f"{base}_{idx}"
        if candidate not in used:
            used[base] = idx + 1
            used[candidate] = 1
            return candidate
        idx += 1


def _compute_scenario_id(prompt: str, mapping: Dict[str, Any] | None) -> str:
    fingerprint = {"prompt": prompt}
    if mapping:
        # Ensure deterministic ordering
        fingerprint["dimensions"] = {k: mapping[k] for k in sorted(mapping.keys())}
    blob = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _map_json_type_to_py(t: str):
    t = (t or "").lower()
    return {
        "string": str,
        "boolean": bool,
        "bool": bool,
        "number": float,
        "float": float,
        "integer": int,
        "int": int,
        "object": dict,
        "array": list,
    }.get(t, str)


def _get_instructions(base_dir: Path, agent_cfg: Dict[str, Any]) -> str:
    s = str(agent_cfg.get("instructions") or "")
    if not s:
        f = agent_cfg.get("instructions_file")
        if f:
            s = _read_text_file(base_dir, str(f))
    return s


async def _build_generator_agent(base_dir: Path, agent_cfg: Dict[str, Any], dims_catalog: List[Dict[str, Any]] | None = None, raw_dims: List[Dict[str, Any]] | None = None):
    try:
        from agents import Runner as AgentsRunner
        from omniagents.core.agents.specs import AgentSpec
        from omniagents.core.agents.builder import _default_build_agent
    except Exception as e:
        raise RuntimeError(f"omniagents not available: {e}")

    instructions = _get_instructions(base_dir, agent_cfg)
    model = agent_cfg.get("model", "gpt-4o-mini")
    if not instructions:
        instructions = (
            "You generate realistic user prompts given a persona tuple. "
            "Return ONLY JSON with keys name and prompt."
        )
    # Allow instructions to reference the dimension catalog via Jinja
    try:
        instructions = _render_template(
            instructions,
            dimensions=raw_dims or [],
            dimensions_catalog=dims_catalog or [],
        )
    except Exception:
        pass

    def _get_instr() -> str:
        return instructions

    output_type = agent_cfg.get("output_type")
    output_type_strict = bool(agent_cfg.get("output_type_strict", False))
    out_type = dict
    if isinstance(output_type, type):
        out_type = output_type
    elif isinstance(output_type, str):
        if output_type.lower() == "dict":
            out_type = dict
        elif output_type.lower() == "str":
            out_type = str
        elif output_type.lower() == "list":
            out_type = list
    if isinstance(agent_cfg.get("output_schema"), dict):
        try:
            from typing_extensions import TypedDict
            schema = agent_cfg.get("output_schema") or {}
            props = schema.get("properties", {}) or {}
            ann = {k: _map_json_type_to_py((v or {}).get("type")) for k, v in props.items()}
            out_type = TypedDict("GeneratorOutput", ann)  # type: ignore[assignment]
        except Exception:
            pass

    spec = AgentSpec(
        name=agent_cfg.get("name") or "PromptGenerator",
        header_title=agent_cfg.get("name") or "PromptGenerator",
        page_title=agent_cfg.get("name") or "PromptGenerator",
        welcome_text="",
        client_storage_prefix="generator.",
        get_agent_instructions=_get_instr,
        build_agent=None,
        available_tools=[],
        model_name=model,
        output_type=out_type,
        output_type_strict=output_type_strict,
    )
    agent = await _default_build_agent({}, None, spec, None)
    return agent


async def _run_generation(agent, prompt: str, max_turns: int = 1) -> List[Dict[str, Any]]:
    from agents import Runner as AgentsRunner
    res = await AgentsRunner.run(agent, prompt, max_turns=max_turns)
    val = getattr(res, "final_output", None)
    if isinstance(val, list):
        return [x for x in val if isinstance(x, dict)]
    if isinstance(val, dict):
        return [val]
    s = str(val)
    try:
        t = s.strip()
        if t.startswith("```"):
            t = t.strip('`')
            if t.lower().startswith("json"):
                t = t[4:]
            t = t.strip()
        data = json.loads(t)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]
        return []
    except Exception:
        return []


def _enumerate_combos(dimensions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    names = [str(d.get("name") or "dimension") for d in dimensions]
    value_lists: List[List[str]] = []
    for d in dimensions:
        vs = [str(v.get("name") or "value") for v in (d.get("values") or [])]
        if not vs:
            vs = ["value"]
        value_lists.append(vs)
    combos: List[Dict[str, str]] = []
    for prod in itertools.product(*value_lists):
        combos.append({k: v for k, v in zip(names, prod)})
    return combos


def _persona_from_mapping(dimensions: List[Dict[str, Any]], mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in dimensions:
        dname = str(d.get("name") or "dimension")
        vname = str(mapping.get(dname) or "value")
        vdesc = ""
        vexamples: List[str] = []
        for v in (d.get("values") or []):
            if str(v.get("name")) == vname:
                vdesc = str(v.get("description") or "")
                vexamples = list(v.get("examples") or [])
                break
        out.append({
            "dimension": dname,
            "value": vname,
            "description": vdesc,
            "examples": vexamples,
        })
    return out


async def _main_async(args) -> Tuple[List[Dict[str, Any]], Path, str]:
    cfg_path = Path(args.config).resolve()
    raw = _read_yaml(cfg_path)
    eval_cfg = (raw.get("evaluation") or {})
    gen_cfg = (eval_cfg.get("synthetic_data_generation") or {})
    ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")

    dims = list(gen_cfg.get("dimensions") or [])
    if not dims:
        raise SystemExit("No evaluation.synthetic_data_generation.dimensions found")

    target_count = int(args.count or gen_cfg.get("target_scenario_count") or 10)
    generator_block = gen_cfg.get("generator") or {}
    if not generator_block:
        raise SystemExit("No evaluation.synthetic_data_generation.generator block found")
    agent_cfg = (generator_block.get("agent") or {})
    input_cfg = (generator_block.get("input") or {})
    seed = None if args.seed is None else int(args.seed)
    if seed is not None:
        random.seed(seed)

    prompt_str = input_cfg.get("template") or ""
    if not prompt_str:
        prompt_file = input_cfg.get("template_file")
        if prompt_file:
            prompt_str = _read_text_file(cfg_path.parent, str(prompt_file))

    dims_catalog = _catalog_with_labels(dims)
    agent = await _build_generator_agent(cfg_path.parent, agent_cfg, dims_catalog=dims_catalog, raw_dims=dims)

    scenarios: List[Dict[str, Any]] = []
    used_names: Dict[str, int] = {}
    combos = _enumerate_combos(dims)
    if not combos:
        raise SystemExit("No dimension value combinations found")
    random.shuffle(combos)
    if target_count <= 0:
        out_path = Path(args.output or "artifacts/eval/generated_scenarios.yaml")
        if not out_path.is_absolute():
            base = Path(getattr(args, "project_root", cfg_path.parent))
            out_path = (base / out_path).resolve()
        if out_path.suffix:
            out_path = out_path.with_name(f"{out_path.stem}_{ts}{out_path.suffix}")
        else:
            out_path = out_path.with_name(f"{out_path.name}_{ts}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"scenarios": []}
        class NoAliasDumper(yaml.SafeDumper):
            def ignore_aliases(self, data):
                return True
        out_path.write_text(yaml.dump(data, sort_keys=False, Dumper=NoAliasDumper, allow_unicode=True), encoding="utf-8")
        return [], out_path, ts
    per_combo = max(1, math.ceil(target_count / len(combos)))
    seen_ids: set[str] = set()
    for mapping in combos:
        persona = _persona_from_mapping(dims, mapping)
        selected_value_names = [f"{it['dimension']}={it['value']}" for it in persona]
        ctx = {
            "selected_values": persona,
            "selected_value_names": selected_value_names,
            "selected_values_with_descriptions": _summarize_persona(persona),
            "dimensions": dims,
            "dimensions_catalog": dims_catalog,
            "selected_mapping": {it["dimension"]: it["value"] for it in persona},
            "num_samples": per_combo,
        }
        rendered = _render_template(prompt_str, **ctx)
        outs = await _run_generation(agent, rendered, max_turns=int(input_cfg.get("max_turns", 1)))
        for out in outs:
            name = str(out.get("name") or _slugify("_".join(selected_value_names)))
            prompt = str(out.get("prompt") or "")
            if not prompt:
                continue
            sid = _compute_scenario_id(prompt, mapping)
            if sid in seen_ids:
                continue
            uniq = _ensure_unique_name(_slugify(name), used_names)
            item = {"id": sid, "name": uniq, "prompt": prompt, "dimensions": dict(mapping), "measures": []}
            scenarios.append(item)
            seen_ids.add(sid)
            if len(scenarios) >= target_count:
                break
        if len(scenarios) >= target_count:
            break

    out_path = Path(args.output or "artifacts/eval/generated_scenarios.yaml")
    if not out_path.is_absolute():
        base = Path(getattr(args, "project_root", cfg_path.parent))
        out_path = (base / out_path).resolve()
    if out_path.suffix:
        out_path = out_path.with_name(f"{out_path.stem}_{ts}{out_path.suffix}")
    else:
        out_path = out_path.with_name(f"{out_path.name}_{ts}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"scenarios": scenarios}
    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True
    out_path.write_text(yaml.dump(data, sort_keys=False, Dumper=NoAliasDumper, allow_unicode=True), encoding="utf-8")
    return scenarios, out_path, ts


async def _run_scenarios_against_agent(agent_yaml: Path, scenarios: List[Dict[str, Any]], max_turns: int | None = None, include_history: bool = False, workflow_name: str | None = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    cfg = _read_yaml(agent_yaml)
    defaults = ((cfg.get("evaluation") or {}).get("defaults") or {})
    raw_dims_catalog = list(((cfg.get("evaluation") or {}).get("synthetic_data_generation") or {}).get("dimensions") or [])
    dims_catalog = _catalog_with_labels(raw_dims_catalog)
    import hashlib, json as _json
    try:
        _cat_blob = _json.dumps(dims_catalog, sort_keys=True, ensure_ascii=False)
        catalog_id = hashlib.sha1(_cat_blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        catalog_id = None
    for sc in scenarios:
        spec = load_agent_spec_from_yaml(str(agent_yaml))
        spec.use_safe_agent = False
        try:
            if max_turns is not None:
                spec.max_turns = int(max_turns)
            elif "max_turns" in defaults:
                spec.max_turns = int(defaults.get("max_turns", spec.max_turns))
        except Exception:
            pass
        agent = await create_unified_agent(spec)
        prompt = sc.get("prompt", "")
        msgs = [{"role": "user", "content": prompt}]
        if workflow_name:
            try:
                from agentio import trace as agentio_trace
                try:
                    import omniagents as _oa
                    _oa_version = getattr(_oa, "__version__", None)
                except Exception:
                    _oa_version = None
                import uuid as _uuid
                try:
                    _sid = sc.get("id") or sc.get("name") or "scenario"
                    run_uid = f"{_sid}-{_uuid.uuid4().hex[:8]}"
                except Exception:
                    run_uid = _uuid.uuid4().hex[:8]
                meta = {
                    "omniagents": {
                        "run_type": "synthetic_generation",
                        "scenario": {
                            "id": sc.get("id"),
                            "name": sc.get("name"),
                        },
                        "run": {"id": run_uid},
                        "synthetic": {
                            "catalog_id": catalog_id,
                            "catalog": {"dimensions": dims_catalog},
                            "selection": {"mapping": sc.get("dimensions", {})},
                        },
                        "config": {
                            "agent_yaml": str(agent_yaml),
                        },
                        **({"omniagents_version": _oa_version} if _oa_version else {}),
                    }
                }
                gid = run_uid
                with agentio_trace(workflow_name, group_id=gid, metadata=meta):
                    run = agent.run_streamed(msgs)
                    async for _ in run.stream_events():
                        pass
            except Exception:
                run = agent.run_streamed(msgs)
                async for _ in run.stream_events():
                    pass
        else:
            run = agent.run_streamed(msgs)
            async for _ in run.stream_events():
                pass
        session_id = getattr(agent, "_session_id", None)
        history = load_history(session_id) if session_id else []
        results.append({
            "scenario_id": sc.get("id"),
            "scenario_name": sc.get("name"),
            "prompt": prompt,
            "session_id": session_id,
            "final_assistant_text": final_assistant_text(history),
            "history_len": len(history),
            **({"history": history} if include_history else {}),
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate scenarios from synthetic_data_generation block")
    parser.add_argument("--project", "-p", required=True, help="Path to project.yml for dbt-style layout")
    parser.add_argument("--select", default="", help="Selector to include scenarios (dbt-style)")
    parser.add_argument("--exclude", default="", help="Selector to exclude scenarios (dbt-style)")
    parser.add_argument("--output", "-o", default="artifacts/eval/generated_scenarios.yaml", help="Where to write scenarios YAML")
    parser.add_argument("--count", type=int, default=None, help="Override number of scenarios")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--include-dimensions", action="store_true", help="Include chosen dimensions per scenario")
    parser.add_argument("--run", action="store_true", help="Run generated scenarios against the agent")
    parser.add_argument("--run-output", default="artifacts/eval/generated_runs.json", help="Where to write run results JSON")
    parser.add_argument("--run-max-turns", type=int, default=None, help="Max turns when running scenarios")
    parser.add_argument("--include-history", action="store_true", help="Include full history in run output")
    parser.add_argument("--run-from", default="", help="Run scenarios from an existing YAML instead of generating")
    args = parser.parse_args()

    compiled_cfg_path: Path
    try:
        from tools.project_compile import compile_project
        compiled_cfg_path, _graph = compile_project(str(args.project), select=str(args.select or ""), exclude=str(args.exclude or ""))
        setattr(args, "config", str(compiled_cfg_path))
        proj_root = Path(args.project).resolve()
        if proj_root.is_file():
            proj_root = proj_root.parent
        setattr(args, "project_root", str(proj_root))
    except Exception as e:
        raise SystemExit(f"Failed to compile project: {e}")

    scenarios: List[Dict[str, Any]]
    out_path: Path
    gen_ts: str | None = None

    if args.run_from:
        in_path = Path(args.run_from)
        if not in_path.is_absolute():
            base = Path(getattr(args, "project_root", Path(args.project).resolve()))
            if base.is_file():
                base = base.parent
            in_path = (base / in_path).resolve()
        y = yaml.safe_load(in_path.read_text(encoding="utf-8")) or {}
        scenarios = list((y.get("scenarios") or []))
        # Ensure IDs exist
        for sc in scenarios:
            if not sc.get("id"):
                mapping = sc.get("dimensions") if isinstance(sc.get("dimensions"), dict) else None
                sc["id"] = _compute_scenario_id(str(sc.get("prompt", "")), mapping)
        out_path = in_path
        print(f"Loaded {len(scenarios)} scenarios from {out_path}")
    else:
        scenarios, out_path, gen_ts = asyncio.run(_main_async(args))
        print(f"Wrote {len(scenarios)} scenarios to {out_path}")

    if args.run:
        try:
            cfg = _read_yaml(Path(args.config).resolve())
            tracing = (cfg.get("tracing") or {})
            provider = str(tracing.get("provider") or "agentio").lower()
            if provider == "agentio":
                settings = tracing.get("settings") or {}
                resume = settings.get("resume", "never")
                project = tracing.get("project") or "scenario_runs"
                base_name = tracing.get("name") or Path(args.config).stem
                def _extract_ts_from_filename(p: Path) -> str | None:
                    m = re.search(r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})", p.name)
                    return m.group(1) if m else None
                ts_for_workflow = gen_ts or _extract_ts_from_filename(out_path) or datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                name = f"{base_name}_scenarios_{ts_for_workflow}"
                agentio.init(project=str(project), name=name, resume=str(resume), replace_default=True)
                runs = asyncio.run(_run_scenarios_against_agent(Path(args.config).resolve(), scenarios, max_turns=args.run_max_turns, include_history=bool(args.include_history), workflow_name=name))
            else:
                runs = asyncio.run(_run_scenarios_against_agent(Path(args.config).resolve(), scenarios, max_turns=args.run_max_turns, include_history=bool(args.include_history)))
        except Exception:
            runs = asyncio.run(_run_scenarios_against_agent(Path(args.config).resolve(), scenarios, max_turns=args.run_max_turns, include_history=bool(args.include_history)))
        run_out = Path(args.run_output)
        if not run_out.is_absolute():
            base = Path(getattr(args, "project_root", Path(args.project).resolve()))
            if base.is_file():
                base = base.parent
            run_out = (base / run_out).resolve()
        run_out.parent.mkdir(parents=True, exist_ok=True)
        Path(run_out).write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")
        print(f"Ran {len(runs)} scenarios; wrote results to {run_out}")


if __name__ == "__main__":
    main()
