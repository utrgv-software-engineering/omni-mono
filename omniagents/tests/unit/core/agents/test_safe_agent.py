import asyncio
import json

import pytest

from agents import Agent
from agents.tool import FunctionTool

from omniagents.core.agents.safe import SafeAgent, ToolsToFinalOutputResult
from omniagents.core.agents.safe import _global_safe_tool_names


@pytest.fixture(autouse=True)
def reset_globals():
    _global_safe_tool_names.set(set())
    yield
    _global_safe_tool_names.set(set())


async def fake_get_all_tools(self, ctx):
    return self.tools


@pytest.fixture(autouse=True)
def patch_agent_tools(monkeypatch):
    monkeypatch.setattr(Agent, "get_all_tools", fake_get_all_tools)


def make_tool(name, result):
    async def _invoke(_ctx, params_json):
        return json.dumps({"result": result, "params": params_json})

    return FunctionTool(
        name=name,
        description=name,
        params_json_schema={},
        on_invoke_tool=_invoke,
    )


def make_safe_agent(approval_callback):
    return SafeAgent(
        name="safe",
        tools=[make_tool("tool_one", "initial")],
        instructions="",
        model="gpt",
        approval_callback=approval_callback,
    )


@pytest.mark.asyncio
async def test_safe_agent_rejection_sets_halt():
    async def reject(_, __, ___):
        return False, False, "blocked"

    agent = make_safe_agent(reject)
    tools = await agent.get_all_tools(None)
    wrapped = tools[0]
    payload = await wrapped.on_invoke_tool(None, json.dumps({"x": 1}))
    decoded = json.loads(payload)
    assert decoded["error"] == "TOOL_REJECTED"
    result = await agent._custom_tool_use_behavior(None, [])
    assert isinstance(result, ToolsToFinalOutputResult)
    assert result.is_final_output is True
    assert result.final_output == ""


@pytest.mark.asyncio
async def test_safe_agent_always_approve_marks_tool_safe():
    calls = []

    async def approve(name, formatted, metadata):
        calls.append((name, formatted, metadata))
        return True, True, None

    agent = make_safe_agent(approve)
    tools = await agent.get_all_tools(None)
    wrapped = tools[0]
    response = await wrapped.on_invoke_tool(None, json.dumps({"x": 1}))
    decoded = json.loads(response)
    assert decoded["result"] == "initial"
    assert agent.is_tool_safe(wrapped) is True
    assert "tool_one" in _global_safe_tool_names.get()
    assert calls
