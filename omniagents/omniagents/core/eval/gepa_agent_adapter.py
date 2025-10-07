from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.session.history_db import load_history
from omniagents.core.evaluation import (
    build_eval_context,
    run_measures,
    final_assistant_text,
)
from omniagents.core.eval.pipeline import _failure_category_map, _load_local_measures, _run_judges
from omniagents.core.utils.jinja import render_template

try:
    from gepa.strategies.instruction_proposal import InstructionProposalSignature
except Exception:
    InstructionProposalSignature = None  # type: ignore


@dataclass
class AgentDataInst:
    id: str
    label: Optional[bool] = None
    name: Optional[str] = None
    prompt: Optional[str] = None


class OmniAgentsAgentAdapter:
    def __init__(
        self,
        compiled_yaml: Path,
        category: str,
        *,
        agent_max_turns: Optional[int] = None,
        score_mode: str = "fraction",
        optimizer_key: Optional[str] = None,
        workflow_name: Optional[str] = None,
    ) -> None:
        self.compiled_yaml = Path(compiled_yaml).resolve()
        self.category = str(category)
        self.agent_max_turns = int(agent_max_turns) if agent_max_turns is not None else None
        self.score_mode = score_mode
        self.optimizer_key = optimizer_key
        self.workflow_name = workflow_name

        cfg = yaml.safe_load(self.compiled_yaml.read_text(encoding="utf-8")) or {}
        eval_cfg = (cfg.get("evaluation") or {})
        cmap = _failure_category_map(eval_cfg)
        self.category_measures: List[str] = list(cmap.get(self.category, []) or [])
        self._agent_meta = {
            "name": cfg.get("name") or None,
            "label": cfg.get("label") or None,
            "description": cfg.get("description") or None,
        }

        _load_local_measures(self.compiled_yaml)

        self._optimizer_cfg: Optional[Dict[str, Any]] = None
        self._optimizer_agent = None
        if self.optimizer_key:
            try:
                self._optimizer_cfg = ((cfg.get("evaluation") or {}).get("optimizers") or {}).get(self.optimizer_key) or None
            except Exception:
                self._optimizer_cfg = None
        # Always provide proposal generation; if no optimizer configured,
        # fall back to built-in agent prompts to avoid generic reflection.
        self.propose_new_texts = self._propose_new_texts  # type: ignore

    async def _run_once(self, instructions: str, prompt: str) -> Dict[str, Any]:
        spec = load_agent_spec_from_yaml(str(self.compiled_yaml))
        spec.use_safe_agent = False
        if self.agent_max_turns is not None:
            try:
                spec.max_turns = int(self.agent_max_turns)
            except Exception:
                pass

        def _get_instr() -> str:
            return instructions

        spec.get_agent_instructions = _get_instr

        agent = await create_unified_agent(spec)
        msgs = [{"role": "user", "content": prompt}]

        run = agent.run_streamed(msgs)
        async for _ in run.stream_events():
            pass

        session_id = getattr(agent, "_session_id", None)
        history = load_history(session_id) if session_id else []
        text = final_assistant_text(history)

        cfg = yaml.safe_load(self.compiled_yaml.read_text(encoding="utf-8")) or {}
        eval_cfg = (cfg.get("evaluation") or {})
        scen = {"measures": list(self.category_measures)}
        try:
            metadata = await _run_judges(self.compiled_yaml, eval_cfg, scen, history)
        except Exception:
            metadata = {}
        ctx = build_eval_context(history=history, tools=spec.available_tools, metadata=metadata)
        measures = run_measures(ctx, measures=self.category_measures) if self.category_measures else []

        return {
            "history": history,
            "final_assistant_text": text,
            "measures": measures,
        }

    def _score_from_measures(self, measures: List[Dict[str, Any]]) -> float:
        if not measures:
            return 0.0
        total = len(measures)
        passed = 0
        for m in measures:
            pv = m.get("passed")
            if pv is True:
                passed += 1
        if self.score_mode == "all_or_nothing":
            return 1.0 if passed == total else 0.0
        return passed / total

    def _find_classification_measure(self, measures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        try:
            cands = [m for m in (measures or []) if isinstance(m.get("name"), str) and str(m.get("name")).endswith("_judge_agent")]
            if cands:
                return cands[0]
        except Exception:
            pass
        return None

    def evaluate(
        self,
        batch: List[Dict[str, Any]],
        candidate: Dict[str, str],
        capture_traces: bool = False,
    ):
        instructions = str(candidate.get("instructions") or "You are a helpful assistant. Return ONLY JSON when asked.")

        async def _run_batch() -> List[Dict[str, Any]]:
            outs: List[Dict[str, Any]] = []
            # Determine if labels are present for classification feedback/scoring
            has_labels = any(("label" in it) for it in (batch or []))
            # Pre-compute counts for potential balanced scoring (not used yet; reserved)
            pos_count = sum(1 for it in (batch or []) if bool(it.get("label"))) if has_labels else 0
            neg_count = sum(1 for it in (batch or []) if ("label" in it and not bool(it.get("label")))) if has_labels else 0
            for item in batch:
                prompt = str(item.get("prompt") or "")
                if not prompt:
                    outs.append({"score": 0.0, "error": "missing_prompt"})
                    continue
                try:
                    if self.workflow_name:
                        try:
                            from agentio import trace as agentio_trace  # type: ignore
                            import uuid as _uuid
                            run_uid = _uuid.uuid4().hex[:8]
                            meta = {
                                "omniagents": {
                                    "run_type": "optimizer",
                                    "scope": "agent",
                                    "category": self.category,
                                    "config": {"compiled_yaml": str(self.compiled_yaml)},
                                    "scenario": {"id": item.get("id"), "name": item.get("name")},
                                    "run": {"id": run_uid},
                                }
                            }
                            gid = run_uid
                            with agentio_trace(self.workflow_name, group_id=gid, metadata=meta):
                                res = await self._run_once(instructions, prompt)
                        except Exception:
                            res = await self._run_once(instructions, prompt)
                    else:
                        res = await self._run_once(instructions, prompt)
                except Exception as e:
                    outs.append({"score": 0.0, "error": f"run_failed:{e}"})
                    continue
                measures = list(res.get("measures") or [])
                # Derive classification using judge-backed measure when labels exist
                class_info = None
                class_score = None
                try:
                    if has_labels:
                        cm = self._find_classification_measure(measures)
                        if cm is not None and ("label" in item):
                            label = bool(item.get("label"))
                            passed = (cm.get("passed") is True)
                            # Map judge-backed measure pass=>no violation (pred=False), fail=>violation (pred=True)
                            pred = (not passed)
                            agree = (pred == label)
                            kind = None
                            if pred is None:
                                kind = "UNK"
                            else:
                                if pred is True and label is True:
                                    kind = "TP"
                                elif pred is False and label is False:
                                    kind = "TN"
                                elif pred is True and label is False:
                                    kind = "FP"
                                elif pred is False and label is True:
                                    kind = "FN"
                            class_info = {"pred": pred, "label": label, "agree": agree, "kind": kind, "measure": cm.get("name")}
                            class_score = 1.0 if (agree is True) else 0.0
                except Exception:
                    class_info = None
                    class_score = None

                # Compute constraint score over remaining measures (exclude classification measure to avoid double counting)
                constraints = []
                if measures:
                    cm_name = class_info.get("measure") if isinstance(class_info, dict) else None
                    for m in measures:
                        if cm_name and m.get("name") == cm_name:
                            continue
                        constraints.append(m)
                constraint_score = self._score_from_measures(constraints) if constraints else None

                # Hybrid score: if classification exists and constraints exist, average; else fall back
                if class_score is not None and constraint_score is not None:
                    score = 0.5 * class_score + 0.5 * float(constraint_score)
                elif class_score is not None:
                    score = class_score
                elif constraint_score is not None:
                    score = float(constraint_score)
                else:
                    score = self._score_from_measures(measures)
                out: Dict[str, Any] = {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "score": score,
                }
                if capture_traces:
                    fails = []
                    for m in measures:
                        pv = m.get("passed")
                        if pv is False:
                            fails.append({
                                "name": m.get("name"),
                                "reason": m.get("reason"),
                            })
                    out.update({
                        "prompt": prompt,
                        "final_assistant_text": res.get("final_assistant_text"),
                        "measures": measures,
                        "failures": fails,
                        "classification": class_info,
                    })
                outs.append(out)
            return outs

        try:
            outputs = asyncio.run(_run_batch())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                outputs = loop.run_until_complete(_run_batch())  # type: ignore
            else:
                outputs = asyncio.run(_run_batch())

        scores = [float(o.get("score") or 0.0) for o in outputs]
        trajectories: Optional[List[Dict[str, Any]]] = None
        if capture_traces:
            trajectories = []
            for o in outputs:
                tr = {
                    "id": o.get("id"),
                    "name": o.get("name"),
                    "score": o.get("score"),
                    "prompt": o.get("prompt"),
                    "final_assistant_text": o.get("final_assistant_text"),
                    "failures": o.get("failures"),
                }
                trajectories.append(tr)

        class _EvalBatch:
            def __init__(self, outputs, scores, trajectories):
                self.outputs = outputs
                self.scores = scores
                self.trajectories = trajectories

        return _EvalBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def _read_text_file(self, base_dir: Path, rel: str) -> str:
        p = Path(rel)
        if not p.is_absolute():
            p = (base_dir / rel).resolve()
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _format_dataset_md(self, items: List[Dict[str, Any]]) -> str:
        def render_value(value, level=3):
            if isinstance(value, dict):
                s = ""
                for k, v in value.items():
                    s += f"{'#' * level} {k}\n"
                    s += render_value(v, min(level + 1, 6))
                if not value:
                    s += "\n"
                return s
            elif isinstance(value, (list, tuple)):
                s = ""
                for i, item in enumerate(value):
                    s += f"{'#' * level} Item {i + 1}\n"
                    s += render_value(item, min(level + 1, 6))
                if not value:
                    s += "\n"
                return s
            else:
                return f"{str(value).strip()}\n\n"
        def convert_sample_to_markdown(sample, examplenum):
            s = f"# Example {examplenum}\n"
            for key, val in sample.items():
                s += f"## {key}\n"
                s += render_value(val, level=3)
            return s
        return "\n\n".join(convert_sample_to_markdown(sample, i + 1) for i, sample in enumerate(items))

    def _get_tools_schema_md(self) -> str:
        if hasattr(self, "__tools_schema_md"):
            return getattr(self, "__tools_schema_md") or ""
        md = ""
        try:
            # Use spec.available_tools to access tool objects (no async build required)
            spec = load_agent_spec_from_yaml(str(self.compiled_yaml))
            tools = getattr(spec, 'available_tools', []) or []
            lines: List[str] = []
            for t in tools:
                try:
                    name = getattr(t, 'name', None) or getattr(t, '__name__', '')
                    desc = getattr(t, 'description', '')
                    schema = getattr(t, 'params_json_schema', None) or {}
                    lines.append(f"## {name}")
                    if desc:
                        lines.append(desc)
                    if schema:
                        import json as _json
                        lines.append("```json")
                        lines.append(_json.dumps(schema, indent=2))
                        lines.append("```")
                except Exception:
                    continue
            if lines:
                md = "\n".join(lines)
        except Exception:
            md = ""
        setattr(self, "__tools_schema_md", md)
        return md

    async def _ensure_optimizer_agent(self):
        if self._optimizer_agent is not None:
            return self._optimizer_agent
        ocfg = self._optimizer_cfg or {}
        agent_cfg = (ocfg.get("agent") or {})
        from omniagents.core.agents.specs import AgentSpec
        from omniagents.core.agents.builder import _default_build_agent
        base_dir = self.compiled_yaml.resolve().parent
        instructions = agent_cfg.get("instructions") or ""
        if not instructions and agent_cfg.get("instructions_file"):
            instructions = self._read_text_file(base_dir, str(agent_cfg.get("instructions_file")))
        if not instructions:
            instructions = "You are an optimizer. Return ONLY a fenced code block with the new text."
        model = agent_cfg.get("model") or "gpt-4.1"
        def _get_instr() -> str:
            return instructions
        spec = AgentSpec(
            name=agent_cfg.get("name") or f"Optimizer:{self.optimizer_key}",
            header_title=agent_cfg.get("name") or f"Optimizer:{self.optimizer_key}",
            page_title=agent_cfg.get("name") or f"Optimizer:{self.optimizer_key}",
            welcome_text="",
            client_storage_prefix=f"optimizer_{self.optimizer_key}.",
            get_agent_instructions=_get_instr,
            build_agent=None,
            available_tools=[],
            model_name=model,
            output_type=str,
            output_type_strict=False,
        )
        agent = await _default_build_agent({}, None, spec, None)
        self._optimizer_agent = agent
        return agent

    def _extract_fenced(self, s: str) -> str:
        try:
            t = str(s)
            if t.count("```") >= 2:
                i = t.find("```"); j = t.rfind("```")
                if 0 <= i < j:
                    inner = t[i+3:j].strip()
                    if inner.lower().startswith("json") or inner.lower().startswith("yaml"):
                        inner = inner.split("\n", 1)[1] if "\n" in inner else ""
                    return inner.strip()
            if t.startswith("```") and t.endswith("```"):
                t = t.strip("`")
                if t.lower().startswith("json") or t.lower().startswith("yaml"):
                    t = t[4:]
                return t.strip()
            return t.strip()
        except Exception:
            return s

    def _default_instruction_prompt(self, current_doc: str, items: List[Dict[str, Any]]) -> str:
        md = self._format_dataset_md(items)
        tools_md = self._get_tools_schema_md()
        name = self._agent_meta.get("label") or self._agent_meta.get("name") or "assistant"
        desc = self._agent_meta.get("description") or ""
        sections: List[str] = []
        sections.append("You are optimizing the LLM agent’s instructions to improve performance across scenarios.")
        info_parts: List[str] = []
        persona = [f"- Role: assistant", f"- Name: {name}"]
        if desc:
            persona.append(f"- Description: {desc}")
        info_parts.append("\n".join(persona))
        info_parts.append("\n## Current Instructions\n```\n" + current_doc + "\n```")
        if tools_md:
            info_parts.append("\n## Available Tools (if any)\n" + tools_md)
        sections.append("# LLM Agent Information\n" + "\n\n".join(info_parts))
        sections.append("# Goal\nWrite improved, generalizable instructions that maximize correct behavior across the dataset without overfitting.")
        ctx_parts: List[str] = []
        ctx_parts.append("The reflective examples below include task inputs, the LLM agent’s responses, and feedback highlighting successes and failures. Use them to identify generalizable decision rules and edge cases. Do not copy phrasing verbatim or overfit to specific example IDs.")
        ctx_parts.append("\n```\n" + md + "\n```")
        sections.append("# Context\n" + "\n\n".join(ctx_parts))
        guidance = [
            "- Do: include domain constraints and decision rules; be explicit about edge cases.",
            "- Don’t: overfit to specific IDs; avoid brittle patterns; don’t require unavailable context.",
            "- Tools: use tools when they improve correctness; follow parameter schemas.",
            "- Output format: return ONLY the new instructions in a single fenced code block. No commentary.",
        ]
        sections.append("# Guidance\n" + "\n".join(guidance))
        return "\n\n".join(sections)

    def _render_optimizer_prompt(self, component: str, current_text: str, items: List[Dict[str, Any]]) -> str:
        ocfg = self._optimizer_cfg or {}
        proposals = (ocfg.get("proposals") or {})
        base_dir = self.compiled_yaml.resolve().parent
        if component == "instructions":
            tf = proposals.get("instructions_template_file")
            if tf:
                tmpl = self._read_text_file(base_dir, str(tf))
                dataset_md = self._format_dataset_md(items)
                tools_md = self._get_tools_schema_md()
                return render_template(
                    tmpl,
                    current_instruction_doc=current_text,
                    dataset_md=dataset_md,
                    tools_schema_md=tools_md,
                    target_role="assistant",
                    agent_name=self._agent_meta.get("name"),
                    agent_label=self._agent_meta.get("label"),
                    agent_description=self._agent_meta.get("description"),
                )
            return self._default_instruction_prompt(current_text, items)
        # For any other component, reuse default
        return self._default_instruction_prompt(current_text, items)

    def _propose_new_texts(self, candidate: Dict[str, str], reflective_dataset: Dict[str, List[Dict[str, Any]]], components_to_update: List[str]) -> Dict[str, str]:
        import asyncio as _asyncio
        new_texts: Dict[str, str] = {}
        async def _run():
            agent = await self._ensure_optimizer_agent()
            from agents import Runner as AgentsRunner
            for name in components_to_update:
                base_text = candidate.get(name) or ""
                ds_items = list(reflective_dataset.get(name) or [])
                prompt = self._render_optimizer_prompt(name, base_text, ds_items)
                if self.workflow_name:
                    try:
                        from agentio import trace as agentio_trace  # type: ignore
                        import uuid as _uuid
                        run_uid = _uuid.uuid4().hex[:8]
                        meta = {
                            "omniagents": {
                                "run_type": "optimizer_proposal",
                                "scope": "agent",
                                "category": self.category,
                                "component": name,
                                "config": {"compiled_yaml": str(self.compiled_yaml)},
                                "run": {"id": run_uid},
                            }
                        }
                        with agentio_trace(self.workflow_name, group_id=run_uid, metadata=meta):
                            res = await AgentsRunner.run(agent, prompt, max_turns=1)
                    except Exception:
                        res = await AgentsRunner.run(agent, prompt, max_turns=1)
                else:
                    res = await AgentsRunner.run(agent, prompt, max_turns=1)
                out = getattr(res, "final_output", None)
                out_s = out if isinstance(out, str) else str(out)
                new_texts[name] = self._extract_fenced(out_s)
            return new_texts
        try:
            return _asyncio.run(_run())
        except RuntimeError:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                return loop.run_until_complete(_run())  # type: ignore
            return _asyncio.run(_run())

    def make_reflective_dataset(
        self,
        candidate: Dict[str, str],
        eval_batch: Any,
        components_to_update: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        reflective: Dict[str, List[Dict[str, Any]]] = {}
        trajs = list(eval_batch.trajectories or [])
        # If classification info exists, prioritize FP/FN similar to judge adapter
        has_class = any((isinstance(t.get("classification"), dict) and t.get("classification", {}).get("kind")) for t in trajs)
        if has_class:
            kinds = {"TP": [], "TN": [], "FP": [], "FN": [], "UNK": []}
            for t in trajs:
                cls = t.get("classification") or {}
                k = cls.get("kind") or "UNK"
                if k not in kinds:
                    k = "UNK"
                kinds[k].append(t)
            errs = kinds.get("FP", []) + kinds.get("FN", [])
            tp = kinds.get("TP", [])
            tn = kinds.get("TN", [])
            sample_tp: List[Dict[str, Any]] = []
            sample_tn: List[Dict[str, Any]] = []
            target = max(1, int(0.25 * max(1, len(errs))))
            if tp:
                sample_tp = tp[:target]
            if tn:
                sample_tn = tn[:target]
            selected = errs + sample_tp + sample_tn
            for comp in components_to_update:
                if comp != "instructions":
                    continue
                items: List[Dict[str, Any]] = []
                for t in selected:
                    fails = list(t.get("failures") or [])
                    reasons = []
                    for f in fails:
                        nm = f.get("name")
                        rsn = f.get("reason")
                        reasons.append(f"{nm}: {rsn}" if rsn else str(nm))
                    cls = t.get("classification") or {}
                    k = cls.get("kind")
                    if k == "TP":
                        fb = "Correct Fail. Reinforce guidance that led to correct classification."
                    elif k == "TN":
                        fb = "Correct Pass. Reinforce guidance that prevents false triggers."
                    elif k == "FP":
                        fb = "False Positive. Tighten criteria or require stronger evidence to avoid spurious violations."
                    elif k == "FN":
                        fb = "False Negative. Add explicit rules and edge cases to catch these violations."
                    else:
                        fb = "No decision. Clarify rules and required outputs."
                    if reasons:
                        fb = fb + " Measure failures: " + ", ".join(reasons)
                    d = {
                        "Inputs": {"text": t.get("prompt")},
                        "Generated Outputs": {"final_assistant_text": t.get("final_assistant_text")},
                        "Feedback": fb,
                    }
                    items.append(d)
                if items:
                    reflective[comp] = items
        else:
            for comp in components_to_update:
                if comp != "instructions":
                    continue
                items: List[Dict[str, Any]] = []
                for t in trajs:
                    fails = list(t.get("failures") or [])
                    if not fails:
                        fb = "All measures passed. Reinforce useful phrasing and constraints."
                    else:
                        reasons = []
                        for f in fails:
                            nm = f.get("name")
                            rsn = f.get("reason")
                            reasons.append(f"{nm}: {rsn}" if rsn else str(nm))
                        fb = "Some measures failed. Improve guidance to address: " + ", ".join(reasons)
                    d = {
                        "Inputs": {"text": t.get("prompt")},
                        "Generated Outputs": {"final_assistant_text": t.get("final_assistant_text")},
                        "Feedback": fb,
                    }
                    items.append(d)
                if items:
                    reflective[comp] = items
        if not reflective:
            raise Exception("No reflective data constructed")
        return reflective
