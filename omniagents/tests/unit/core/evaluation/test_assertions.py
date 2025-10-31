import pytest

from omniagents.core.evaluation.assertions import (
    assert_args_subset,
    assert_assistant_contains,
    assert_coords_propagated,
    assert_no_tool_called,
    assert_tool_called,
    assert_tool_call_sequence,
    assert_tool_output_satisfies,
    final_assistant_text,
    link_outputs,
    parse_tool_calls,
    parse_tool_outputs,
)


@pytest.fixture
def sample_history():
    return [
        {
            "type": "function_call",
            "name": "foo",
            "arguments": '{"x": 1, "y": 2.0}',
            "call_id": "c1",
        },
        {
            "type": "custom_tool_call",
            "name": "bar",
            "input": {"z": 3},
            "call_id": "c2",
        },
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": '{"results": [{"value": "ok"}]}',
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "c2",
            "output": {"results": [{"value": "bar"}]},
        },
        {
            "role": "assistant",
            "type": "message",
            "content": [{"type": "output_text", "text": "Answer"}],
        },
    ]


def test_parse_and_link_tool_data(sample_history):
    calls = parse_tool_calls(sample_history)
    outs = parse_tool_outputs(sample_history)
    assert len(calls) == 2
    assert calls[0]["args"] == {"x": 1, "y": 2.0}
    assert len(outs) == 2
    assert outs[0]["output_json"]["results"][0]["value"] == "ok"
    links = link_outputs(calls, outs)
    assert "c1" in links and "c2" in links
    assert links["c2"]["output"]["output_json"]["results"][0]["value"] == "bar"


def test_tool_assertions(sample_history):
    ok, info = assert_tool_called(sample_history, "foo", args={"x": 1})
    assert ok and info["count"] == 1
    ok, info = assert_tool_call_sequence(
        sample_history, ["foo", "bar"], contiguous=True
    )
    assert ok
    ok, _ = assert_no_tool_called(sample_history, "missing")
    assert ok
    ok, output_info = assert_tool_output_satisfies(
        sample_history, "foo", lambda data: data["results"][0]["value"] == "ok"
    )
    assert ok and output_info["output"]["call_id"] == "c1"
    assert final_assistant_text(sample_history) == "Answer"


def test_assert_args_subset_numeric_tolerance():
    assert assert_args_subset({"value": 1.0000004}, {"value": 1})
    assert not assert_args_subset({"value": 2}, {"value": 1})


def test_assert_tool_called_respects_window(sample_history):
    ok, info = assert_tool_called(sample_history, "foo", after=0)
    assert not ok
    assert info["count"] == 0


def test_assert_tool_call_sequence_requires_contiguous_order():
    history = [
        {"type": "function_call", "name": "foo", "arguments": "{}", "call_id": "a"},
        {"type": "function_call", "name": "baz", "arguments": "{}", "call_id": "b"},
        {"type": "function_call", "name": "bar", "arguments": "{}", "call_id": "c"},
    ]
    ok, info = assert_tool_call_sequence(history, ["foo", "bar"], contiguous=True)
    assert not ok
    assert info["reason"] == "not_contiguous"


def test_assert_tool_output_satisfies_handles_missing_output(sample_history):
    history = sample_history[:-2]
    ok, info = assert_tool_output_satisfies(history, "bar", lambda data: True)
    assert not ok
    assert info["reason"] == "no_output"


def test_assert_tool_output_satisfies_handles_predicate_failure(sample_history):
    ok, info = assert_tool_output_satisfies(sample_history, "foo", lambda data: False)
    assert not ok
    assert info["output"]["call_id"] == "c1"


def test_assert_coords_propagated_success():
    history = [
        {
            "type": "function_call",
            "name": "geo_lookup",
            "arguments": "{}",
            "call_id": "c1",
        },
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": {"results": [{"latitude": 1.0, "longitude": 2.0}]},
        },
        {
            "type": "function_call",
            "name": "weather_fetch",
            "arguments": {"latitude": 1.0, "longitude": 2.0},
            "call_id": "c2",
        },
    ]
    ok, info = assert_coords_propagated(history, "geo_lookup", "weather_fetch")
    assert ok
    assert info["from"] == [1.0, 2.0]


