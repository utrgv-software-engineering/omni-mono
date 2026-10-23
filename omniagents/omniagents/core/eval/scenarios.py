from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

_logger = logging.getLogger(__name__)

from omniagents.core.eval.pipeline import _catalog_with_labels
from omniagents.core.utils.jinja import render_template, read_text_file


def _read_yaml(p: Path) -> Dict[str, Any]:
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


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
    t = s.lower().strip()
    t = re.sub(r"[^a-z0-9\-\s_]+", "", t)
    t = re.sub(r"[\s_]+", "_", t)
    t = re.sub(r"_+", "_", t)
    if not t:
        t = "scenario"
    return t[:80]


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
            s = read_text_file(base_dir, str(f))
    return s


async def _build_generator_agent(
    base_dir: Path,
    agent_cfg: Dict[str, Any],
    dims_catalog: List[Dict[str, Any]] | None = None,
    raw_dims: List[Dict[str, Any]] | None = None,
):
    try:
        from omniagents.core.agents.specs import AgentSpec
        from omniagents.core.agents.builder import _default_build_agent
    except Exception as e:
        raise RuntimeError(f"omniagents not available: {e}")

    instructions = _get_instructions(base_dir, agent_cfg)
    model = agent_cfg.get("model", "gpt-4.1")
    if not instructions:
        instructions = "You generate realistic user prompts given a persona tuple. Return ONLY JSON with keys name and prompt."
    try:
        instructions = render_template(
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
    raw_out_schema = agent_cfg.get("output_schema")
    if isinstance(raw_out_schema, dict) and raw_out_schema:
        try:
            from typing import Optional

            # Prefer a Pydantic BaseModel so the SDK does not wrap in {"response": ...}
            try:
                from pydantic import BaseModel, create_model  # type: ignore
            except Exception:
                BaseModel = None  # type: ignore
                create_model = None  # type: ignore

            schema = raw_out_schema or {}
            schema_type = str(schema.get("type") or "object").lower()
            props = (
                (schema.get("properties") or {})
                if isinstance(schema.get("properties"), dict)
                else {}
            )
            required = set(schema.get("required") or [])

            if BaseModel and create_model and schema_type == "object":
                fields: Dict[str, tuple] = {}
                for k, v in props.items():
                    py_t = _map_json_type_to_py((v or {}).get("type"))
                    if k in required:
                        fields[k] = (py_t, ...)
                    else:
                        fields[k] = (Optional[py_t], None)
                # Fallback to a permissive single-field model if no properties
                if not fields:
                    fields = {"data": (dict, ...)}
                out_type = create_model("GeneratorOutput", **fields)  # type: ignore
            else:
                # If array or pydantic unavailable, fall back to plain str to parse manually
                # or dict for object without strict validation
                out_type = dict if schema_type == "object" else str
        except Exception:
            pass

    spec = AgentSpec(
        name=agent_cfg.get("name") or "PromptGenerator",
        welcome_text="",
        get_agent_instructions=_get_instr,
        build_agent=None,
        available_tools=[],
        model_name=model,
        output_type=out_type,
        output_type_strict=output_type_strict,
        project_slug=None,
        agent_slug="generator",
    )
    agent = await _default_build_agent({}, None, spec, None)
    return agent


async def _run_generation(
    agent, prompt: str, max_turns: int = 1
) -> List[Dict[str, Any]]:
    from agents import Runner as AgentsRunner

    # Synthetic prompt generator has no tools today, but anchor to cwd
    # for parity with judges/optimizers — keeps the strict-mode contract
    # satisfied if a future config adds tools to the generator agent.
    res = await AgentsRunner.run(
        agent,
        prompt,
        context={"workspace_root": os.getcwd()},
        max_turns=max_turns,
    )
    val = getattr(res, "final_output", None)
    try:
        if val is not None and hasattr(val, "model_dump"):
            val = val.model_dump()  # pydantic v2
        elif val is not None and hasattr(val, "dict"):
            # pydantic v1 fallback
            val = val.dict()
    except Exception:
        pass
    if isinstance(val, list):
        return [x for x in val if isinstance(x, dict)]
    if isinstance(val, dict):
        return [val]
    s = str(val)
    try:
        t = s.strip()
        if t.startswith("```"):
            t = t.strip("`")
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


def _enumerate_combos(
    dimensions: List[Dict[str, Any]], filters: Dict[str, List[str]] | None = None
) -> List[Dict[str, str]]:
    import itertools

    names = [str(d.get("name") or "dimension") for d in dimensions]
    value_lists: List[List[str]] = []
    for d in dimensions:
        vs = [str(v.get("name") or "value") for v in (d.get("values") or [])]
        if not vs:
            vs = ["value"]
        if filters:
            allowed = filters.get(str(d.get("name") or ""))
            if allowed:
                allowed_set = {str(v) for v in allowed}
                vs = [v for v in vs if v in allowed_set]
        if not vs:
            return []
        value_lists.append(vs)
    combos: List[Dict[str, str]] = []
    for prod in itertools.product(*value_lists):
        combos.append({k: v for k, v in zip(names, prod)})
    return combos


def _persona_from_mapping(
    dimensions: List[Dict[str, Any]], mapping: Dict[str, str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in dimensions:
        dname = str(d.get("name") or "dimension")
        vname = str(mapping.get(dname) or "value")
        vdesc = ""
        vexamples: List[str] = []
        for v in d.get("values") or []:
            if str(v.get("name")) == vname:
                vdesc = str(v.get("description") or "")
                vexamples = list(v.get("examples") or [])
                break
        out.append(
            {
                "dimension": dname,
                "value": vname,
                "description": vdesc,
                "examples": vexamples,
            }
        )
    return out


def _write_set(
    output: Path,
    scenarios: List[Dict[str, Any]],
    set_name: str | None,
    ts: str,
    cfg_path: Path,
    project_root: Path | None,
    workflow_name: str | None,
) -> Path:
    """Write a generated set's ``scenarios.yml`` and, beside it,
    ``manifest.json`` (paths in it relative to the project, since the set
    is committed)."""
    out_path = output.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    out_path.write_text(
        yaml.dump(
            {"scenarios": scenarios},
            sort_keys=False,
            Dumper=NoAliasDumper,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    source = Path(cfg_path).resolve()
    if project_root is not None and Path(project_root).resolve() in source.parents:
        source = source.relative_to(Path(project_root).resolve())
    manifest = {
        "timestamp": ts,
        "name": str(set_name) if set_name else out_path.parent.name,
        "counts": {"total": len(scenarios)},
        "source": str(source),
    }
    if workflow_name:
        manifest["workflow_generate"] = str(workflow_name)
    (out_path.parent / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return out_path


async def generate_scenarios(
    compiled_yaml: Path,
    *,
    output: Path,
    count: int | None = None,
    seed: int | None = None,
    set_name: str | None = None,
    workflow_name: str | None = None,
    progress_cb: Any | None = None,
    dimension_filters: Dict[str, List[str]] | None = None,
    eval_config: Any | None = None,
) -> Tuple[List[Dict[str, Any]], Path, str]:
    if eval_config is not None:
        cfg_path = eval_config.agent_path
        eval_cfg = eval_config.config
        _project_root = eval_config.project_root
    else:
        cfg_path = Path(compiled_yaml).resolve()
        raw = _read_yaml(cfg_path)
        eval_cfg = raw.get("evaluation") or {}
        _project_root = None
    gen_cfg = eval_cfg.get("synthetic_data_generation") or {}
    ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")

    dims = list(gen_cfg.get("dimensions") or [])
    target_count = int(
        count if count is not None else gen_cfg.get("target_scenario_count") or 10
    )
    generator_block = gen_cfg.get("generator") or {}
    agent_cfg = generator_block.get("agent") or {}
    input_cfg = generator_block.get("input") or {}
    if seed is not None:
        random.seed(int(seed))

    prompt_str = input_cfg.get("template") or ""
    if not prompt_str:
        prompt_file = input_cfg.get("template_file")
        if prompt_file:
            prompt_str = read_text_file(cfg_path.parent, str(prompt_file))

    dims_catalog = _catalog_with_labels(dims)
    agent = await _build_generator_agent(
        cfg_path.parent, agent_cfg, dims_catalog=dims_catalog, raw_dims=dims
    )

    scenarios: List[Dict[str, Any]] = []
    used_names: Dict[str, int] = {}
    filters_for_combos: Dict[str, List[str]] | None = None
    if dimension_filters:
        normalized_filters: Dict[str, List[str]] = {}
        for key, values in dimension_filters.items():
            if not values:
                continue
            allowed_values = [str(v).strip() for v in values if str(v).strip()]
            if allowed_values:
                normalized_filters[str(key)] = allowed_values
        if normalized_filters:
            filters_for_combos = normalized_filters
    combos = _enumerate_combos(dims, filters_for_combos)
    random.shuffle(combos)
    import hashlib as _hashlib, json as _json

    try:
        _cat_blob = _json.dumps(dims_catalog, sort_keys=True, ensure_ascii=False)
        catalog_id = _hashlib.sha1(_cat_blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        catalog_id = None
    if progress_cb:
        try:
            progress_cb({"event": "start", "target": target_count})
        except Exception:
            pass
    if target_count <= 0:
        out_path = _write_set(
            Path(output), [], set_name, ts, cfg_path, _project_root, workflow_name
        )
        if progress_cb:
            try:
                progress_cb({"event": "done", "count": 0})
            except Exception:
                pass
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
        rendered = render_template(prompt_str, **ctx)
        if workflow_name:
            try:
                from omniagents.core.tracing.local import trace as studio_trace

                try:
                    import omniagents as _oa

                    _oa_version = getattr(_oa, "__version__", None)
                except Exception:
                    _oa_version = None
                import uuid as _uuid

                run_uid = _uuid.uuid4().hex[:8]
                meta = {
                    "omniagents": {
                        "run_type": "scenario_generation",
                        "run": {"id": run_uid},
                        "synthetic": {
                            "catalog_id": catalog_id,
                            "catalog": {"dimensions": dims_catalog},
                            "selection": {
                                "mapping": {
                                    it["dimension"]: it["value"] for it in persona
                                }
                            },
                            "num_samples": per_combo,
                        },
                        "config": {"compiled_yaml": str(cfg_path)},
                        **({"omniagents_version": _oa_version} if _oa_version else {}),
                    }
                }
                gid = run_uid
                with studio_trace(workflow_name, group_id=gid, metadata=meta):
                    outs = await _run_generation(
                        agent, rendered, max_turns=int(input_cfg.get("max_turns", 1))
                    )
            except Exception:
                outs = await _run_generation(
                    agent, rendered, max_turns=int(input_cfg.get("max_turns", 1))
                )
        else:
            outs = await _run_generation(
                agent, rendered, max_turns=int(input_cfg.get("max_turns", 1))
            )
        for out in outs:
            name = str(out.get("name") or _slugify("_".join(selected_value_names)))
            prompt = str(out.get("prompt") or "")
            if not prompt:
                continue
            sid = _compute_scenario_id(prompt, mapping)
            if sid in seen_ids:
                continue
            uniq = _ensure_unique_name(_slugify(name), used_names)
            item = {
                "id": sid,
                "name": uniq,
                "prompt": prompt,
                "dimensions": dict(mapping),
                "measures": [],
            }
            scenarios.append(item)
            seen_ids.add(sid)
            if progress_cb:
                try:
                    progress_cb(
                        {
                            "event": "progress",
                            "current": len(scenarios),
                            "total": target_count,
                            "scenario": item,
                        }
                    )
                except Exception:
                    pass
            try:
                pr = prompt if isinstance(prompt, str) else str(prompt)
                if len(pr) > 160:
                    pr = pr[:160] + "..."
                print(f"[{len(scenarios)}/{target_count}] {uniq}: {pr}", flush=True)
            except Exception:
                pass
            if len(scenarios) >= target_count:
                break
        if len(scenarios) >= target_count:
            break

    out_path = _write_set(
        Path(output), scenarios, set_name, ts, cfg_path, _project_root, workflow_name
    )
    if progress_cb:
        try:
            progress_cb({"event": "done", "count": len(scenarios)})
        except Exception:
            pass
    return scenarios, out_path, ts
