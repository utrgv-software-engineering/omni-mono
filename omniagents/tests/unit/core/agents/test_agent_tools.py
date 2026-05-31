"""Tests for agent-as-tool wiring (Agent.as_tool() integration)."""

from pathlib import Path

import pytest

from omniagents.core.agents.builder import (
    _build_agent_tools_from_configs,
    _default_build_agent,
)
from omniagents.core.agents.specs import AgentSpec, AgentToolConfig


def make_spec(**overrides):
    base = {
        "name": "agent",
        "welcome_text": "welcome",
        "get_agent_instructions": lambda: "",
        "build_agent": overrides.pop("build_agent", None),
    }
    base.update(overrides)
    return AgentSpec(**base)


def test_agent_tool_config_validates_xor():
    with pytest.raises(ValueError):
        AgentToolConfig()
    with pytest.raises(ValueError):
        AgentToolConfig(yaml_path="x", inline_spec=make_spec())


@pytest.mark.asyncio
async def test_build_agent_tools_inline(monkeypatch):
    # Stub the SDK Agent.as_tool() to record what it received.
    captured = {}

    class _StubAgent:
        name = "stub"

        def as_tool(self, **kwargs):
            captured.update(kwargs)
            return ("FAKE_TOOL", kwargs)

    async def fake_default_build_agent(*args, **kwargs):
        return _StubAgent()

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent",
        fake_default_build_agent,
    )

    sub_spec = make_spec(name="SubAgent")
    cfg = AgentToolConfig(
        inline_spec=sub_spec,
        tool_name="ask_sub",
        tool_description="Consult the sub-agent",
        is_enabled=True,
        max_turns=5,
    )

    out = await _build_agent_tools_from_configs([cfg], settings={})
    assert len(out) == 1
    assert captured["tool_name"] == "ask_sub"
    assert captured["tool_description"] == "Consult the sub-agent"
    assert callable(captured["on_stream"])
    assert captured["is_enabled"] is True
    assert captured["max_turns"] == 5


@pytest.mark.asyncio
async def test_build_agent_tools_passes_session_to_subagent(monkeypatch):
    captured = {}
    session = object()

    class _StubAgent:
        name = "stub"

        def as_tool(self, **kwargs):
            return ("FAKE_TOOL", kwargs)

    async def fake_default_build_agent(*args, **kwargs):
        captured.update(kwargs)
        return _StubAgent()

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent",
        fake_default_build_agent,
    )

    cfg = AgentToolConfig(inline_spec=make_spec(name="Sub"), tool_name="ask_sub")

    out = await _build_agent_tools_from_configs([cfg], settings={}, session=session)

    assert len(out) == 1
    assert captured["session"] is session


@pytest.mark.asyncio
async def test_build_agent_tools_circular_skip(tmp_path, monkeypatch):
    yaml_path = tmp_path / "x.yaml"
    yaml_path.write_text("name: x")
    cfg = AgentToolConfig(yaml_path=str(yaml_path))

    out = await _build_agent_tools_from_configs(
        [cfg], settings={}, _loading_stack={Path(yaml_path).resolve()}
    )
    assert out == []


@pytest.mark.asyncio
async def test_default_build_agent_appends_agent_tools(monkeypatch):
    """Verify _default_build_agent extends `tools` with built agent_tools."""

    class _StubSubAgent:
        name = "sub"

        def as_tool(self, **kwargs):
            return ("AGENT_TOOL_SENTINEL", kwargs)

    async def fake_inner_build(*args, **kwargs):
        # Returning a stub agent only triggered for the SUB-agent build path.
        return _StubSubAgent()

    # Patch only the recursive sub-build invoked by _build_agent_tools_from_configs.
    monkeypatch.setattr(
        "omniagents.core.agents.builder._build_agent_tools_from_configs",
        lambda **kw: _fake_built_tools(),
    )

    async def _fake_built_tools_async():
        return [("AGENT_TOOL_SENTINEL", {})]

    def _fake_built_tools():
        return _fake_built_tools_async()

    spec = make_spec(name="Parent")
    spec.agent_tool_configs = [
        AgentToolConfig(inline_spec=make_spec(name="Sub"), tool_name="ask_sub")
    ]

    agent = await _default_build_agent({}, None, spec)
    # The sentinel should now be in the agent's tools list
    assert any(
        isinstance(t, tuple) and t[0] == "AGENT_TOOL_SENTINEL" for t in agent.tools
    )
