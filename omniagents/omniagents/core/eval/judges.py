from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

import yaml

from omniagents.core.utils.jinja import render_template
from omniagents.core.tools.discovery import discover_functions_in_dir


async def evaluate_split(
    compiled_yaml: Path,
    split_path: Path,
    judge_key: str,
    judge_max_turns: int = 1,
    run_max_turns: int | None = None,
    show: int = 20,
    replay_mode: str = "replay",
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    eval_config: Any | None = None,
) -> Dict[str, Any]:

    if eval_config is not None:
        cfg = eval_config.agent_config
        eval_cfg = eval_config.config
        _eval_dir = eval_config.eval_dir
    else:
        cfg = yaml.safe_load(Path(compiled_yaml).read_text(encoding="utf-8")) or {}
        eval_cfg = cfg.get("evaluation") or {}
        _eval_dir = None
    agent_meta = {
        "name": cfg.get("name"),
        "label": cfg.get("label"),
        "description": cfg.get("description"),
        "tools": cfg.get("tools") or [],
    }
    judges_cfg = eval_cfg.get("judges") or {}
    jcfg = judges_cfg.get(str(judge_key))
    if not isinstance(jcfg, dict):
        raise SystemExit(f"Judge not found: {judge_key}")

    data = yaml.safe_load(Path(split_path).read_text(encoding="utf-8")) or {}
    scenarios = [x for x in (data.get("scenarios") or []) if isinstance(x, dict)]
    if not scenarios:
        raise SystemExit("No scenarios in split")

    from omniagents.core.eval.judge_validation import (
        TestSplitSpent,
        check_test_unspent,
        confusion,
        is_test_split,
        judge_version,
        record_validation,
    )
    from omniagents.core.eval.layout import EvalLayout

    # A judge dataset's test split is measured once per version of the judge;
    # that measurement is the judge's validation.
    layout = (
        EvalLayout(eval_config.eval_dir, eval_config.project_root)
        if eval_config is not None
        else None
    )
    on_test = layout is not None and is_test_split(layout, Path(split_path))
    if on_test:
        try:
            check_test_unspent(
                layout, str(judge_key), judge_version(jcfg), Path(split_path)
            )
        except TestSplitSpent as exc:
            raise SystemExit(str(exc)) from exc

    category_hint: Optional[str] = None
    try:
        category_hint = Path(split_path).parent.name
    except Exception:
        category_hint = None
    if not category_hint:
        try:
            for sc in scenarios:
                c = sc.get("target_category")
                if c:
                    category_hint = str(c)
                    break
        except Exception:
            category_hint = None

    from omniagents.core.eval.reviews import dataset_conversations

    # What is judged: each example's answer, as the person who labelled it
    # saw it.
    conversations = dataset_conversations(scenarios)
    expected_ids = [str(sc.get("id") or sc.get("name")) for sc in scenarios]
    missing = [sid for sid in expected_ids if sid not in conversations]
    if missing:
        raise SystemExit(
            f"{len(missing)} example(s) in the split carry no reviewed answer "
            f"(e.g. {missing[0]}); build the judge dataset from reviews"
        )
    by_id = {sid: conversations[sid] for sid in expected_ids}

    from omniagents.core.agents.specs import AgentSpec
    from omniagents.core.agents.builder import _default_build_agent

    # The provider the judge model resolved to (set when the agent is built).
    judge_model: Dict[str, Any] = {}

    async def _build_judge_agent(agent_cfg: Dict[str, Any], base_dir: Path):
        def _map_js_type(t: Optional[str]):
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

        instructions = agent_cfg.get("instructions") or ""
        instr_file = agent_cfg.get("instructions_file")
        if not instructions and instr_file:
            try:
                p = Path(instr_file)
                if not p.is_absolute():
                    p = (base_dir / p).resolve()
                instructions = p.read_text(encoding="utf-8") if p.is_file() else ""
            except Exception:
                instructions = ""
        if not instructions:
            instructions = "You are an evaluation agent. Return ONLY JSON."
        else:
            try:
                instructions = render_template(
                    instructions,
                    agent=agent_meta,
                    agent_name=agent_meta.get("name"),
                    agent_label=agent_meta.get("label"),
                    agent_description=agent_meta.get("description"),
                    agent_tools=agent_meta.get("tools"),
                    category=category_hint,
                )
            except Exception:
                pass
        from omniagents.core.eval.loader import judge_examples_text

        examples = judge_examples_text(jcfg)
        if examples:
            instructions = f"{instructions}\n\n{examples}"
        model = agent_cfg.get("model", "gpt-4.1")
        output_type = agent_cfg.get("output_type", dict)
        output_type_strict = bool(agent_cfg.get("output_type_strict", False))
        out_type: Any = dict
        if isinstance(output_type, type):
            out_type = output_type
        elif isinstance(output_type, str):
            out_type = (
                dict
                if output_type.lower() == "dict"
                else (str if output_type.lower() == "str" else list)
            )
        if isinstance(agent_cfg.get("output_schema"), dict):
            try:
                from typing_extensions import TypedDict

                props = (agent_cfg.get("output_schema") or {}).get(
                    "properties", {}
                ) or {}
                ann = {k: _map_js_type((v or {}).get("type")) for k, v in props.items()}
                out_type = TypedDict("JudgeOutput", ann)  # type: ignore
            except Exception:
                pass
        # Discover tools relative to compiled target dir
        discovered = {}
        try:
            t1 = (base_dir / "tools").resolve()
            if t1.is_dir():
                discovered.update(discover_functions_in_dir(str(t1)))
        except Exception:
            pass
        try:
            t2 = (base_dir / "project_tools").resolve()
            if t2.is_dir():
                discovered.update(discover_functions_in_dir(str(t2)))
        except Exception:
            pass
        tools_list = []
        activated = agent_cfg.get("tools", [])
        if isinstance(activated, str) and activated.strip().lower() in {"*", "all"}:
            tools_list = list(discovered.values())
        elif isinstance(activated, list):
            for nm in activated:
                if nm in discovered:
                    tools_list.append(discovered[nm])

        def _get_instr() -> str:
            return instructions

        from omniagents.core.eval.models import (
            merged_model_settings,
            resolve_eval_model,
        )

        resolved = resolve_eval_model(model)
        judge_model["provider"] = resolved.provider if resolved else None
        nm = agent_cfg.get("name") or "Judge"
        spec = AgentSpec(
            name=nm,
            welcome_text="",
            get_agent_instructions=_get_instr,
            build_agent=None,
            available_tools=tools_list,
            model_name=resolved.name if resolved else model,
            model_settings=merged_model_settings(None, resolved),
            output_type=out_type,
            output_type_strict=output_type_strict,
            project_slug=None,
            agent_slug="judge",
        )
        agent = await _default_build_agent({}, None, spec, None)
        return agent

    async def _run_judge(agent, prompt: str, max_turns: int = 1):
        from omniagents.core.eval.models import run_to_final_output

        # Judges in this split-evaluation flow are post-hoc — there's
        # no live scenario workspace at this point. Anchor to cwd so
        # any builtin tool the judge gains in the future resolves
        # against the eval host's working directory rather than
        # raising WorkspaceRootRequiredError under strict mode.
        from agents.exceptions import ModelBehaviorError

        try:
            return await run_to_final_output(
                agent,
                prompt,
                provider=judge_model.get("provider"),
                context={"workspace_root": os.getcwd()},
                max_turns=max_turns,
            )
        except ModelBehaviorError as exc:
            # A reply that is not the verdict schema is no verdict: counted
            # as unanswered, not as Pass or Fail, and the check goes on.
            return {"error": "invalid_judge_output", "detail": str(exc)}

    def _predict_label_from_judge_output(val: Any, category: str) -> bool | None:
        try:
            if isinstance(val, dict):
                ans = val.get("answer")
                if isinstance(ans, str):
                    a = ans.strip().lower()
                    if a in ("pass", "fail"):
                        return True if a == "fail" else False
            if isinstance(val, dict) and "should_decline" in val:
                return not bool(val.get("should_decline"))
        except Exception:
            pass
        return None

    base_dir = Path(_eval_dir) if _eval_dir else Path(compiled_yaml).resolve().parent
    judge_agent = await _build_judge_agent(jcfg.get("agent") or {}, base_dir)
    input_cfg = jcfg.get("input") or {}
    template = input_cfg.get("template") or ""
    if not template:
        tf = input_cfg.get("template_file")
        if tf:
            try:
                p = Path(tf)
                if not p.is_absolute():
                    p = (base_dir / p).resolve()
                template = p.read_text(encoding="utf-8") if p.is_file() else ""
            except Exception:
                template = ""
    if not template:
        template = "{{ text }}"
    source = str(input_cfg.get("source", "final_assistant_text")).strip()
    max_turns = int(judge_max_turns or 1)

    # Pre-compute line numbers for each scenario id by scanning the split YAML text
    scen_line_by_id: Dict[str, int] = {}
    try:
        split_lines = Path(split_path).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(split_lines, start=1):
            s = line.strip()
            if s.startswith("- id: "):
                try:
                    sid = s.split(":", 1)[1].strip()
                except Exception:
                    sid = None
                if sid:
                    scen_line_by_id[sid] = i
    except Exception:
        scen_line_by_id = {}

    results: List[Dict[str, Any]] = []
    total = len(scenarios)
    if progress_cb:
        try:
            progress_cb({"event": "start", "total": total})
        except Exception:
            pass
    idx = 0
    pairs: List[tuple] = []
    for sc in scenarios:
        sid = str(sc.get("id") or sc.get("name"))
        label = bool(sc.get("label"))
        run = by_id[sid]
        if source == "history":
            text = json.dumps(run.get("history") or [])
        else:
            text = run.get("final_assistant_text") or ""
        prompt = render_template(
            template,
            text=text,
            history=(run.get("history") or []),
            history_json=json.dumps(run.get("history") or []),
            agent=agent_meta,
            agent_name=agent_meta.get("name"),
            agent_label=agent_meta.get("label"),
            agent_description=agent_meta.get("description"),
            agent_tools=agent_meta.get("tools"),
        )
        from omniagents.core.eval.pipeline import normalize_fail_reason

        out = normalize_fail_reason(
            await _run_judge(judge_agent, prompt, max_turns=max_turns), jcfg
        )
        pred = _predict_label_from_judge_output(out, sc.get("target_category") or "")
        agree = None if pred is None else (pred == label)
        # A label marks the failure present; Pass is the positive class.
        pairs.append((not label, None if pred is None else not pred))
        results.append(
            {
                "id": sid,
                "name": sc.get("name"),
                "label": label,
                "pred": pred,
                "agree": agree,
                "prompt": (run.get("prompt") or ""),
                "final_assistant_text": (run.get("final_assistant_text") or ""),
                "judge_output": out,
                "split_file": str(split_path),
                "split_line": int(scen_line_by_id.get(str(sid)) or 0),
            }
        )
        idx += 1
        if progress_cb:
            try:
                progress_cb(
                    {
                        "event": "progress",
                        "index": idx,
                        "total": total,
                        "scenario_id": sid,
                        "agree": agree,
                        "pred": pred,
                        "label": label,
                    }
                )
            except Exception:
                pass

    counts = confusion(pairs)
    validation = (
        record_validation(
            layout, str(judge_key), jcfg, Path(split_path), counts, source="judge run"
        )
        if on_test
        else None
    )
    return {
        "split": str(split_path),
        "judge": str(judge_key),
        "counts": counts,
        "tpr": counts["tpr"],
        "tnr": counts["tnr"],
        "validation": validation,
        "results": results,
    }
