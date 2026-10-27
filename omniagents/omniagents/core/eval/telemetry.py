"""Telemetry helpers for evaluation runs.

Captures per-scenario timing, token usage, tool-call counts, and error counts
from the OpenAI agent streaming events.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, AsyncIterator, Dict, List


def _empty_usage() -> Dict[str, Any]:
    """Return a zeroed-out usage dict matching the bridge.py shape."""
    return {
        "requests": 0,
        "input_tokens": 0,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }


async def consume_stream_events(stream: AsyncIterator) -> Dict[str, Any]:
    """Consume stream events and accumulate token usage.

    Two shapes carry usage: the SDK's own stream (``raw_response_event``
    with a ``ResponseCompletedEvent``, what ``Runner.run_streamed`` yields)
    and a project agent's RPC stream, where the runtime bridge has already
    turned each completed response into a ``token_usage`` item. The bridge's
    ``totals`` there are that response's own ``usage`` (it treats them as a
    running context figure, so its ``delta`` is context growth, not spend);
    summing ``totals`` per response gives the same count as the SDK stream.

    Returns a dict with ``usage`` (token counters).
    """
    from openai.types.responses import ResponseCompletedEvent

    usage = _empty_usage()

    async for event in stream:
        etype = getattr(event, "type", None)
        if etype == "run_item_stream_event" and getattr(event, "name", None) == (
            "token_usage"
        ):
            item = getattr(event, "item", None)
            totals = item.get("totals") if isinstance(item, dict) else None
            if isinstance(totals, dict):
                _add(usage, totals)
            continue
        if etype != "raw_response_event":
            continue
        data = getattr(event, "data", None)
        if not isinstance(data, ResponseCompletedEvent):
            continue
        response = data.response
        if not response or not response.usage:
            continue

        _add(usage, response.usage)

    return {"usage": usage}


def _add(usage: Dict[str, Any], response_usage: Any) -> None:
    """One response's usage (the SDK's object or the bridge's dict) into ``usage``."""
    from omniagents.core.usage import response_usage as flat

    tokens = flat(response_usage)
    usage["requests"] += 1
    for key, value in tokens.items():
        usage[key] += value
    total = (
        response_usage.get("total_tokens")
        if isinstance(response_usage, dict)
        else getattr(response_usage, "total_tokens", None)
    )
    usage["total_tokens"] += int(
        total or tokens["input_tokens"] + tokens["output_tokens"]
    )


def count_tool_calls(history: List[Dict[str, Any]]) -> int:
    """Count tool/function calls in the conversation history."""
    from omniagents.core.evaluation.assertions import parse_tool_calls

    return len(parse_tool_calls(history))


def count_errors(history: List[Dict[str, Any]]) -> int:
    """Count error indicators in the conversation history.

    Looks for tool outputs that signal an error (exception traces, error
    dicts, or the ``status: "incomplete"`` pattern).
    """
    errors = 0
    for item in history or []:
        if not isinstance(item, dict):
            continue

        # Tool output with explicit error content
        item_type = item.get("type", "")
        if item_type in ("function_call_output",):
            output = item.get("output", "")
            if isinstance(output, str) and (
                "error" in output.lower()[:80]
                or "exception" in output.lower()[:80]
                or "traceback" in output.lower()[:80]
            ):
                errors += 1
            elif isinstance(output, dict) and output.get("error"):
                errors += 1

        # Incomplete response status
        if item.get("status") == "incomplete":
            errors += 1

    return errors


@contextmanager
def scenario_timer():
    """Context manager that yields a callable returning elapsed seconds."""
    start = time.monotonic()
    elapsed: Dict[str, float] = {}

    def get_elapsed() -> float:
        return elapsed.get("value", time.monotonic() - start)

    try:
        yield get_elapsed
    finally:
        elapsed["value"] = round(time.monotonic() - start, 3)


def build_telemetry(
    *,
    elapsed_fn,
    history: List[Dict[str, Any]],
    stream_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the telemetry dict to merge into a run result.

    Args:
        elapsed_fn: Callable from ``scenario_timer`` returning seconds.
        history: Conversation history.
        stream_result: Dict returned by ``consume_stream_events``.

    Returns:
        Dict with ``duration_seconds``, ``tool_call_count``, ``usage``,
        and ``error_count``.
    """
    return {
        "duration_seconds": elapsed_fn(),
        "tool_call_count": count_tool_calls(history),
        "usage": stream_result.get("usage", _empty_usage()),
        "error_count": count_errors(history),
    }
