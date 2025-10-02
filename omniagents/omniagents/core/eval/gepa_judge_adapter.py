from __future__ import annotations

import json
from dataclasses import dataclass
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from omniagents.core.utils.jinja import render_template


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
        source: str = "final_assistant_text",
        judge_max_turns: int = 1,
    ) -> None:
        self.compiled_yaml = Path(compiled_yaml).resolve()
        self.judge_key = str(judge_key)
        self.runs_file = Path(runs_file).resolve()
        self.source = str(source)
        self.judge_max_turns = int(judge_max_turns)

        self._cfg = yaml.safe_load(self.compiled_yaml.read_text(encoding="utf-8")) or {}
        self._eval_cfg = (self._cfg.get("evaluation") or {})
        self._judge_cfg = (self._eval_cfg.get("judges") or {}).get(self.judge_key) or {}
        runs_blob = json.loads(self.runs_file.read_text(encoding="utf-8"))
        runs_list = list(runs_blob.get("runs") or [])
        self._runs_by_id: Dict[str, Dict[str, Any]] = {str(r.get("scenario_id")): r for r in runs_list}

        self.propose_new_texts = None

    @staticmethod
    def _predict_label_from_judge_output(val: Any) -> Optional[bool]:
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

    def _build_judge_agent(self, instructions: str) -> Any:
        from omniagents.core.agents.specs import AgentSpec
        from omniagents.core.agents.builder import _default_build_agent
        agent_cfg = (self._judge_cfg.get("agent") or {})
        model = agent_cfg.get("model") or "gpt-4o-mini"
        def _get_instr() -> str:
            return instructions
        # Derive output typing similar to judges.py
        def _map_js_type(t: str):
            t = (t or "").lower()
            return {"string": str, "boolean": bool, "bool": bool, "number": float, "float": float, "integer": int, "int": int, "object": dict, "array": list}.get(t, str)
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
                props = (agent_cfg.get("output_schema") or {}).get("properties", {}) or {}
                ann = {k: _map_js_type((v or {}).get("type")) for k, v in props.items()}
                out_type = TypedDict("JudgeOutput", ann)  # type: ignore
            except Exception:
                pass
        spec = AgentSpec(
            name=self._judge_cfg.get("agent", {}).get("name") or f"Judge:{self.judge_key}",
            header_title=self._judge_cfg.get("agent", {}).get("name") or f"Judge:{self.judge_key}",
            page_title=self._judge_cfg.get("agent", {}).get("name") or f"Judge:{self.judge_key}",
            welcome_text="",
            client_storage_prefix=f"judge_{self.judge_key}.",
            get_agent_instructions=_get_instr,
            build_agent=None,
            available_tools=[],
            model_name=model,
            output_type=out_type,
            output_type_strict=output_type_strict,
        )
        return _default_build_agent({}, None, spec, None)

    def evaluate(
        self,
        batch: List[Dict[str, Any]],
        candidate: Dict[str, str],
        capture_traces: bool = False,
    ):
        from agents import Runner as AgentsRunner

        template = str(candidate.get("template") or "{{ text }}")
        instructions = str(candidate.get("instructions") or "You are an evaluation agent. Return ONLY JSON.")

        # Build judge agent once per evaluation
        judge_agent = None
        try:
            judge_agent = asyncio.run(self._build_judge_agent(instructions))  # type: ignore
        except RuntimeError:
            import asyncio as _asyncio
            judge_agent = _asyncio.get_event_loop().run_until_complete(self._build_judge_agent(instructions))

        outputs: List[Dict[str, Any]] = []
        scores: List[float] = []
        trajectories: List[Dict[str, Any]] | None = [] if capture_traces else None

        for item in batch:
            sid = str(item.get("id"))
            label = bool(item.get("label"))
            run = self._runs_by_id.get(sid) or {}
            if self.source == "history":
                text = json.dumps(run.get("history") or [])
            else:
                text = run.get("final_assistant_text") or ""
            prompt = render_template(template, text=text, history=(run.get("history") or []), history_json=json.dumps(run.get("history") or []))

            try:
                res = asyncio.run(AgentsRunner.run(judge_agent, prompt, max_turns=self.judge_max_turns))  # type: ignore
            except RuntimeError:
                import asyncio as _asyncio
                res = _asyncio.get_event_loop().run_until_complete(AgentsRunner.run(judge_agent, prompt, max_turns=self.judge_max_turns))
            val = getattr(res, "final_output", None)
            if not isinstance(val, dict):
                s = str(val)
                try:
                    t = s.strip()
                    if t.startswith("```"):
                        t = t.strip('`')
                        if t.lower().startswith("json"):
                            t = t[4:]
                        t = t.strip()
                    if not (t.startswith("{") and t.endswith("}")):
                        i = t.find("{"); j = t.rfind("}")
                        if i != -1 and j != -1 and j > i:
                            t = t[i:j+1]
                    val = json.loads(t)
                except Exception:
                    val = {"raw": s}
            pred = self._predict_label_from_judge_output(val)
            agree = None if pred is None else (bool(pred) == label)
            score = 1.0 if agree is True else 0.0

            outputs.append({"pred": pred, "score": score, "agree": agree})
            scores.append(score)

            if capture_traces and trajectories is not None:
                trajectories.append({
                    "id": sid,
                    "label": label,
                    "pred": pred,
                    "agree": agree,
                    "prompt": prompt,
                    "source": self.source,
                    "judge_output": val,
                })

        class _EvalBatch:
            def __init__(self, outputs, scores, trajectories):
                self.outputs = outputs
                self.scores = scores
                self.trajectories = trajectories

        return _EvalBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self,
        candidate: Dict[str, str],
        eval_batch: Any,
        components_to_update: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        reflective: Dict[str, List[Dict[str, Any]]] = {}
        trajs = list(eval_batch.trajectories or [])
        for comp in components_to_update:
            items: List[Dict[str, Any]] = []
            for t in trajs:
                label = bool(t.get("label"))
                pred = t.get("pred")
                agree = t.get("agree")
                prompt = t.get("prompt")
                jout = t.get("judge_output") or {}
                explanation = ""
                try:
                    explanation = (jout.get("explanation") or jout.get("reasoning") or jout.get("rationale") or "") if isinstance(jout, dict) else ""
                except Exception:
                    explanation = ""
                if agree is True:
                    fb = "Prediction matched label. Capture and reinforce useful patterns and phrasing."
                else:
                    if pred is None:
                        fb = "Judge did not resolve to a label. Clarify decision rule and required outputs."
                    else:
                        kind = "False Positive" if (pred is True and label is False) else "False Negative"
                        fb = f"{kind}. Improve discrimination for this case."
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
                        "Inputs": {"current_template": candidate.get("template", ""), "text": prompt},
                        "Generated Outputs": jout,
                        "Feedback": fb + " Ensure the rendered input provides the right evidence and constraints for correct classification.",
                    }
                else:
                    continue
                items.append(d)
            if items:
                reflective[comp] = items
        if not reflective:
            raise Exception("No reflective data constructed")
        return reflective
