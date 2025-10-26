import types

from omniagents.core.evaluation.context import build_eval_context, allowed_tool_names


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
            "output": '{"results": [{"value": 1}]}'
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
