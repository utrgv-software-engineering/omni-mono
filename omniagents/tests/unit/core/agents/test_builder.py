from pathlib import Path

import pytest

from omniagents.core.agents.builder import (
    _default_build_agent,
    _build_handoffs_from_configs,
)
from omniagents.core.agents.safe import SafeAgent
from omniagents.core.agents.specs import AgentSpec, HandoffConfig


def make_spec(**overrides):
    base = {
        "name": "agent",
        "header_title": "header",
        "page_title": "page",
        "welcome_text": "welcome",
        "client_storage_prefix": "client",
        "get_agent_instructions": lambda: "",
        "build_agent": overrides.pop("build_agent", None),
    }
    base.update(overrides)
    return AgentSpec(**base)


@pytest.mark.asyncio
async def test_default_build_agent_wraps_safe_agent():
    spec = make_spec()
    spec.use_safe_agent = True
    agent = await _default_build_agent(
        {}, None, spec, approval_callback=lambda *a, **k: None
    )
    assert isinstance(agent, SafeAgent)


@pytest.mark.asyncio
async def test_build_handoffs_from_configs_skips_recursive(tmp_path):
    path = tmp_path / "handoff.yaml"
    config = HandoffConfig(yaml_path=str(path))
    result = await _build_handoffs_from_configs(
        [config],
        {},
        approval_callback=None,
        _loading_stack={Path(path)},
    )
    assert result == []


@pytest.mark.asyncio
async def test_build_handoffs_from_inline(monkeypatch):
    produced = object()

    async def fake_default(*args, **kwargs):
        if "spec" in kwargs:
            return kwargs["spec"]
        return args[2]

    def fake_handoff(agent, tool_name_override=None, tool_description_override=None):
        return (agent, tool_name_override, tool_description_override)

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_default
    )
    monkeypatch.setattr("agents.handoff", fake_handoff)

    spec = make_spec()
    config = HandoffConfig(
        inline_spec=spec, tool_name_override="alias", tool_description_override="desc"
    )
    result = await _build_handoffs_from_configs(
        [config], {}, approval_callback=produced
    )
    assert len(result) == 1
    agent, name_override, desc_override = result[0]
    assert agent is spec
    assert name_override == "alias"
    assert desc_override == "desc"
