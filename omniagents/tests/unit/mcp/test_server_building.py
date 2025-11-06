"""Tests for MCP server construction and configuration.

This module tests the building and configuration of MCP servers from various
sources (YAML files, programmatic config) and validates proper parameter
resolution and error handling.
"""

import json

import pytest

pytest.importorskip("agents")
pytest.importorskip("agents.mcp")
pytest.importorskip("agents.guardrail")
pytest.importorskip("agents.strict_schema")

from omniagents.core.agents.mcp import build_mcp_servers, mcp_configuration_signature
from omniagents.core.agents.mcp import MCPServerConfig
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.config.loader import load_agent_spec_from_yaml


def make_spec(**overrides):
    """Create a test AgentSpec with optional overrides."""
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


# =============================================================================
# Server Building Tests
# =============================================================================


def test_build_mcp_servers_resolves_settings(monkeypatch):
    """Test that settings placeholders are properly resolved in server params."""
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
    """Test that missing required settings raise ValueError."""
    config = MCPServerConfig(
        name="fail", type="http", params={"url": {"setting": "missing"}}
    )
    spec = make_spec(mcp_servers=[config])
    with pytest.raises(ValueError):
        build_mcp_servers(spec, {})


def test_mcp_configuration_signature_sorted(tmp_path):
    """Test that MCP configuration signature is sorted alphabetically by name."""
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


# =============================================================================
# YAML Integration Tests
# =============================================================================


def test_build_mcp_servers_stdio(tmp_path):
    """Test building stdio MCP servers from YAML configuration."""
    yaml_path = tmp_path / "agent.yml"
    yaml_path.write_text(
        """
name: stdio_agent
instructions: hello
mcp_servers:
  - name: shell
    type: stdio
    params:
      command: echo
      args:
        - hi
"""
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    servers = build_mcp_servers(spec, {})
    assert len(servers) == 1
    assert servers[0].__class__.__name__ == "MCPServerStdio"


def test_build_mcp_servers_sse_placeholder_resolution(tmp_path):
    """Test building SSE MCP servers with setting placeholders from YAML."""
    yaml_path = tmp_path / "agent.yml"
    yaml_path.write_text(
        """
name: sse_agent
instructions: hi
mcp_servers:
  - name: remote
    type: sse
    params:
      url:
        setting: mcp_url
      headers:
        Authorization:
          setting: mcp_token
"""
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    settings = {"mcp_url": "https://example", "mcp_token": "secret"}
    servers = build_mcp_servers(spec, settings)
    assert len(servers) == 1
    server = servers[0]
    assert server.__class__.__name__ == "MCPServerSse"
    assert server.params["url"] == "https://example"
    assert server.params["headers"]["Authorization"] == "secret"


def test_build_mcp_servers_missing_url_raises_yaml(tmp_path):
    """Test that YAML configs with missing required params raise ValueError."""
    yaml_path = tmp_path / "agent.yml"
    yaml_path.write_text(
        """
name: sse_agent
instructions: hi
mcp_servers:
  - name: remote
    type: sse
    params:
      headers:
        Authorization: token
"""
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    with pytest.raises(ValueError):
        build_mcp_servers(spec, {})
