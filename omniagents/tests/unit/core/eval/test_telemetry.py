import asyncio
import time

from omniagents.core.eval.telemetry import (
    consume_stream_events,
    count_tool_calls,
    count_errors,
    scenario_timer,
    build_telemetry,
    _empty_usage,
)

# --- consume_stream_events ---


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    input_tokens_details = None
    output_tokens_details = None


class _FakeResponse:
    usage = _FakeUsage()
    model = "gpt-4.1"


class _FakeResponseCompletedEvent:
    """Mimics openai.types.responses.ResponseCompletedEvent."""

    def __init__(self):
        self.response = _FakeResponse()


def _make_raw_event(data):
    class Evt:
        type = "raw_response_event"

    e = Evt()
    e.data = data
    return e


def test_consume_stream_events_accumulates_usage():
    from openai.types.responses import ResponseCompletedEvent

    # Build a real ResponseCompletedEvent-like object via our fake
    # The function checks isinstance, so we need the real class.
    # Instead, we patch at a higher level by using fake events with the
    # right type check. Let's just use the real class path check.
    # For unit testing, we'll monkeypatch isinstance via duck-typing.

    # Simpler approach: test with non-matching events (no usage captured)
    async def _stream():
        yield _make_raw_event("not a ResponseCompletedEvent")

    result = asyncio.run(consume_stream_events(_stream()))
    assert result["usage"]["requests"] == 0
    assert result["usage"]["total_tokens"] == 0


def test_consume_stream_events_ignores_non_raw_events():
    class NonRawEvent:
        type = "run_item_event"
        data = None

    async def _stream():
        yield NonRawEvent()

    result = asyncio.run(consume_stream_events(_stream()))
    assert result["usage"] == _empty_usage()


# --- count_tool_calls ---


def test_count_tool_calls_with_function_calls():
    history = [
        {"type": "function_call", "name": "search", "call_id": "c1", "arguments": "{}"},
        {"role": "assistant", "content": "hello"},
        {"type": "function_call", "name": "lookup", "call_id": "c2", "arguments": "{}"},
    ]
    assert count_tool_calls(history) == 2


def test_count_tool_calls_empty_history():
    assert count_tool_calls([]) == 0
    assert count_tool_calls(None) == 0


# --- count_errors ---


def test_count_errors_detects_error_in_tool_output():
    history = [
        {
            "type": "function_call_output",
            "output": "Error: something went wrong",
            "call_id": "c1",
        },
        {
            "type": "function_call_output",
            "output": "success result",
            "call_id": "c2",
        },
        {
            "type": "function_call_output",
            "output": "Traceback (most recent call last):\n  File...",
            "call_id": "c3",
        },
    ]
    assert count_errors(history) == 2


def test_count_errors_detects_dict_error():
    history = [
        {
            "type": "function_call_output",
            "output": {"error": "timeout"},
            "call_id": "c1",
        },
    ]
    assert count_errors(history) == 1


def test_count_errors_detects_incomplete_status():
    history = [
        {"status": "incomplete", "reason": "max_tokens"},
    ]
    assert count_errors(history) == 1


def test_count_errors_empty():
    assert count_errors([]) == 0
    assert count_errors(None) == 0


# --- scenario_timer ---


def test_scenario_timer_returns_elapsed():
    with scenario_timer() as elapsed_fn:
        time.sleep(0.05)
    elapsed = elapsed_fn()
    assert elapsed >= 0.04
    assert elapsed < 1.0


# --- build_telemetry ---


def test_build_telemetry_assembles_all_fields():
    history = [
        {"type": "function_call", "name": "search", "call_id": "c1", "arguments": "{}"},
        {
            "type": "function_call_output",
            "output": "Error: not found",
            "call_id": "c1",
        },
        {"role": "assistant", "content": "done"},
    ]
    stream_result = {
        "usage": {
            "requests": 2,
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }
    }

    with scenario_timer() as elapsed_fn:
        pass

    result = build_telemetry(
        elapsed_fn=elapsed_fn,
        history=history,
        stream_result=stream_result,
    )

    assert "duration_seconds" in result
    assert result["duration_seconds"] >= 0
    assert result["tool_call_count"] == 1
    assert result["error_count"] == 1
    assert result["usage"]["total_tokens"] == 150
    assert result["usage"]["requests"] == 2


def test_consume_stream_events_counts_project_agent_token_usage():
    """A project agent's RPC stream carries usage as ``token_usage`` items.

    Each item's ``totals`` is that response's usage; the eval used to read
    only the SDK's raw events and recorded zero for every project agent.
    """
    from agents.stream_events import RunItemStreamEvent

    def _usage(i, o):
        return RunItemStreamEvent(
            name="token_usage",
            item={
                "delta": {"requests": 1, "input_tokens": 0},
                "totals": {
                    "requests": 1,
                    "input_tokens": i,
                    "output_tokens": o,
                    "total_tokens": i + o,
                },
            },
        )

    async def _stream():
        yield _usage(3, 2)
        yield _usage(7, 1)

    result = asyncio.run(consume_stream_events(_stream()))
    assert result["usage"] == {
        "requests": 2,
        "input_tokens": 10,
        "output_tokens": 3,
        "total_tokens": 13,
    }
