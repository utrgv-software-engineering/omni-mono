import types

from omniagents.core.evaluation.context import (
    JudgeResult,
    Message,
    TimelineEntry,
    ToolCall,
    ToolLink,
    ToolOutput,
    allowed_tool_names,
    build_eval_context,
    extract_tool_calls,
    fail_reason,
    pass_reason,
)


def sample_tool():
    return None


def test_build_eval_context_links_calls_and_messages():
    history = [
        {"role": "user", "content": " hi "},
        {
            "type": "function_call",
            "name": "lookup",
            "arguments": '{"term": "value"}',
            "call_id": "call-1",
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"results": [{"value": 1}]}',
        },
        {
            "role": "assistant",
            "type": "message",
            "content": [{"type": "output_text", "text": " done "}],
        },
    ]
    agent = types.SimpleNamespace(
        tools=[
            types.SimpleNamespace(name="lookup"),
            types.SimpleNamespace(func=sample_tool),
        ]
    )
    metadata = {"quality": {"answer": "pass", "reasoning": "ok"}}
    ctx = build_eval_context(agent=agent, history=history, metadata=metadata)

    call = ctx.tool_calls("lookup")[0]
    assert call.args == {"term": "value"}
    assert call.has_results()
    output = ctx.tool_results("lookup")[0]
    assert output.results == [{"value": 1}]
    assert ctx.linked_tool_result(call).results == [{"value": 1}]
    assert ctx.tool_links()[0].call is call
    assert ctx.messages[0].text == "hi"
    assert ctx.assistant_messages[-1].text == "done"
    assert ctx.user_messages[0].text == "hi"
    assert ctx.final_assistant_message.text == "done"
    judge = ctx.judge_result("quality")
    assert judge is not None and judge.passed
    assert allowed_tool_names(agent) == {"lookup", "sample_tool"}


def test_allowed_tool_names_prefers_explicit_tools():
    def external_tool():
        return None

    tools = [types.SimpleNamespace(func=external_tool)]
    names = allowed_tool_names(None, tools=tools)
    assert names == {"external_tool"}


def test_eval_context_tool_helpers_and_missing_links():
    history = [
        {"type": "function_call", "name": "lookup", "arguments": "{}", "call_id": "a"},
        {"type": "function_call", "name": "lookup", "arguments": "{}", "call_id": "b"},
    ]
    ctx = build_eval_context(history=history)
    assert ctx.first_tool("lookup").call_id == "a"
    assert ctx.latest_tool("lookup").call_id == "b"
    assert ctx.linked_tool_result({"call_id": "missing"}) is None


def test_timeline_entry_ordering_and_indexing():
    entry = TimelineEntry(index=5)
    assert entry.after({"index": 2})
    assert not entry.after({"index": 10})
    assert entry.before(TimelineEntry(index=7))
    assert not entry.before(None)


def test_toolcall_and_output_helpers():
    output = ToolOutput(index=1, call_id="c1", output_json={"results": [1, 2]})
    call = ToolCall(index=0, name="tool", output=output)
    link = ToolLink(call=call, output=output)
    assert call.results == [1, 2]
    assert call.has_results()
    assert call.has_multiple_results()
    assert link.results == [1, 2]
    empty_output = ToolOutput(index=2, call_id="c2", output_json="not dict")
    empty_call = ToolCall(index=3, name="tool", output=empty_output)
    assert empty_call.results is None
    assert empty_call.results_count == 0
    assert not empty_call.has_results()


def test_message_dataclass_strips_text():
    msg = Message(index=1, role="assistant", content=" hi ", text="hi")
    assert msg.role == "assistant"


def test_judge_result_conversion_and_reason_helpers():
    judge = JudgeResult(answer="PASS", reasoning="solid", raw={})
    assert judge.passed
    assert pass_reason("ok", extra=1) == {"passed": True, "reason": "ok", "extra": 1}
    assert fail_reason("bad", code=500) == {
        "passed": False,
        "reason": "bad",
        "code": 500,
    }
    assert judge.to_result() == {"passed": True, "reason": "solid"}


def test_judge_result_handles_invalid_metadata():
    ctx = build_eval_context(metadata={"quality": {"answer": 1, "reasoning": "text"}})
    assert ctx.judge_result("missing") is None
    assert ctx.judge_result("quality") is None


def test_extract_tool_calls_filters_relevant_entries():
    history = [
        {"name": "search", "type": "function_call", "arguments": "{}", "call_id": "a"},
        {"role": "assistant", "content": "hi"},
        {"name": "ignored", "type": "message"},
    ]
    calls = extract_tool_calls(history)
    assert calls == [
        {
            "index": 0,
            "name": "search",
            "arguments": "{}",
            "call_id": "a",
            "raw": history[0],
        }
    ]
