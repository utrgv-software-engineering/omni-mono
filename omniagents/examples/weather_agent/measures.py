from __future__ import annotations

from typing import Dict, Any, List

from omniagents.core.evaluation import (
    evaluation_measure,
    parse_tool_calls,
)


@evaluation_measure
def tool_hallucination(ctx) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = getattr(ctx, "history", []) or []
    calls = parse_tool_calls(history)
    allowed = set(getattr(ctx, "available_tools", set()) or set())
    unknown = []
    for c in calls:
        n = c.get("name")
        if n and n not in allowed:
            unknown.append(
                {
                    "index": c.get("index"),
                    "name": n,
                    "arguments": c.get("args"),
                    "call_id": c.get("call_id"),
                }
            )
    total = len(calls)
    bad = len(unknown)
    return {
        "name": "tool_hallucination",
        "passed": bad == 0,
        "counts": {"total_tool_calls": total, "unknown_tool_calls": bad},
        "unknown_tool_calls": unknown,
    }



@evaluation_measure
def out_of_scope_judge_agent(ctx) -> Dict[str, Any]:
    meta = getattr(ctx, "metadata", {}) or {}
    judge = meta.get("out_of_scope_judge")
    if not isinstance(judge, dict):
        return {"name": "out_of_scope_judge_agent", "passed": False, "reason": "no_judge_result"}
    should_decline = bool(judge.get("should_decline", False))
    return {"name": "out_of_scope_judge_agent", "passed": should_decline, "judge": judge}
