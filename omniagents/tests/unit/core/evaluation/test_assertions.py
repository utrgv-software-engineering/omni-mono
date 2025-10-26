import pytest

from omniagents.core.evaluation.assertions import (
    parse_tool_calls,
    parse_tool_outputs,
    link_outputs,
    assert_tool_called,
    assert_no_tool_called,
    assert_tool_call_sequence,
    assert_tool_output_satisfies,
    final_assistant_text,
    assert_args_subset,
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
    ok, info = assert_tool_call_sequence(sample_history, ["foo", "bar"], contiguous=True)
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
