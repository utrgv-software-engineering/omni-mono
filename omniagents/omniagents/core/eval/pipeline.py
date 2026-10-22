"""Project-level evaluation: config, measures and judges around the engine.

:func:`run_evaluation` / :func:`run_evaluation_async` are what ``omniagents
eval suite run``, ``eval scenarios run`` and Studio call: they resolve a
project's evaluation config, load its ``measures.py``, build the agent
target and hand everything to :func:`omniagents.core.eval.engine.run_suite`.
Judges (:func:`run_judges`) run inside the engine, once per run.
"""

from __future__ import annotations

import asyncio
import json
import importlib.util
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, cast

import yaml

from omniagents.core.evaluation import final_assistant_text, parse_tool_calls
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


def _first_user_text(history: List[Dict[str, Any]]) -> str:
    for item in history or []:
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    str(c.get("text") or "") for c in content if isinstance(c, dict)
                )
    return ""


async def run_judges(
    eval_cfg: Dict[str, Any],
    scenario: Dict[str, Any],
    history: List[Dict[str, Any]],
    base_dir: Path,
) -> Dict[str, Any]:
    """Run the judges ``scenario`` selects; returns ``{judge_key: output}``.

    Selection is :func:`_collect_judges_for_scenario`; each judge's relative
    ``instructions_file``/``template_file`` resolve against ``base_dir``
    (the evaluations directory, or the agent's directory without one).
    """
    metadata: Dict[str, Any] = {}
    for key, jcfg in _collect_judges_for_scenario(eval_cfg, scenario):
        metadata[key] = await run_judge(
            key, jcfg, scenario=scenario, history=history, base_dir=base_dir
        )
    return metadata


async def run_judge(
    key: str,
    jcfg: Dict[str, Any],
    *,
    scenario: Dict[str, Any],
    history: List[Dict[str, Any]],
    base_dir: Path,
) -> Any:
    """Run one judge config over a conversation and return its parsed output.

    The judge's ``input.template`` (Jinja) sees ``text`` (per
    ``input.source``), ``history``, ``history_json``, ``scenario``,
    ``user_input`` (the first user message) and ``tools_used`` (names of the
    tools called, in order). Returns ``{"error": "judge_failed"}`` when the
    judge cannot run.
    """
    agent_cfg = (jcfg or {}).get("agent") or {}
    input_cfg = (jcfg or {}).get("input") or {}
    source = str(input_cfg.get("source", "final_assistant_text")).strip()
    try:
        if source == "history":
            text = json.dumps(history)
        else:
            text = final_assistant_text(history) or ""
    except Exception:
        text = ""
    eval_base = Path(base_dir)
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
        user_input=_first_user_text(history),
        tools_used=[c.get("name") for c in parse_tool_calls(history) if c.get("name")],
    )

    try:
        from agents import Runner as AgentsRunner
        from omniagents.core.agents.specs import AgentSpec
        from omniagents.core.agents.builder import _default_build_agent
    except Exception:
        return {"error": "judge_failed"}
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
            td_name = f"Judge_{key.title().replace('-', '_').replace(' ', '')}_Output"
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
        return val
    except Exception:
        return {"error": "judge_failed"}


async def run_evaluation_async(
    compiled_yaml: Optional[Path] = None,
    workflow_name: str | None = None,
    include_unmeasured: bool = False,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    concurrency: int = 5,
    eval_dir: Path | None = None,
    project_root: Path | None = None,
    eval_config: Any | None = None,
    *,
    target: Any | None = None,
    eval_cfg: Optional[Dict[str, Any]] = None,
    environment: Any | None = None,
    include_history: bool = False,
) -> Dict[str, Any]:
    """Run a project's evaluation through the engine.

    The config comes from ``eval_config`` (an
    :class:`~omniagents.core.eval.loader.EvalConfig`), or from the
    ``evaluation`` block of ``compiled_yaml``; ``eval_cfg`` replaces its
    merged dict (the notebook adds judges and measures that way) and
    ``environment`` the declared environment. The project's ``measures.py``
    is imported first. ``target`` defaults to the
    project agent (:class:`~omniagents.core.eval.targets.ProjectAgentTarget`).
    """
    from omniagents.core.eval.engine import run_suite
    from omniagents.core.eval.targets import ProjectAgentTarget

    selection: Dict[str, Any] = {}
    agent_config: Optional[Dict[str, Any]] = None
    if eval_config is not None:
        cfg_path = Path(eval_config.agent_path)
        cfg = eval_config.config
        eval_dir = eval_dir or eval_config.eval_dir
        project_root = project_root or eval_config.project_root
        agent_config = eval_config.agent_config
        selection = {
            "select": getattr(eval_config, "select", None),
            "exclude": getattr(eval_config, "exclude", None),
            "split": getattr(eval_config, "split", None),
            "holdout": getattr(eval_config, "holdout_manifest", None),
        }
    else:
        if compiled_yaml is None:
            raise ValueError("run_evaluation_async needs compiled_yaml or eval_config")
        cfg_path = Path(compiled_yaml).resolve()
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cfg = raw.get("evaluation") or {}
        agent_config = {k: v for k, v in raw.items() if k != "evaluation"}

    _load_local_measures(cfg_path, project_root=project_root, eval_dir=eval_dir)
    if target is None:
        target = ProjectAgentTarget(
            cfg_path, project_root=project_root, agent_config=agent_config
        )
    return await run_suite(
        target,
        eval_cfg if eval_cfg is not None else cfg,
        eval_dir=eval_dir,
        project_root=project_root,
        environment=environment,
        include_unmeasured=include_unmeasured,
        progress_cb=progress_cb,
        concurrency=concurrency,
        workflow_name=workflow_name,
        include_history=include_history,
        selection=selection,
    )


def run_evaluation(
    compiled_yaml: Path,
    tracing_cfg: Optional[Dict[str, Any]] = None,
    workflow_base_name: Optional[str] = None,
    include_unmeasured: bool = False,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    eval_config: Any | None = None,
) -> Dict[str, Any]:
    """Synchronous :func:`run_evaluation_async` that also initializes studio tracing."""
    if eval_config is not None:
        cfg_path = Path(eval_config.agent_path)
        tracing_cfg = tracing_cfg or eval_config.tracing_config
    else:
        cfg_path = Path(compiled_yaml).resolve()

    workflow_name = None
    try:
        if tracing_cfg and str(tracing_cfg.get("provider", "")).lower() == "studio":
            import omniagents.studio.tracing as studio_tracing
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
