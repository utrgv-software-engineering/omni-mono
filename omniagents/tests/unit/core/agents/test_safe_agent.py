import asyncio
import json
from types import SimpleNamespace

import pytest

from agents import Agent
from agents.tool import FunctionTool

from omniagents.core.agents.safe import (
    SafeAgent,
    ToolsToFinalOutputResult,
    _global_safe_tool_names,
    _global_safe_tool_patterns,
)
from omniagents.core.tools import discovery as tools_discovery


@pytest.fixture(autouse=True)
def reset_globals():
    token_names = _global_safe_tool_names.set(set())
    token_patterns = _global_safe_tool_patterns.set([])
    try:
        yield
    finally:
        _global_safe_tool_names.reset(token_names)
        _global_safe_tool_patterns.reset(token_patterns)


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


def make_safe_agent(approval_callback, **kwargs):
    return SafeAgent(
        name="safe",
        tools=[make_tool("tool_one", "initial")],
        instructions="",
        model="gpt",
        approval_callback=approval_callback,
        **kwargs,
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


def test_is_tool_safe_with_patterns_and_globals():
    agent = SafeAgent(
        name="safe",
        tools=[make_tool("primary", "ok")],
        instructions="",
        model="gpt",
        approval_callback=SafeAgent.auto_approval_callback,
        safe_tool_names=["primary"],
        safe_tool_patterns=[r"safe_.*"],
    )
    SafeAgent.set_global_safe_tools(
        tool_names=["shared_tool"], tool_patterns=[r"global_.*"]
    )
    assert agent.is_tool_safe(make_tool("primary", "ok")) is True
    assert agent.is_tool_safe(make_tool("safe_read", "ok")) is True
    assert agent.is_tool_safe(make_tool("shared_tool", "ok")) is True
    assert agent.is_tool_safe(make_tool("global_edit", "ok")) is True
    assert agent.is_tool_safe(make_tool("random", "ok")) is False


def test_safe_tool_management_api():
    agent = SafeAgent(
        name="safe",
        tools=[make_tool("alpha", "ok")],
        instructions="",
        model="gpt",
        approval_callback=SafeAgent.auto_approval_callback,
    )
    agent.add_safe_tool("alpha")
    agent.add_safe_tool_pattern(r"beta_.*")
    assert agent.is_tool_safe(make_tool("alpha", "ok")) is True
    assert agent.is_tool_safe(make_tool("beta_read", "ok")) is True
    assert agent.remove_safe_tool("alpha") is True
    assert agent.is_tool_safe(make_tool("alpha", "ok")) is False
    agent.clear_safe_tools()
    assert agent.is_tool_safe(make_tool("beta_read", "ok")) is False


def test_add_safe_tool_pattern_rejects_invalid_regex():
    agent = SafeAgent(
        name="safe",
        tools=[make_tool("alpha", "ok")],
        instructions="",
        model="gpt",
        approval_callback=SafeAgent.auto_approval_callback,
    )
    before = len(agent._safe_tool_patterns)
    agent.add_safe_tool_pattern(r"[unclosed")
    after = len(agent._safe_tool_patterns)
    assert before == after


def test_set_and_add_global_safe_tools():
    SafeAgent.set_global_safe_tools(tool_names=["one"], tool_patterns=[r"two_.*"])
    SafeAgent.add_global_safe_tool("three")
    names = _global_safe_tool_names.get()
    patterns = _global_safe_tool_patterns.get()
    assert names == {"one", "three"}
    assert any(p.pattern == r"two_.*" for p in patterns)


def test_compute_approval_metadata(monkeypatch):
    monkeypatch.setattr(tools_discovery, "_approval_metadata_registry", {})

    @tools_discovery.approval_metadata()
    def sample_tool_approval(path: str):
        return {"preview": path}

    agent = SafeAgent(
        name="safe",
        tools=[make_tool("sample_tool", "ok")],
        instructions="",
        model="gpt",
        approval_callback=SafeAgent.auto_approval_callback,
    )
    metadata = agent._compute_approval_metadata("sample_tool", {"path": "file.txt"})
    assert metadata == {"preview": "file.txt"}


@pytest.mark.asyncio
async def test_custom_tool_use_behavior_run_llm_again():
    agent = SafeAgent(
        name="safe",
        tools=[make_tool("alpha", "ok")],
        instructions="",
        model="gpt",
        approval_callback=SafeAgent.auto_approval_callback,
    )
    result = await agent._custom_tool_use_behavior(
        None, [SimpleNamespace(output="data")]
    )
    assert result.is_final_output is False
    assert result.final_output is None


@pytest.mark.asyncio
async def test_custom_tool_use_behavior_stop_on_first_tool():
    agent = SafeAgent(
        name="safe",
        tools=[make_tool("alpha", "ok")],
        instructions="",
        model="gpt",
        approval_callback=SafeAgent.auto_approval_callback,
        tool_use_behavior="stop_on_first_tool",
    )
    await agent.get_all_tools(None)
    output = "payload"
    result = await agent._custom_tool_use_behavior(
        None, [SimpleNamespace(output=output)]
    )
    assert result.is_final_output is True
    assert result.final_output == output


@pytest.mark.asyncio
async def test_custom_tool_use_behavior_stop_at_tools_dict():
    behavior = {"stop_at_tool_names": ["alpha"]}
    agent = SafeAgent(
        name="safe",
        tools=[make_tool("alpha", "ok")],
        instructions="",
        model="gpt",
        approval_callback=SafeAgent.auto_approval_callback,
        tool_use_behavior=behavior,
    )
    result = await agent._custom_tool_use_behavior(
        None, [SimpleNamespace(name="alpha", output="data")]
    )
    assert result.is_final_output is True
    assert result.final_output == "data"
