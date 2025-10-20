from __future__ import annotations

import asyncio
import json
import inspect
from dataclasses import dataclass
from pathlib import Path
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
)
from omniagents.core.eval.pipeline import _failure_category_map, _load_local_measures, _run_judges
from omniagents.core.utils.jinja import render_template
from omniagents.core.project.runtime import load_project
from omniagents.core.tools.discovery import discover_functions_in_dir

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
        self.debug_dir: Optional[Path] = None
        self.__debug_iter = 0
        self.__debug_last_batch_ids: List[str] = []

        self._spec_kwargs: Dict[str, Any] = {}
        self._project_root: Optional[Path] = None
        manifest = self._locate_manifest()
        if manifest is not None:
            try:
                ctx = load_project(str(manifest))
                self._project_root = ctx.root
                tool_registry = dict(ctx.tool_registry or {})
                agent_tools_dir = self.compiled_yaml.parent / "tools"
                if agent_tools_dir.is_dir():
                    try:
                        tool_registry.update(discover_functions_in_dir(str(agent_tools_dir)))
                    except Exception:
                        pass
                if tool_registry:
                    self._spec_kwargs["tool_registry"] = tool_registry
                guardrails = ctx.guardrails_registry or {}
                if guardrails:
                    self._spec_kwargs["guardrails_registry"] = guardrails
                server_functions = ctx.server_functions_registry or {}
                if server_functions:
                    self._spec_kwargs["server_functions_registry"] = server_functions
                defaults: Dict[str, Any] = {}
                if ctx.tracing_defaults:
                    defaults["tracing"] = ctx.tracing_defaults
                if ctx.settings_defaults:
                    defaults["settings"] = ctx.settings_defaults
                if ctx.ui_defaults:
                    defaults.update(ctx.ui_defaults)
                if defaults:
                    self._spec_kwargs["project_defaults"] = defaults
            except Exception:
                self._project_root = None
        self._ensure_sys_path()

    def _locate_manifest(self) -> Optional[Path]:
        current = self.compiled_yaml.parent
        for _ in range(16):
            candidate = current / "project.yml"
            if candidate.is_file():
                return candidate
            if current == current.parent:
                break
            current = current.parent
        return None

    def _ensure_sys_path(self) -> None:
        try:
            if self._project_root:
                proj_path = str(self._project_root)
                if proj_path not in sys.path:
                    sys.path.insert(0, proj_path)
            compiled_parent = str(self.compiled_yaml.parent)
            if compiled_parent not in sys.path:
                sys.path.insert(0, compiled_parent)
        except Exception:
            pass

    def _load_spec(self):
        try:
            if self._spec_kwargs:
                return load_agent_spec_from_yaml(str(self.compiled_yaml), **self._spec_kwargs)
        except Exception:
            pass
        return load_agent_spec_from_yaml(str(self.compiled_yaml))

    async def _run_once(self, instructions: str, prompt: str) -> Dict[str, Any]:
        spec = self._load_spec()
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

        try:
            run = agent.run_streamed(msgs)
            async for _ in run.stream_events():
                pass

            session_id = getattr(agent, "_session_id", None)
            history = load_history(
                session_id,
                project_slug=spec.project_slug,
                agent_slug=spec.agent_slug,
            ) if session_id else []
            text = final_assistant_text(history)
        finally:
            cleanup = getattr(agent, "cleanup", None)
            if callable(cleanup):
                maybe_coro = cleanup()
                if inspect.isawaitable(maybe_coro):
                    await maybe_coro  # type: ignore[arg-type]

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

    def evaluate(
        self,
        batch: List[Dict[str, Any]],
        candidate: Dict[str, str],
        capture_traces: bool = False,
    ):
        instructions = str(candidate.get("instructions") or "You are a helpful assistant. Return ONLY JSON when asked.")

        async def _run_batch() -> List[Dict[str, Any]]:
            outs: List[Dict[str, Any]] = []
            for item in batch:
                prompt = str(item.get("prompt") or "")
                if not prompt:
                    fail_entry = {"name": "prompt", "reason": "missing_prompt"}
                    outs.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "score": 0.0,
                        "violation": True,
                        "failures": [fail_entry],
                        "measures": [],
                        "prompt": prompt,
                    })
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
                    fail_entry = {"name": "execution", "reason": str(e)}
                    outs.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "score": 0.0,
                        "violation": True,
                        "failures": [fail_entry],
                        "measures": [],
                        "prompt": prompt,
                    })
                    continue
                measures = list(res.get("measures") or [])
                failures = []
                for m in measures:
                    if m.get("passed") is False:
                        failures.append({
                            "name": m.get("name"),
                            "reason": m.get("reason"),
                        })
                violation = bool(failures)
                score = self._score_from_measures(measures)
                out: Dict[str, Any] = {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "score": score,
                    "violation": violation,
                }
                if capture_traces:
                    out.update({
                        "prompt": prompt,
                        "final_assistant_text": res.get("final_assistant_text"),
                        "measures": measures,
                        "failures": failures,
                        "violation": violation,
                    })
                else:
                    out["failures"] = failures
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
                    "violation": o.get("violation"),
                }
                if "measures" in o:
                    tr["measures"] = o.get("measures")
                trajectories.append(tr)

        try:
            if self.debug_dir is not None:
                batch_ids = [str(o.get("id")) for o in outputs if o.get("id") is not None]
                stage = None
                if capture_traces:
                    self.__debug_iter += 1
                    stage = "before"
                    self.__debug_last_batch_ids = list(batch_ids)
                else:
                    if self.__debug_last_batch_ids and self.__debug_last_batch_ids == batch_ids:
                        stage = "after"
                if stage is not None:
                    try:
                        from pathlib import Path as _Path

                        pdir = _Path(self.debug_dir)
                        pdir.mkdir(parents=True, exist_ok=True)
                        fails = sum(1 for o in outputs if bool(o.get("violation")))
                        passes = sum(1 for o in outputs if not bool(o.get("violation")))
                        mismatches = []
                        for idx, o in enumerate(outputs):
                            if bool(o.get("violation")) and len(mismatches) < 5:
                                try:
                                    mismatches.append({
                                        "id": batch[idx].get("id"),
                                        "name": batch[idx].get("name"),
                                        "failures": o.get("failures"),
                                    })
                                except Exception:
                                    continue
                        by_id = {}
                        for idx, o in enumerate(outputs):
                            try:
                                by_id[str(batch[idx].get("id"))] = {
                                    "violation": bool(o.get("violation")),
                                    "score": float(o.get("score") or 0.0),
                                }
                            except Exception:
                                continue
                        info = {
                            "iteration": self.__debug_iter,
                            "stage": stage,
                            "subsample_ids": batch_ids,
                            "violation_counts": {"violations": fails, "passes": passes},
                            "scores_sum": float(sum(scores)),
                            "candidate_summary": {
                                "has_instructions": isinstance(candidate.get("instructions"), str),
                                "instructions_len": len(candidate.get("instructions") or ""),
                            },
                            "by_id": by_id,
                            "examples": mismatches,
                        }
                        fp = pdir / f"iter_{self.__debug_iter:04d}_{stage}.json"
                        fp.write_text(json.dumps(info, indent=2), encoding="utf-8")
                    except Exception:
                        pass
        except Exception:
            pass

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
            spec = self._load_spec()
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
            project_slug=None,
            agent_slug=f"optimizer_{self.optimizer_key}",
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
        if not trajs:
            raise Exception("No reflective data constructed")

        violations = [t for t in trajs if bool(t.get("violation"))]
        compliant = [t for t in trajs if not bool(t.get("violation"))]
        support_count = max(1, int(0.25 * len(violations))) if violations else max(1, len(compliant))
        support_examples = compliant[:support_count]
        selected = violations + support_examples if violations else compliant[:support_count]

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
                measure_summaries = []
                for m in (t.get("measures") or []):
                    nm = m.get("name")
                    status = "pass" if m.get("passed") else "fail"
                    reason = m.get("reason")
                    if reason:
                        measure_summaries.append(f"{nm} ({status}): {reason}")
                    else:
                        measure_summaries.append(f"{nm} ({status})")
                if bool(t.get("violation")):
                    if reasons:
                        fb = "Measures failed. Update instructions to resolve: " + ", ".join(reasons) + "."
                    else:
                        fb = "Measures failed. Strengthen instructions to address the observed violations."
                    if measure_summaries:
                        fb = fb + " Measure details: " + " | ".join(measure_summaries)
                else:
                    fb = "All measures passed. Reinforce guidance that preserves compliant behavior."
                    if measure_summaries:
                        fb = fb + " Measure details: " + " | ".join(measure_summaries)
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
