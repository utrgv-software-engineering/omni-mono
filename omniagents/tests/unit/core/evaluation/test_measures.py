from types import SimpleNamespace

from omniagents.core.evaluation.context import ToolCall
from omniagents.core.evaluation.measures import tool_hallucination


class DummyCtx:
    def __init__(self, calls, names):
        self._calls = calls
        self.available_tools = names

    def tool_calls(self):
        return self._calls


def test_tool_hallucination_flags_unknown_tools():
    known_call = ToolCall(index=0, name="known", args={}, call_id="c1")
    unknown_call = ToolCall(index=1, name="unknown", args={}, call_id="c2")
    ctx = DummyCtx([known_call, unknown_call], {"known"})
    result = tool_hallucination(ctx)
    assert result["passed"] is False
    assert result["counts"]["total_tool_calls"] == 2
    assert result["counts"]["unknown_tool_calls"] == 1
    assert result["unknown_tool_calls"][0]["name"] == "unknown"


def test_tool_hallucination_passes_when_all_tools_known():
    call = ToolCall(index=0, name="known", args={}, call_id="c1")
    ctx = DummyCtx([call], {"known"})
    result = tool_hallucination(ctx)
    assert result["passed"] is True
    assert result["counts"]["unknown_tool_calls"] == 0