def test_assert_coords_propagated_mismatch():
    history = [
        {
            "type": "function_call",
            "name": "geo_lookup",
            "arguments": "{}",
            "call_id": "c1",
        },
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": {"results": [{"latitude": 1.0, "longitude": 2.0}]},
        },
        {
            "type": "function_call",
            "name": "weather_fetch",
            "arguments": {"latitude": 5.0, "longitude": 2.0},
            "call_id": "c2",
        },
    ]
    ok, info = assert_coords_propagated(history, "geo_lookup", "weather_fetch")
    assert not ok
    assert info["reason"] == "coords_mismatch"


def test_assert_assistant_contains_positive_and_negative(sample_history):
    ok, _ = assert_assistant_contains(sample_history, must_include=["answer"])
    assert ok
    ok2, details = assert_assistant_contains(sample_history, must_not_include=["wrong"])
    assert ok2
    failure, info = assert_assistant_contains(
        sample_history, must_not_include=["answer"]
    )
    assert not failure
    assert info["reason"] == "forbidden"


def test_parse_tool_calls_respects_include_types_and_literal_eval():
    history = [
        {
            "type": "function_call",
            "name": "alpha",
            "arguments": "{'a': 1}",
            "call_id": "x",
        },
        {"type": "custom_tool_call", "name": "beta", "input": "{}", "call_id": "y"},
        "not a dict",
    ]
    calls = parse_tool_calls(history, include_types=["function_call"])
    assert len(calls) == 1
    assert calls[0]["args"] == {"a": 1}


def test_parse_tool_outputs_handles_unparsable_values():
    history = [
        {"type": "function_call_output", "call_id": "c1", "output": "not-json"},
    ]
    outs = parse_tool_outputs(history)
    assert outs[0]["output_json"] is None


def test_assert_tool_call_sequence_unordered_mode():
    history = [
        {"type": "function_call", "name": "first", "arguments": "{}", "call_id": "a"},
        {"type": "function_call", "name": "second", "arguments": "{}", "call_id": "b"},
    ]
    ok, info = assert_tool_call_sequence(history, ["second", "first"], ordered=False)
    assert ok
    assert len(info["calls"]) == 2


def test_assert_tool_output_satisfies_for_specific_call():
    history = [
        {
            "type": "function_call",
            "name": "foo",
            "arguments": "{}",
            "call_id": "c1",
        },
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": {"results": [{"value": "ok"}]},
        },
    ]
    call = parse_tool_calls(history)[0]

    def broken(_):
        raise RuntimeError("boom")

    ok, info = assert_tool_output_satisfies(history, "foo", broken, for_call=call)
    assert not ok
    assert info["output"]["call_id"] == "c1"


def test_assert_coords_propagated_failure_conditions():
    ok, info = assert_coords_propagated([], "a", "b")
    assert not ok and info["reason"] == "no_source_call"

    history = [
        {"type": "function_call", "name": "a", "arguments": "{}", "call_id": "c1"},
        {"type": "function_call_output", "call_id": "c1", "output": "[]"},
    ]
    ok2, info2 = assert_coords_propagated(history, "a", "b")
    assert not ok2 and info2["reason"] == "invalid_source_coords"

    history2 = [
        {
            "type": "function_call",
            "name": "a",
            "arguments": "{}",
            "call_id": "c1",
        },
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": {"results": [{"latitude": 1, "longitude": 2}]},
        },
    ]
    ok3, info3 = assert_coords_propagated(history2, "a", "b")
    assert not ok3 and info3["reason"] == "no_dest_call"

    history3 = history2 + [
        {
            "type": "function_call",
            "name": "b",
            "arguments": {"latitude": "bad", "longitude": 2},
            "call_id": "c2",
        }
    ]
    ok4, info4 = assert_coords_propagated(history3, "a", "b")
    assert not ok4 and info4["reason"] == "invalid_dest_coords"


def test_assert_assistant_contains_missing_requirement(sample_history):
    ok, info = assert_assistant_contains(sample_history, must_include=["unknown"])
    assert not ok
    assert info["reason"] == "missing"


def test_final_assistant_text_with_string_content():
    history = [
        {"role": "assistant", "content": " hello "},
        {"role": "user", "content": "no"},
    ]
    assert final_assistant_text(history) == "hello"
