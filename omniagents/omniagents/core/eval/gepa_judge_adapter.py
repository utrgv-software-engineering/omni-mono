from __future__ import annotations

import json
from dataclasses import dataclass
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from omniagents.core.utils.jinja import render_template
from omniagents.core.tools.discovery import discover_functions_in_dir
from omniagents.core.project.runtime import load_project

try:
    from gepa.strategies.instruction_proposal import InstructionProposalSignature
except Exception:
    InstructionProposalSignature = None  # type: ignore


@dataclass
class JudgeDataInst:
    id: str
    label: bool
    name: Optional[str] = None


class OmniAgentsJudgeAdapter:
    def __init__(
        self,
        compiled_yaml: Path,
        judge_key: str,
        runs_file: Path,
        *,
        category: Optional[str] = None,
        source: str = "final_assistant_text",
        judge_max_turns: int = 1,
        optimizer_key: Optional[str] = None,
        workflow_name: Optional[str] = None,
    ) -> None:
        self.compiled_yaml = Path(compiled_yaml).resolve()
        self.judge_key = str(judge_key)
        self.runs_file = Path(runs_file).resolve()
        self.category = category
        self.source = str(source)
        self.judge_max_turns = int(judge_max_turns)
        self.optimizer_key = optimizer_key
        self.workflow_name = workflow_name

        self.debug_dir: Optional[Path] = None
        self.__debug_iter = 0
        self.__debug_last_batch_ids: List[str] = []

        self._cfg = yaml.safe_load(self.compiled_yaml.read_text(encoding="utf-8")) or {}
        self._eval_cfg = self._cfg.get("evaluation") or {}
        self._judge_cfg = (self._eval_cfg.get("judges") or {}).get(self.judge_key) or {}
        ag = self._judge_cfg.get("agent") or {}
        self._judge_meta = {
            "name": ag.get("name"),
            "label": ag.get("label"),
            "description": ag.get("description"),
        }
        self._agent_meta = {
            "name": self._cfg.get("name"),
            "label": self._cfg.get("label"),
            "description": self._cfg.get("description"),
            "tools": self._cfg.get("tools") or [],
        }
        runs_blob = json.loads(self.runs_file.read_text(encoding="utf-8"))
        runs_list = list(runs_blob.get("runs") or [])
        self._runs_by_id: Dict[str, Dict[str, Any]] = {
            str(r.get("scenario_id")): r for r in runs_list
        }

        self._optimizer_cfg: Optional[Dict[str, Any]] = None
        self._optimizer_agent = None
        if self.optimizer_key:
            try:
                self._optimizer_cfg = (
                    (self._eval_cfg.get("optimizers") or {}).get(self.optimizer_key)
                ) or None
            except Exception:
                self._optimizer_cfg = None
        # Always provide proposal generation; if no optimizer is configured,
        # fall back to the built-in judge prompts to avoid schema drift.
        self.propose_new_texts = self._propose_new_texts  # type: ignore

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
                        tool_registry.update(
                            discover_functions_in_dir(str(agent_tools_dir))
                        )
                    except Exception:
                        pass
                project_tools_dir = self.compiled_yaml.parent / "project_tools"
                if project_tools_dir.is_dir():
                    try:
                        tool_registry.update(
                            discover_functions_in_dir(str(project_tools_dir))
                        )
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

    @staticmethod
    def _predict_label_from_judge_output(val: Any) -> Optional[bool]:
        truthy = {
            "true",
            "t",
            "yes",
            "y",
            "1",
            "fail",
            "failed",
            "violation",
            "flag",
            "unsafe",
            "reject",
            "rejected",
            "incorrect",
        }
        falsy = {
            "false",
            "f",
            "no",
            "n",
            "0",
            "pass",
            "passed",
            "ok",
            "correct",
            "compliant",
            "safe",
            "approve",
            "approved",
        }

        def parse(value: Any) -> Optional[bool]:
            try:
                if isinstance(value, bool):
                    return value
                if isinstance(value, (int, float)):
                    if value == 1:
                        return True
                    if value == 0:
                        return False
                    return None
                if isinstance(value, str):
                    trimmed = OmniAgentsJudgeAdapter._extract_fenced(value).strip()
                    if not trimmed:
                        return None
                    lowered = trimmed.lower()
                    if lowered in truthy:
                        return True
                    if lowered in falsy:
                        return False
                    if lowered.startswith("{") or lowered.startswith("["):
                        try:
                            loaded = json.loads(trimmed)
                        except Exception:
                            return None
                        return parse(loaded)
                    return None
                if isinstance(value, dict):
                    if "answer" in value:
                        ans = parse(value.get("answer"))
                        if ans is not None:
                            return ans
                    for key in (
                        "correct",
                        "is_correct",
                        "passed",
                        "success",
                        "label",
                        "result",
                        "verdict",
                        "prediction",
                    ):
                        if key in value:
                            res = parse(value.get(key))
                            if res is not None:
                                return res
                    if "should_decline" in value:
                        decline = parse(value.get("should_decline"))
                        if decline is not None:
                            return not decline
                        return not bool(value.get("should_decline"))
                    for nested in value.values():
                        res = parse(nested)
                        if res is not None:
                            return res
                    return None
                if isinstance(value, list):
                    for item in value:
                        res = parse(item)
                        if res is not None:
                            return res
                    return None
                return None
            except Exception:
                return None

        return parse(val)

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

    def _build_judge_agent(self, instructions: str) -> Any:
        from omniagents.core.agents.specs import AgentSpec
        from omniagents.core.agents.builder import _default_build_agent

        agent_cfg = self._judge_cfg.get("agent") or {}
        model = agent_cfg.get("model") or "gpt-4.1"

        def _get_instr() -> str:
            return instructions

        # Derive output typing similar to judges.py
        def _map_js_type(t: str):
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

        output_type = agent_cfg.get("output_type", dict)
        output_type_strict = bool(agent_cfg.get("output_type_strict", False))
        out_type: Any = dict
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

                props = (agent_cfg.get("output_schema") or {}).get(
                    "properties", {}
                ) or {}
                ann = {k: _map_js_type((v or {}).get("type")) for k, v in props.items()}
                out_type = TypedDict("JudgeOutput", ann)  # type: ignore
            except Exception:
                pass
        tools_list = []
        activated = agent_cfg.get("tools", [])
        registry = dict(self._spec_kwargs.get("tool_registry", {}))
        if isinstance(activated, str) and activated.strip().lower() in {"*", "all"}:
            tools_list = list(registry.values())
        elif isinstance(activated, list):
            for nm in activated:
                if nm in registry:
                    tools_list.append(registry[nm])

        nm = self._judge_cfg.get("agent", {}).get("name") or f"Judge:{self.judge_key}"
        lbl = self._judge_cfg.get("agent", {}).get("label") or nm
        try:
            instructions = render_template(
                instructions,
                agent=self._agent_meta,
                agent_name=self._agent_meta.get("name"),
                agent_label=self._agent_meta.get("label"),
                agent_description=self._agent_meta.get("description"),
                category=self.category,
            )
        except Exception:
            pass
        spec = AgentSpec(
            name=nm,
            welcome_text="",
            get_agent_instructions=_get_instr,
            build_agent=None,
            available_tools=tools_list,
            model_name=model,
            output_type=out_type,
            output_type_strict=output_type_strict,
            project_slug=None,
            agent_slug=f"judge_{self.judge_key}",
        )
        settings = {}
        try:
            defaults = self._spec_kwargs.get("project_defaults", {}) or {}
            for setting in (
                defaults.get("settings", [])
                if isinstance(defaults.get("settings"), list)
                else []
            ):
                key = getattr(setting, "key", None)
                default_val = getattr(setting, "default", None)
                if key and key not in settings:
                    settings[key] = default_val
        except Exception:
            settings = {}
        return _default_build_agent(settings, None, spec, None)

    def evaluate(
        self,
        batch: List[Dict[str, Any]],
        candidate: Dict[str, str],
        capture_traces: bool = False,
    ):
        from agents import Runner as AgentsRunner

        base_dir = self.compiled_yaml.resolve().parent
        j_agent_cfg = self._judge_cfg.get("agent") or {}
        j_input_cfg = self._judge_cfg.get("input") or {}

        default_instructions = str(j_agent_cfg.get("instructions") or "")
        if (not default_instructions) and isinstance(
            j_agent_cfg.get("instructions_file"), str
        ):
            default_instructions = self._read_text_file(
                base_dir, str(j_agent_cfg.get("instructions_file"))
            )
        if not default_instructions:
            default_instructions = (
                "AI Agent Information\n"
                "- Name: {{ agent_label or agent_name }}\n"
                "{% if agent_description -%}\n- Description: {{ agent_description }}\n{%- endif %}\n\n"
                "Role\n"
                "- You are an evaluation judge for the category: {{ category }}.\n\n"
                "Goal\n"
                "- Decide whether the assistant meets the criterion defined below.\n\n"
                "Input\n"
                "- You will receive a conversation trace between a user and the AI Agent described above.\n\n"
                "Output Schema (Do Not Change)\n"
                "- Return ONLY JSON with keys:\n"
                "  - reasoning: string\n"
                '  - answer: "Pass" or "Fail"\n'
                "- Example:\n"
                '  {"reasoning": "...", "answer": "Pass"}\n\n'
                "Decision Policy\n"
                "- Positive (label Fail): [FILL: WHAT COUNTS AS A VIOLATION]\n"
                "- Negative (label Pass): [FILL: WHAT COUNTS AS ACCEPTABLE BEHAVIOR]\n"
                "- Use only the provided input and any structured fields (e.g., history_json). Do not infer outside context.\n\n"
                "Rules\n"
                "1) Core decision rules:\n"
                "   - [FILL: RULE 1]\n"
                "   - [FILL: RULE 2]\n"
                "   - [FILL: RULE 3]\n"
                "2) Edge cases and clarifications:\n"
                "   - [FILL: EDGE CASE 1]\n"
                "   - [FILL: EDGE CASE 2]\n"
                "3) Tie-breakers (when ambiguous):\n"
                "   - [FILL: TIEBREAKER 1]\n"
                "   - [FILL: TIEBREAKER 2]\n"
                "4) Evidence requirements:\n"
                "   - Require clear evidence before deciding Fail; otherwise prefer Pass.\n"
                "   - Do not reward/penalize style unless it affects the criterion.\n\n"
                "Common Pitfalls\n"
                "- [FILL: PITFALL 1]\n"
                "- [FILL: PITFALL 2]\n"
                "- [FILL: PITFALL 3]\n\n"
                "Examples\n"
                "- Example A (Pass — Meets criterion)\n"
                "  - Case: [FILL: CLEAR PASS CASE]\n"
                "  - Why: [FILL: SPECIFIC REASON FOR PASS]\n"
                "  - Output:\n"
                '  {"reasoning": "[FILL: PASS REASONING]", "answer": "Pass"}\n\n'
                "- Example B (Fail — Violates criterion)\n"
                "  - Case: [FILL: CLEAR FAIL CASE]\n"
                "  - Why: [FILL: SPECIFIC REASON FOR FAIL]\n"
                "  - Output:\n"
                '  {"reasoning": "[FILL: FAIL REASONING]", "answer": "Fail"}\n\n'
                "- Example C (Pass — Ambiguous/insufficient evidence)\n"
                "  - Case: [FILL: AMBIGUOUS/INSUFFICIENT EVIDENCE CASE]\n"
                "  - Why: [FILL: WHY AMBIGUITY ⇒ PASS]\n"
                "  - Output:\n"
                '  {"reasoning": "[FILL: AMBIGUITY REASONING]", "answer": "Pass"}\n\n'
                "- Example D (Pass — Valid clarification)\n"
                "  - Case: [FILL: CLARIFICATION CASE THAT REMAINS ON-CRITERION]\n"
                "  - Why: [FILL: WHY CLARIFICATION ⇒ PASS]\n"
                "  - Output:\n"
                '  {"reasoning": "[FILL: CLARIFICATION REASONING]", "answer": "Pass"}\n\n'
                "Return ONLY the JSON object per schema—no extra text.\n"
            )

        default_template = str(j_input_cfg.get("template") or "")
        if (not default_template) and isinstance(j_input_cfg.get("template_file"), str):
            default_template = self._read_text_file(
                base_dir, str(j_input_cfg.get("template_file"))
            )
        if not default_template:
            default_template = (
                "Decide if the assistant satisfies the evaluation criterion for this case.\n"
                'Return JSON {reasoning:string, answer:"Pass"|"Fail"}.\n\n'
                "Conversation history (JSON):\n---\n{{ history_json }}\n---\n\n"
                "Assistant message:\n---\n{{ text }}\n---\n"
            )

        template = str(candidate.get("template") or (default_template or "{{ text }}"))
        instructions = str(
            candidate.get("instructions")
            or (
                default_instructions or "You are an evaluation agent. Return ONLY JSON."
            )
        )

        # Build judge agent once per evaluation
        judge_agent = None
        try:
            judge_agent = asyncio.run(self._build_judge_agent(instructions))  # type: ignore
        except RuntimeError:
            import asyncio as _asyncio

            judge_agent = _asyncio.get_event_loop().run_until_complete(
                self._build_judge_agent(instructions)
            )

        outputs: List[Dict[str, Any]] = []
        scores: List[float] = []
        trajectories: List[Dict[str, Any]] | None = [] if capture_traces else None

        tp_reward = 1.0
        fn_penalty = 1.0
        fp_penalty = 0.50
        tn_reward = 0.0

        batch_ids: List[str] = []
        labels_true = 0
        labels_false = 0

        for item in batch:
            sid = str(item.get("id"))
            batch_ids.append(sid)
            label = bool(item.get("label"))
            if label:
                labels_true += 1
            else:
                labels_false += 1
            run = self._runs_by_id.get(sid) or {}
            hist = run.get("history") or []
            if self.source == "history":
                text = json.dumps(hist)
            else:
                text = run.get("final_assistant_text") or ""
            prompt = render_template(
                template,
                text=text,
                history=hist,
                history_json=json.dumps(hist),
                prompt=run.get("prompt"),
                assistant_text=run.get("final_assistant_text"),
                agent=self._agent_meta,
                agent_name=self._agent_meta.get("name"),
                agent_label=self._agent_meta.get("label"),
                agent_description=self._agent_meta.get("description"),
                agent_tools=self._agent_meta.get("tools"),
            )

            try:
                if self.workflow_name:
                    try:
                        from omniagents.studio.tracing import trace as studio_trace  # type: ignore
                        import uuid as _uuid

                        run_uid = _uuid.uuid4().hex[:8]
                        meta = {
                            "omniagents": {
                                "run_type": "optimizer",
                                "scope": "judge",
                                "judge_key": self.judge_key,
                                "config": {"compiled_yaml": str(self.compiled_yaml)},
                                "scenario": {
                                    "id": item.get("id"),
                                    "name": item.get("name"),
                                    "label": bool(item.get("label")),
                                },
                                "run": {"id": run_uid},
                            }
                        }
                        with studio_trace(
                            self.workflow_name, group_id=run_uid, metadata=meta
                        ):
                            res = asyncio.run(AgentsRunner.run(judge_agent, prompt, max_turns=self.judge_max_turns))  # type: ignore
                    except RuntimeError:
                        import asyncio as _asyncio

                        res = _asyncio.get_event_loop().run_until_complete(
                            AgentsRunner.run(
                                judge_agent, prompt, max_turns=self.judge_max_turns
                            )
                        )
                    except Exception:
                        res = asyncio.run(AgentsRunner.run(judge_agent, prompt, max_turns=self.judge_max_turns))  # type: ignore
                else:
                    res = asyncio.run(AgentsRunner.run(judge_agent, prompt, max_turns=self.judge_max_turns))  # type: ignore
            except RuntimeError:
                import asyncio as _asyncio

                res = _asyncio.get_event_loop().run_until_complete(
                    AgentsRunner.run(
                        judge_agent, prompt, max_turns=self.judge_max_turns
                    )
                )
            val = getattr(res, "final_output", None)
            if not isinstance(val, dict):
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
            pred = self._predict_label_from_judge_output(val)
            agree = None if pred is None else (bool(pred) == label)
            if pred is None:
                score = 0.0
            else:
                if pred is True and label is True:
                    score = tp_reward
                elif pred is False and label is True:
                    score = max(0.0, 1.0 - fn_penalty)
                elif pred is True and label is False:
                    score = max(0.0, 1.0 - fp_penalty)
                else:
                    score = tn_reward

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

            outputs.append({"pred": pred, "score": score, "agree": agree, "kind": kind})
            scores.append(score)

            if capture_traces and trajectories is not None:
                trajectories.append(
                    {
                        "id": sid,
                        "label": label,
                        "pred": pred,
                        "agree": agree,
                        "kind": kind,
                        "prompt": prompt,
                        "source": self.source,
                        "judge_output": val,
                    }
                )

        class _EvalBatch:
            def __init__(self, outputs, scores, trajectories):
                self.outputs = outputs
                self.scores = scores
                self.trajectories = trajectories

        try:
            if self.debug_dir is not None:
                stage = None
                if capture_traces:
                    self.__debug_iter += 1
                    stage = "before"
                    self.__debug_last_batch_ids = list(batch_ids)
                else:
                    if (
                        self.__debug_last_batch_ids
                        and self.__debug_last_batch_ids == batch_ids
                    ):
                        stage = "after"
                    else:
                        stage = None
                if stage is not None:
                    kk = {"TP": 0, "TN": 0, "FP": 0, "FN": 0, "UNK": 0}
                    for o in outputs:
                        k = o.get("kind") or "UNK"
                        if k in kk:
                            kk[k] += 1
                        else:
                            kk["UNK"] += 1
                    mismatches = []
                    for i, o in enumerate(outputs):
                        k = o.get("kind")
                        if k in ("FP", "FN", "UNK"):
                            try:
                                mismatches.append(
                                    {
                                        "id": batch_ids[i],
                                        "label": bool(batch[i].get("label")),
                                        "pred": o.get("pred"),
                                        "kind": k,
                                        "name": batch[i].get("name"),
                                    }
                                )
                            except Exception:
                                pass
                        if len(mismatches) >= 5:
                            break
                    by_id = {}
                    for i, o in enumerate(outputs):
                        try:
                            by_id[batch_ids[i]] = {
                                "label": bool(batch[i].get("label")),
                                "pred": o.get("pred"),
                                "kind": o.get("kind"),
                            }
                        except Exception:
                            continue
                    info = {
                        "iteration": self.__debug_iter,
                        "stage": stage,
                        "source": self.source,
                        "subsample_ids": batch_ids,
                        "labels": {"pos": labels_true, "neg": labels_false},
                        "kinds": kk,
                        "scores_sum": float(sum(scores)),
                        "by_id": by_id,
                        "candidate_summary": {
                            "has_instructions": "instructions" in candidate
                            and isinstance(candidate.get("instructions"), str),
                            "has_template": "template" in candidate
                            and isinstance(candidate.get("template"), str),
                            "instructions_len": (
                                len(candidate.get("instructions", ""))
                                if isinstance(candidate.get("instructions"), str)
                                else 0
                            ),
                            "template_len": (
                                len(candidate.get("template", ""))
                                if isinstance(candidate.get("template"), str)
                                else 0
                            ),
                        },
                        "mismatches": mismatches,
                    }
                    try:
                        pdir = Path(self.debug_dir)
                        pdir.mkdir(parents=True, exist_ok=True)
                        fp = pdir / f"iter_{self.__debug_iter:04d}_{stage}.json"
                        fp.write_text(json.dumps(info, indent=2), encoding="utf-8")
                    except Exception:
                        pass
        except Exception:
            pass

        return _EvalBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def _get_tools_schema_md(self) -> str:
        if hasattr(self, "__tools_schema_md"):
            return getattr(self, "__tools_schema_md") or ""
        md = ""
        try:
            # Discover tool objects without building the agent
            base_dir = self.compiled_yaml.resolve().parent
            discovered: Dict[str, Any] = {}
            try:
                t1 = base_dir / "tools"
                if t1.is_dir():
                    discovered.update(discover_functions_in_dir(str(t1)))
            except Exception:
                pass
            try:
                t2 = base_dir / "project_tools"
                if t2.is_dir():
                    discovered.update(discover_functions_in_dir(str(t2)))
            except Exception:
                pass
            tools_cfg = (self._judge_cfg.get("agent") or {}).get("tools", [])
            tools: List[Any] = []
            if isinstance(tools_cfg, str) and tools_cfg.strip().lower() in {"*", "all"}:
                tools = list(discovered.values())
            elif isinstance(tools_cfg, list):
                for nm in tools_cfg:
                    if nm in discovered:
                        tools.append(discovered[nm])
            lines: List[str] = []
            for t in tools:
                try:
                    name = getattr(t, "name", None) or getattr(t, "__name__", "")
                    desc = getattr(t, "description", "")
                    schema = getattr(t, "params_json_schema", None) or {}
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

        return "\n\n".join(
            convert_sample_to_markdown(sample, i + 1) for i, sample in enumerate(items)
        )

    async def _ensure_optimizer_agent(self):
        if self._optimizer_agent is not None:
            return self._optimizer_agent
        ocfg = self._optimizer_cfg or {}
        agent_cfg = ocfg.get("agent") or {}
        from omniagents.core.agents.specs import AgentSpec
        from omniagents.core.agents.builder import _default_build_agent

        base_dir = self.compiled_yaml.resolve().parent
        instructions = agent_cfg.get("instructions") or ""
        if not instructions and agent_cfg.get("instructions_file"):
            instructions = self._read_text_file(
                base_dir, str(agent_cfg.get("instructions_file"))
            )
        if not instructions:
            instructions = "You are an optimizer. Return ONLY a fenced code block with the new text."
        model = agent_cfg.get("model") or "gpt-4.1"

        def _get_instr() -> str:
            return instructions

        spec = AgentSpec(
            name=agent_cfg.get("name") or f"Optimizer:{self.optimizer_key}",
            welcome_text="",
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

    @staticmethod
    def _extract_fenced(s: str) -> str:
        try:
            t = str(s)
            if t.count("```") >= 2:
                i = t.find("```")
                j = t.rfind("```")
                if 0 <= i < j:
                    inner = t[i + 3 : j].strip()
                    if inner.lower().startswith("json") or inner.lower().startswith(
                        "yaml"
                    ):
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

    def _default_instruction_prompt(
        self, current_doc: str, items: List[Dict[str, Any]]
    ) -> str:
        md = self._format_dataset_md(items)
        tools_md = self._get_tools_schema_md()
        name = (
            self._judge_meta.get("label")
            or self._judge_meta.get("name")
            or f"judge:{self.judge_key}"
        )
        desc = self._judge_meta.get("description") or ""
        sections: List[str] = []
        sections.append(
            "You are optimizing the LLM-as-Judge’s instructions to improve classification accuracy across scenarios."
        )
        info_parts: List[str] = []
        persona = [f"- Role: judge", f"- Name: {name}"]
        if desc:
            persona.append(f"- Description: {desc}")
        if self.category:
            persona.append(f"- Target Category: {self.category}")
        persona.append("- Input: a conversation trace between a user and an AI Agent")
        info_parts.append("\n".join(persona))
        info_parts.append("\n## Current Instructions\n```\n" + current_doc + "\n```")
        if tools_md:
            info_parts.append("\n## Available Tools (if any)\n" + tools_md)
        sections.append("# Judge Information\n" + "\n\n".join(info_parts))
        try:
            an = self._agent_meta.get("label") or self._agent_meta.get("name")
            ad = self._agent_meta.get("description") or ""
            agent_lines: List[str] = []
            if an:
                agent_lines.append(f"- Name: {an}")
            if ad:
                agent_lines.append(f"- Description: {ad}")
            if agent_lines:
                sections.append("# AI Agent Information\n" + "\n".join(agent_lines))
        except Exception:
            pass
        sections.append(
            "# Goal\nWrite improved, generalizable judge instructions that maximize agreement with labels without overfitting."
        )
        # Derive schema constraints for explicit reminder (if present)
        try:
            schema = (self._judge_cfg.get("agent") or {}).get("output_schema") or {}
            expect = None
            if isinstance(schema, dict):
                props = schema.get("properties") or {}
                if (
                    isinstance(props, dict)
                    and "reasoning" in props
                    and "answer" in props
                ):
                    ans_prop = props.get("answer") or {}
                    enum = ans_prop.get("enum") or []
                    if isinstance(enum, list) and enum:
                        expect = {
                            "keys": ["reasoning", "answer"],
                            "enum": [str(x) for x in enum],
                        }
        except Exception:
            expect = None  # type: ignore
        if expect:
            enum_list = ", ".join(expect["enum"])  # type: ignore[index]
            schema_block = (
                "# Output Schema (Do Not Change)\n"
                f"- The judge must return ONLY JSON with keys: reasoning (string), answer ({enum_list}).\n\n"
                "- Do not add, rename, or remove keys; do not change allowed values.\n\n"
                "- Example:\n\n"
                '```json\n{\n  "reasoning": "...",\n  "answer": "'
                + str(expect["enum"][0])
                + '"\n}\n```\n'
            )
            sections.append(schema_block)
        ctx_parts: List[str] = []
        ctx_parts.append(
            "The reflective examples below include task inputs, the judge’s outputs, and feedback indicating where predictions matched or mismatched labels. Use them to refine decision rules and edge-case handling without memorizing examples."
        )
        ctx_parts.append("\n```\n" + md + "\n```")
        sections.append("# Context\n" + "\n\n".join(ctx_parts))
        guidance = [
            "- Do: clarify decision rules; include edge-case handling and tie-breakers.",
            "- Don’t: rely on example-specific artifacts; avoid leaking labels or overfitting.",
            "- Tools: use tools only for verification (e.g., computation, parsing), not to bias outcomes.",
            "- Output format: return ONLY the new instructions in a single fenced code block. No commentary.",
        ]
        sections.append("# Guidance\n" + "\n".join(guidance))
        return "\n\n".join(sections)

    def _default_template_prompt(
        self, current_template: str, items: List[Dict[str, Any]]
    ) -> str:
        md = self._format_dataset_md(items)
        tools_md = self._get_tools_schema_md()
        cat_line = f"- Target Category: {self.category}\n" if self.category else ""
        prompt = (
            "You are optimizing the judge input template used to render evaluation inputs.\n\n"
            "# Judge Information\n"
            f"- Role: judge\n{cat_line}"
            "- Input: a conversation trace between a user and an AI Agent\n\n"
            "## Current Template\n```\n" + current_template + "\n```\n\n"
            "The following are example cases and judge outcomes with feedback:\n```\n"
            + md
            + "\n```\n\n"
        )
        if tools_md:
            prompt += (
                "Available tools schema (names, descriptions, parameters):\n"
                + tools_md
                + "\n\n"
            )
        try:
            an = self._agent_meta.get("label") or self._agent_meta.get("name")
            ad = self._agent_meta.get("description") or ""
            agent_lines: List[str] = []
            if an:
                agent_lines.append(f"- Name: {an}")
            if ad:
                agent_lines.append(f"- Description: {ad}")
            if agent_lines:
                prompt += "# AI Agent Information\n" + "\n".join(agent_lines) + "\n\n"
        except Exception:
            pass
        prompt += "Rewrite an improved input template for the judge. Preserve placeholders such as {{ text }}, {{ history_json }}, etc. Return only the new template in a fenced block."
        return prompt

    def _render_optimizer_prompt(
        self, component: str, current_text: str, items: List[Dict[str, Any]]
    ) -> str:
        ocfg = self._optimizer_cfg or {}
        proposals = ocfg.get("proposals") or {}
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
                    target_role="judge",
                    judge_name=self._judge_meta.get("name"),
                    judge_label=self._judge_meta.get("label"),
                    judge_description=self._judge_meta.get("description"),
                    category=self.category,
                    agent_name=self._agent_meta.get("name"),
                    agent_label=self._agent_meta.get("label"),
                    agent_description=self._agent_meta.get("description"),
                )
            return self._default_instruction_prompt(current_text, items)
        elif component == "template":
            tf = proposals.get("template_template_file")
            if tf:
                tmpl = self._read_text_file(base_dir, str(tf))
                dataset_md = self._format_dataset_md(items)
                tools_md = self._get_tools_schema_md()
                return render_template(
                    tmpl,
                    current_template=current_text,
                    dataset_md=dataset_md,
                    tools_schema_md=tools_md,
                    target_role="judge",
                    judge_name=self._judge_meta.get("name"),
                    judge_label=self._judge_meta.get("label"),
                    judge_description=self._judge_meta.get("description"),
                    category=self.category,
                    agent_name=self._agent_meta.get("name"),
                    agent_label=self._agent_meta.get("label"),
                    agent_description=self._agent_meta.get("description"),
                )
            return self._default_template_prompt(current_text, items)
        return self._default_instruction_prompt(current_text, items)

    def _propose_new_texts(
        self,
        candidate: Dict[str, str],
        reflective_dataset: Dict[str, List[Dict[str, Any]]],
        components_to_update: List[str],
    ) -> Dict[str, str]:
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
                        from omniagents.studio.tracing import trace as studio_trace  # type: ignore
                        import uuid as _uuid

                        run_uid = _uuid.uuid4().hex[:8]
                        meta = {
                            "omniagents": {
                                "run_type": "optimizer_proposal",
                                "scope": "judge",
                                "judge_key": self.judge_key,
                                "component": name,
                                "config": {"compiled_yaml": str(self.compiled_yaml)},
                                "run": {"id": run_uid},
                            }
                        }
                        with studio_trace(
                            self.workflow_name, group_id=run_uid, metadata=meta
                        ):
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
        kinds = {"TP": [], "TN": [], "FP": [], "FN": [], "UNK": []}
        for t in trajs:
            k = t.get("kind") or "UNK"
            if k not in kinds:
                k = "UNK"
            kinds[k].append(t)
        errs = kinds.get("FP", []) + kinds.get("FN", [])
        tp = kinds.get("TP", [])
        tn = kinds.get("TN", [])
        sample_tp = []
        sample_tn = []
        target = max(1, int(0.25 * max(1, len(errs))))
        if tp:
            sample_tp = tp[:target]
        if tn:
            sample_tn = tn[:target]
        selected = errs + sample_tp + sample_tn
        for comp in components_to_update:
            items: List[Dict[str, Any]] = []
            for t in selected:
                label = bool(t.get("label"))
                pred = t.get("pred")
                k = t.get("kind")
                prompt = t.get("prompt")
                jout = t.get("judge_output") or {}
                explanation = ""
                try:
                    explanation = (
                        (
                            jout.get("explanation")
                            or jout.get("reasoning")
                            or jout.get("rationale")
                            or ""
                        )
                        if isinstance(jout, dict)
                        else ""
                    )
                except Exception:
                    explanation = ""
                if k == "TP":
                    fb = "Correct Fail. Reinforce the decision rules and cues that led to this correct classification."
                elif k == "TN":
                    fb = "Correct Pass. Reinforce what evidence justifies passing and avoid triggering on weak cues."
                elif k == "FP":
                    fb = "False Positive. Reduce spurious failures by tightening criteria or requiring corroborating evidence."
                elif k == "FN":
                    fb = "False Negative. Add explicit rules and edge-case handling to detect these cases."
                else:
                    fb = "No decision. Clarify decision rule and required outputs."
                if comp == "template" and (k in ("FP", "FN")):
                    fb = (
                        fb
                        + " Ensure the template surfaces or de-emphasizes the right evidence to correct this."
                    )
                if explanation:
                    fb = f"{fb} Judge explanation: {explanation}"
                if comp == "instructions":
                    d = {
                        "Inputs": {"text": prompt},
                        "Generated Outputs": jout,
                        "Feedback": fb,
                    }
                elif comp == "template":
                    d = {
                        "Inputs": {
                            "current_template": candidate.get("template", ""),
                            "text": prompt,
                        },
                        "Generated Outputs": jout,
                        "Feedback": fb,
                    }
                else:
                    continue
                items.append(d)
            if items:
                reflective[comp] = items
        if not reflective:
            raise Exception("No reflective data constructed")
        return reflective
