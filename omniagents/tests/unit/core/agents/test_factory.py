import pytest

from omniagents.core.agents.factory import (
    create_unified_agent,
    is_remote_agent,
    is_local_agent,
)
from omniagents.core.agents.specs import AgentSpec, SettingField


def make_spec(**overrides):
    base = {
        "name": "agent",
        "header_title": "header",
        "page_title": "page",
        "welcome_text": "welcome",
        "client_storage_prefix": "client",
        "get_agent_instructions": lambda: "",
        "build_agent": None,
        "settings_fields": overrides.pop("settings_fields", []),
    }
    base.update(overrides)
    return AgentSpec(**base)


@pytest.mark.asyncio
async def test_create_unified_agent_remote(monkeypatch):
    monkeypatch.setenv("REMOTE_TOKEN", "abc123")
    spec = make_spec(
        remote_ws_url="ws://example",
        remote_auth_token_key="REMOTE_TOKEN",
        model_name="gpt-x",
        name="remote",
    )
    agent = await create_unified_agent(spec)
    assert is_remote_agent(agent) is True
    assert agent.model == "gpt-x"
    assert "token=abc123" in agent._url


@pytest.mark.asyncio
async def test_create_unified_agent_local(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    fields = [SettingField(key="api_key", label="API")]
    spec = make_spec(settings_fields=fields)
    adapter = await create_unified_agent(spec, settings={})
    assert is_local_agent(adapter) is True
    assert adapter.settings["api_key"] == "secret"
    assert spec.model_name == "gpt-4"
