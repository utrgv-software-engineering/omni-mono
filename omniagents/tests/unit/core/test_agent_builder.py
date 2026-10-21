import asyncio
import hashlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

from agents import Agent
from agents.tool import FunctionTool
from omniagents.execution import AgentHostResources
from omniagents.core.agents.builder import (
    _build_handoffs_from_configs,
    _default_build_agent,
)
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import (
    AgentSpec,
    HandoffConfig,
    MCPServerConfig,
    SafeAgentOptions,
    SettingField,
)


def make_spec(**overrides):
    base = {
        "name": overrides.pop("name", "Base"),
        "welcome_text": "Welcome",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: ""),
        "build_agent": overrides.pop("build_agent", None),
    }
    base.update(overrides)
    return AgentSpec(**base)


@pytest.mark.asyncio
async def test_default_build_agent_processes_jinja_instructions():
    spec = make_spec(get_agent_instructions=lambda: "Hello {{ context['user'] }}")
    agent = await _default_build_agent({}, None, spec, None)
    ctx = types.SimpleNamespace(context={"user": "Sam"})
    rendered = await agent.instructions(ctx, agent)
    assert rendered == "Hello Sam"


@pytest.mark.asyncio
async def test_default_build_agent_sets_openai_prompt_cache_key():
    spec = make_spec(
        name="CacheAgent",
        get_agent_instructions=lambda: "Stable instructions",
        model_name="gpt-4o",
    )

    agent = await _default_build_agent({}, None, spec, None)

    expected = hashlib.sha256(
        json.dumps(
            {
                "agent": "CacheAgent",
                "project": "",
                "model": "gpt-4o",
                "instructions": "Stable instructions",
                "tools": [],
                "capabilities": [],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:32]
    assert (
        agent.model_settings.extra_args["prompt_cache_key"] == f"omniagents:{expected}"
    )
    assert agent.model_settings.prompt_cache_retention == "24h"


@pytest.mark.asyncio
async def test_default_build_agent_skips_prompt_cache_for_litellm_model():
    spec = make_spec(
        get_agent_instructions=lambda: "Stable instructions",
        model_name="litellm/anthropic/claude-sonnet-4-5",
    )

    agent = await _default_build_agent({}, None, spec, None)

    assert getattr(agent.model_settings, "prompt_cache_key", None) is None


@pytest.mark.asyncio
async def test_default_build_agent_preserves_explicit_prompt_cache_key():
    spec = make_spec(
        get_agent_instructions=lambda: "Stable instructions",
        model_name="gpt-4o",
        model_settings={"prompt_cache_key": "custom-key"},
    )

    agent = await _default_build_agent({}, None, spec, None)

    assert agent.model_settings.extra_args["prompt_cache_key"] == "custom-key"
    assert getattr(agent.model_settings, "prompt_cache_retention", None) is None


@pytest.mark.asyncio
async def test_default_build_agent_merges_settings_and_configures_mcp(monkeypatch):
    spec = make_spec()
    spec.settings_fields = [
        SettingField(key="api_key", label="A", default="fallback"),
        SettingField(key="openai_base_url", label="B", default="http://default"),
    ]
    spec.mcp_servers = [
        MCPServerConfig(name="srv", type="http", params={"url": "https://x"})
    ]
    recorded_settings = {}

    def fake_build_mcp(spec_arg, settings_arg):
        recorded_settings.update(settings_arg)
        return ["server"]

    calls = []

    def fake_set_default_openai_key(key, use_for_tracing=False):
        calls.append((key, use_for_tracing))

    monkeypatch.setattr(
        "omniagents.core.agents.builder.build_mcp_servers", fake_build_mcp
    )
    monkeypatch.setattr(
        sys.modules["agents"], "set_default_openai_key", fake_set_default_openai_key
    )
    monkeypatch.setenv("API_KEY", "env-value")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent = await _default_build_agent(
        {"openai_base_url": "https://custom", "openai_api_key": "local-key"},
        None,
        spec,
        None,
    )

    assert recorded_settings["api_key"] == "env-value"
    assert recorded_settings["openai_base_url"] == "https://custom"
    assert os.getenv("OPENAI_BASE_URL") == "https://custom"
    assert calls == [("local-key", False)]
    assert agent.mcp_servers == ["server"]


@pytest.mark.asyncio
async def test_default_build_agent_uses_provided_mcp_servers(monkeypatch):
    spec = make_spec()
    called = False

    def fail_build(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("omniagents.core.agents.builder.build_mcp_servers", fail_build)
    provided = ["prebuilt"]
    agent = await _default_build_agent({}, provided, spec, None)
    assert agent.mcp_servers == provided
    assert not called


@pytest.mark.asyncio
async def test_default_build_agent_returns_plain_agent_when_safe_set():
    # SDK 0.16: builder no longer wraps; needs_approval is configured
    # per-session in AgentService.configure_tool_approval.
    spec = make_spec()
    spec.use_safe_agent = True
    spec.safe_agent_options = SafeAgentOptions(
        safe_tool_names=["allowed"],
        safe_tool_patterns=["tool-.*"],
        skip_approvals=False,
    )
    tool = FunctionTool(
        name="allowed",
        description="",
        params_json_schema={},
        on_invoke_tool=lambda ctx, params_json: asyncio.sleep(0),
    )
    spec.available_tools = [tool]

    agent = await _default_build_agent({}, None, spec, None)
    assert isinstance(agent, Agent)
    # Builder leaves needs_approval unset; the service applies it per session.
    assert tool.needs_approval is False


@pytest.mark.asyncio
async def test_build_handoffs_from_configs_builds_agents(monkeypatch, tmp_path):
    import agents

    child_spec = make_spec(name="Child")
    inline_spec = make_spec(name="Inline")
    build_calls = []

    async def fake_build(
        settings, mcp_servers, spec, approval_callback, _loading_stack=None
    ):
        build_calls.append((_loading_stack, approval_callback, spec.name))
        return types.SimpleNamespace(name=spec.name)

    def fake_loader(
        path,
        tool_registry=None,
        guardrails_registry=None,
        server_functions_registry=None,
        agents_map=None,
        capability_registry=None,
    ):
        return child_spec

    handoff_calls = []

    def fake_handoff(agent, tool_name_override=None, tool_description_override=None):
        handoff_calls.append((agent, tool_name_override, tool_description_override))
        return {
            "agent": agent,
            "tool": tool_name_override,
            "description": tool_description_override,
        }

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_build
    )
    monkeypatch.setattr(
        "omniagents.core.config.loader.load_agent_spec_from_yaml", fake_loader
    )
    monkeypatch.setattr(agents, "handoff", fake_handoff, raising=False)

    yaml_path = tmp_path / "child.yaml"
    yaml_path.write_text("x", encoding="utf-8")
    configs = [
        HandoffConfig(yaml_path=str(yaml_path)),
        HandoffConfig(
            inline_spec=inline_spec,
            tool_name_override="inline-tool",
            tool_description_override="Inline description",
        ),
    ]

    result = await _build_handoffs_from_configs(
        configs, {"k": "v"}, approval_callback="cb"
    )

    assert [item[2] for item in handoff_calls] == [None, "Inline description"]
    assert [call[2] for call in build_calls] == ["Child", "Inline"]
    assert Path(yaml_path).resolve() in build_calls[0][0]
    assert build_calls[0][1] == "cb"
    assert result[1]["tool"] == "inline-tool"


@pytest.mark.asyncio
async def test_build_handoffs_from_configs_skips_circular(monkeypatch, tmp_path):
    import agents

    async def fail_build(*args, **kwargs):
        raise AssertionError("should not build")

    def fake_handoff(*args, **kwargs):
        return {}

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fail_build
    )
    monkeypatch.setattr(agents, "handoff", fake_handoff, raising=False)

    yaml_path = tmp_path / "node.yaml"
    stack = {yaml_path.resolve()}
    configs = [HandoffConfig(yaml_path=str(yaml_path))]

    result = await _build_handoffs_from_configs(
        configs, {}, approval_callback=None, _loading_stack=stack
    )
    assert result == []


@pytest.mark.asyncio
async def test_agent_service_configures_needs_approval_when_safe_set(monkeypatch):
    # When use_safe_agent is true, the service walks the agent's tool list
    # and attaches a needs_approval predicate to every FunctionTool that
    # does not already declare one.
    spec = make_spec()
    spec.use_safe_agent = True
    spec.safe_agent_options = SafeAgentOptions(
        safe_tool_names=["allowed"], safe_tool_patterns=[], skip_approvals=False
    )

    tool_a = FunctionTool(
        name="risky",
        description="",
        params_json_schema={},
        on_invoke_tool=lambda ctx, params_json: asyncio.sleep(0),
    )
    tool_b = FunctionTool(
        name="allowed",
        description="",
        params_json_schema={},
        on_invoke_tool=lambda ctx, params_json: asyncio.sleep(0),
    )

    async def fake_build(
        settings, mcp_servers, spec_arg, approval_callback, _loading_stack=None, **_
    ):
        return Agent(name="base", tools=[tool_a, tool_b], instructions="", model="m")

    # service.py imports _default_build_agent at module load time, so we
    # patch the module-local reference rather than the source.
    monkeypatch.setattr(
        "omniagents.core.agents.service._default_build_agent", fake_build
    )
    monkeypatch.setattr(AgentService, "_clone_agent", lambda self, agent: agent)

    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agent_builder-host"),
    )
    session = service._session_manager.get_or_create(None)
    agent = await service._build_agent_for_session(session, None)

    assert isinstance(agent, Agent)
    # Both tools have a callable predicate now.
    assert callable(tool_a.needs_approval)
    assert callable(tool_b.needs_approval)

    # The "allowed" tool is in the spec safe list, so its predicate
    # returns False (no approval needed).
    no_approval = await tool_b.needs_approval(None, {}, "call-1")
    assert no_approval is False
    # The "risky" tool is not allowlisted, so it does need approval.
    needs = await tool_a.needs_approval(None, {}, "call-2")
    assert needs is True


@pytest.mark.asyncio
async def test_agent_service_skip_approvals_disables_predicate(monkeypatch):
    spec = make_spec()
    spec.use_safe_agent = True
    spec.safe_agent_options = SafeAgentOptions(skip_approvals=True)

    tool = FunctionTool(
        name="anything",
        description="",
        params_json_schema={},
        on_invoke_tool=lambda ctx, params_json: asyncio.sleep(0),
    )

    async def fake_build(
        settings, mcp_servers, spec_arg, approval_callback, _loading_stack=None, **_
    ):
        return Agent(name="base", tools=[tool], instructions="", model="m")

    # service.py imports _default_build_agent at module load time, so we
    # patch the module-local reference rather than the source.
    monkeypatch.setattr(
        "omniagents.core.agents.service._default_build_agent", fake_build
    )
    monkeypatch.setattr(AgentService, "_clone_agent", lambda self, agent: agent)

    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agent_builder-host"),
    )
    session = service._session_manager.get_or_create(None)
    await service._build_agent_for_session(session, None)

    # skip_approvals=True → predicate is attached but always returns False.
    assert callable(tool.needs_approval)
    assert (await tool.needs_approval(None, {}, "call-1")) is False
