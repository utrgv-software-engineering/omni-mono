from __future__ import annotations

from typing import Dict, Any

from . import evaluation_measure, fail_reason, pass_reason


@evaluation_measure
def tool_hallucination(ctx) -> Dict[str, Any]:
    calls = ctx.tool_calls()
    allowed = set(getattr(ctx, "available_tools", set()) or set())
    unknown = []
    for call in calls:
        if call.name and call.name not in allowed:
            unknown.append(
                {
                    "index": call.index,
                    "name": call.name,
                    "arguments": call.args,
                    "call_id": call.call_id,
                }
            )
    total = len(calls)
    bad = len(unknown)
    result = {
        "name": "tool_hallucination",
        "counts": {"total_tool_calls": total, "unknown_tool_calls": bad},
        "unknown_tool_calls": unknown,
    }
    if bad == 0:
        outcome = pass_reason("all_tools_recognized")
    else:
        outcome = fail_reason("unknown_tool_calls")
    outcome.update(result)
    return outcome
