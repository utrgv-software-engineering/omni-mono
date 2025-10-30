import json

import pytest

from omniagents.core.agents.mcp import build_mcp_servers, mcp_configuration_signature
from omniagents.core.agents.mcp import MCPServerConfig
from omniagents.core.agents.specs import AgentSpec


def make_spec(**overrides):
    base = {
        "name": "agent",
        "header_title": "header",
        "page_title": "page",
        "welcome_text": "welcome",
        "client_storage_prefix": "client",
        "get_agent_instructions": lambda: "",
        "build_agent": None,
        "mcp_servers": overrides.pop("mcp_servers", []),
    }
    base.update(overrides)
    return AgentSpec(**base)


def test_build_mcp_servers_resolves_settings(monkeypatch):
    captured = {}

    class DummyServer:
        def __init__(self, params, **kwargs):
            captured["params"] = params
            captured["kwargs"] = kwargs

    monkeypatch.setattr("omniagents.core.agents.mcp.MCPServerSse", DummyServer)
    config = MCPServerConfig(
        name="primary",
        type="http",
        params={"url": {"setting": "server_url"}},
        options={"cache_tools_list": True, "name": "override"},
    )
    spec = make_spec(mcp_servers=[config])
    servers = build_mcp_servers(spec, {"server_url": "https://example"})
    assert len(servers) == 1
    assert captured["params"]["url"] == "https://example"
    assert captured["kwargs"]["cache_tools_list"] is True
    assert captured["kwargs"]["name"] == "override"


def test_build_mcp_servers_missing_url_raises():
    config = MCPServerConfig(
        name="fail", type="http", params={"url": {"setting": "missing"}}
    )
    spec = make_spec(mcp_servers=[config])
    with pytest.raises(ValueError):
        build_mcp_servers(spec, {})


def test_mcp_configuration_signature_sorted(tmp_path):
    path = tmp_path / "bin"
    path.write_text("echo")
    configs = [
        MCPServerConfig(
            name="zeta",
            type="stdio",
            params={"command": [path]},
            options={"name": "custom"},
        ),
        MCPServerConfig(
            name="alpha",
            type="http",
            params={"url": {"setting": "primary_url"}},
        ),
    ]
    spec = make_spec(mcp_servers=configs)
    signature = mcp_configuration_signature(spec, {"primary_url": "https://a"})
    descriptor = json.loads(signature)
    assert descriptor[0]["name"] == "alpha"
    assert descriptor[0]["params"]["url"] == "https://a"
    assert descriptor[1]["params"]["command"][0] == str(path)
