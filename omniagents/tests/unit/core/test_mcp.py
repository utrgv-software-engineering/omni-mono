import pytest

pytest.importorskip("agents")
pytest.importorskip("agents.mcp")
pytest.importorskip("agents.guardrail")
pytest.importorskip("agents.strict_schema")

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.mcp import build_mcp_servers


def test_build_mcp_servers_stdio(tmp_path):
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


def test_build_mcp_servers_missing_url_raises(tmp_path):
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
