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
    ) -> None:
        self.compiled_yaml = Path(compiled_yaml).resolve()
        self.category = str(category)
        self.agent_max_turns = int(agent_max_turns) if agent_max_turns is not None else None
        self.score_mode = score_mode

        cfg = yaml.safe_load(self.compiled_yaml.read_text(encoding="utf-8")) or {}
        eval_cfg = (cfg.get("evaluation") or {})
        cmap = _failure_category_map(eval_cfg)
        self.category_measures: List[str] = list(cmap.get(self.category, []) or [])

        _load_local_measures(self.compiled_yaml)

        self.propose_new_texts = None

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
                    outs.append({"score": 0.0, "error": "missing_prompt"})
                    continue
                try:
                    res = await self._run_once(instructions, prompt)
                except Exception as e:
                    outs.append({"score": 0.0, "error": f"run_failed:{e}"})
                    continue
                measures = list(res.get("measures") or [])
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

    def make_reflective_dataset(
        self,
        candidate: Dict[str, str],
        eval_batch: Any,
        components_to_update: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        reflective: Dict[str, List[Dict[str, Any]]] = {}
        trajs = list(eval_batch.trajectories or [])
        for comp in components_to_update:
            if comp != "instructions":
                continue
            items: List[Dict[str, Any]] = []
            for t in trajs:
                fails = list(t.get("failures") or [])
                if not fails:
                    fb = "All measures for the category passed. Reinforce useful phrasing and constraints."
                else:
                    reasons = []
                    for f in fails:
                        nm = f.get("name")
                        rsn = f.get("reason")
                        if rsn:
                            reasons.append(f"{nm}: {rsn}")
                        else:
                            reasons.append(str(nm))
                    fb = "Some category measures failed. Improve guidance to address: " + ", ".join(reasons)
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
