"""Tests for agent-as-tool wiring (Agent.as_tool() integration)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
            return SimpleNamespace(on_invoke_tool=AsyncMock(return_value="ok"))

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
async def test_build_agent_tools_inherits_subagent_max_turns(monkeypatch):
    captured = {}

    class _StubAgent:
        name = "stub"

        def as_tool(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(on_invoke_tool=AsyncMock(return_value="ok"))

    async def fake_default_build_agent(*args, **kwargs):
        return _StubAgent()

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent",
        fake_default_build_agent,
    )

    sub_spec = make_spec(name="SubAgent", max_turns=50)
    cfg = AgentToolConfig(inline_spec=sub_spec, tool_name="ask_sub")

    out = await _build_agent_tools_from_configs([cfg], settings={})

    assert len(out) == 1
    assert captured["max_turns"] == 50


@pytest.mark.asyncio
async def test_build_agent_tools_passes_session_to_subagent(monkeypatch):
    captured = {}
    session = object()

    class _StubAgent:
        name = "stub"

        def as_tool(self, **kwargs):
            return SimpleNamespace(on_invoke_tool=AsyncMock(return_value="ok"))

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


@pytest.mark.asyncio
async def test_project_agent_tools_load_with_the_projects_fragments(
    tmp_path, monkeypatch
):
    """A sub-agent named in `agent_tools` is a project agent: its
    instructions include the project's `paths.instruction_fragments`."""
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "identity.md").write_text("You work at Acme.")
    for name, extra in (("foreman", "agent_tools: [triage]\n"), ("triage", "")):
        d = tmp_path / "agents" / name
        d.mkdir(parents=True)
        (d / "agent.yml").write_text(
            f"name: {name}\ninstructions_file: instructions.md\n{extra}"
        )
        (d / "instructions.md").write_text(
            '{% include "identity.md" %} You are ' + name + "."
        )
    defaults = {
        "instruction_fragments": [str(tmp_path / "fragments")],
        "project_root": str(tmp_path),
    }
    agents_map = {
        n: tmp_path / "agents" / n / "agent.yml" for n in ("foreman", "triage")
    }
    parent = load_agent_spec_from_yaml(
        str(agents_map["foreman"]), agents_map=agents_map, project_defaults=defaults
    )

    built = {}

    class _StubAgent:
        def as_tool(self, **kwargs):
            return SimpleNamespace(on_invoke_tool=AsyncMock(return_value="ok"))

    async def fake_default_build_agent(*args, spec, **kwargs):
        built["spec"] = spec
        return _StubAgent()

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_default_build_agent
    )
    await _build_agent_tools_from_configs(
        parent.agent_tool_configs, settings={}, parent_spec=parent
    )
    paths = [Path(p) for p in built["spec"].instructions_search_paths or []]
    assert tmp_path / "fragments" in paths


@pytest.mark.asyncio
async def test_sub_agents_work_with_the_sessions_connected_mcp_servers(monkeypatch):
    """An agent tool runs inside its caller's run: it gets the connected
    servers it declares (by name) from the caller's pool, never fresh,
    unconnected copies; the caller keeps its own."""
    from types import SimpleNamespace

    from omniagents.core.agents import builder
    from omniagents.core.agents.mcp import subagent_mcp_configs
    from omniagents.core.agents.specs import MCPServerConfig

    github = MCPServerConfig(
        name="github", type="streamable_http", params={"url": "http://g"}
    )
    linear = MCPServerConfig(
        name="linear", type="streamable_http", params={"url": "http://l"}
    )
    sub = make_spec(name="Triage")
    sub.mcp_servers = [github, linear]
    parent = make_spec(name="Foreman")
    parent.mcp_servers = [github]
    parent.agent_tool_configs = [AgentToolConfig(inline_spec=sub, tool_name="triage")]
    assert [c.name for c in subagent_mcp_configs(parent)] == ["linear"]

    built = {}
    real = builder._default_build_agent

    async def spy(settings, mcp_servers, spec, *args, **kwargs):
        agent = await real(settings, mcp_servers, spec, *args, **kwargs)
        built[spec.name] = agent
        return agent

    monkeypatch.setattr(builder, "_default_build_agent", spy)
    connected_github = SimpleNamespace(name="github")
    connected_linear = SimpleNamespace(name="linear", _omni_subagent_only=True)
    parent_agent = await spy({}, [connected_github, connected_linear], parent)
    assert parent_agent.mcp_servers == [connected_github]
    assert built["Triage"].mcp_servers == [connected_github, connected_linear]


def test_a_runs_clone_shares_its_sub_agents_connected_servers():
    """A run works on a copy of the agent; connected servers (which hold
    sockets and locks) are the session's and stay shared, a sub-agent's
    included (the SDK keeps an agent tool's agent on the tool)."""
    import threading

    from agents import Agent

    from omniagents.core.agents.service import AgentService

    class Connected:
        name = "linear"

        def __init__(self):
            self.lock = threading.Lock()

    server = Connected()
    triage = Agent(name="Triage", mcp_servers=[server])
    review = Agent(name="Review", mcp_servers=[server])
    parent = Agent(
        name="Foreman",
        tools=[triage.as_tool(tool_name="triage", tool_description="t")],
        handoffs=[review],
    )
    clone = AgentService._clone_agent(None, parent)
    assert clone is not parent
    clone_triage = clone.tools[0]._agent_instance
    assert clone_triage is not triage and clone_triage.mcp_servers[0] is server
    assert (
        clone.handoffs[0] is not review and clone.handoffs[0].mcp_servers[0] is server
    )
