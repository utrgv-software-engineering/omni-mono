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


@evaluation_measure
async def expectations(ctx) -> Dict[str, Any]:
    """The scenario's ``expect`` checks (``tool_called``, ``response_contains``, ``verify``, ...).

    See :mod:`omniagents.core.evaluation.expectations`. Passes when every
    check passes; ``checks`` lists each one.
    """
    from .expectations import check_expectations_async

    checks = await check_expectations_async(ctx, ctx.expect or {})
    failed = [c for c in checks if not c["passed"]]
    if failed:
        outcome = fail_reason("; ".join(f"{c['name']}: {c['reason']}" for c in failed))
    else:
        outcome = pass_reason(f"{len(checks)} expectation(s) met")
    outcome["checks"] = checks
    return outcome


@evaluation_measure
def user_goal_achieved(ctx) -> Dict[str, Any]:
    """A synthetic scenario's simulated user said its goal was achieved (``[DONE]``)."""
    reason = ctx.metadata.get("user_completion_reason")
    if reason == "done":
        return pass_reason("simulated user reached its goal")
    return fail_reason(
        f"simulated user did not reach its goal ({reason or 'max_exchanges'})"
    )
