"""Integration tests for user-supplied MCP server configuration.

This module tests the full flow of loading user MCP configs from JSON files
and merging them with agent-defined MCP servers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omniagents.core.project.runtime import (
    build_spec_from_project,
    load_project,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# =============================================================================
# Project Opt-in Flag Tests
# =============================================================================


def test_project_without_flag_ignores_user_mcp(tmp_path):
    """Verify allow_user_mcp_servers: false (default) ignores user configs."""
    root = tmp_path / "project"
    root.mkdir()

    # Create project.yml WITHOUT allow_user_mcp_servers flag
    _write(
        root / "project.yml",
        """product:
  name: test_project
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
""",
    )

    # Create user MCP config that SHOULD be ignored
    user_mcp_dir = root / ".test_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "user-server": {"type": "stdio", "command": "should-be-ignored"}
                }
            }
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    # User MCP should NOT be loaded
    assert context.allow_user_mcp_servers is False
    assert context.user_mcp_servers == []
    assert len(spec.mcp_servers) == 0


def test_project_with_flag_loads_user_mcp(tmp_path):
    """Verify allow_user_mcp_servers: true loads user configs."""
    root = tmp_path / "project"
    root.mkdir()

    # Create project.yml WITH allow_user_mcp_servers flag
    _write(
        root / "project.yml",
        """product:
  name: test_project
allow_user_mcp_servers: true
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
""",
    )

    # Create user MCP config
    user_mcp_dir = root / ".test_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"user-server": {"type": "stdio", "command": "user-cmd"}}}
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    # User MCP SHOULD be loaded
    assert context.allow_user_mcp_servers is True
    assert len(context.user_mcp_servers) == 1
    assert context.user_mcp_servers[0].name == "user-server"
    assert len(spec.mcp_servers) == 1
    assert spec.mcp_servers[0].name == "user-server"


def test_project_with_flag_false_ignores_user_mcp(tmp_path):
    """Verify explicit allow_user_mcp_servers: false ignores user configs."""
    root = tmp_path / "project"
    root.mkdir()

    _write(
        root / "project.yml",
        """product:
  name: test_project
allow_user_mcp_servers: false
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
""",
    )

    user_mcp_dir = root / ".test_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "user-server": {"type": "stdio", "command": "should-be-ignored"}
                }
            }
        )
    )

    context = load_project(str(root))
    assert context.allow_user_mcp_servers is False
    assert context.user_mcp_servers == []


def test_invalid_flag_type_defaults_false(tmp_path, capsys):
    """Verify non-boolean flag value defaults to false with warning."""
    root = tmp_path / "project"
    root.mkdir()

    _write(
        root / "project.yml",
        """product:
  name: test_project
allow_user_mcp_servers: "yes"
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
""",
    )

    user_mcp_dir = root / ".test_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "user-server": {"type": "stdio", "command": "should-be-ignored"}
                }
            }
        )
    )

    context = load_project(str(root))

    assert context.allow_user_mcp_servers is False
    captured = capsys.readouterr()
    assert "should be a boolean" in captured.out


# =============================================================================
# Merge Precedence Tests
# =============================================================================


def test_user_mcp_merged_with_agent_yml(tmp_path):
    """Test final merged list contains both user and agent servers."""
    root = tmp_path / "project"
    root.mkdir()

    _write(
        root / "project.yml",
        """product:
  name: test_project
allow_user_mcp_servers: true
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
mcp_servers:
  - name: agent-server
    type: stdio
    params:
      command: agent-cmd
""",
    )

    user_mcp_dir = root / ".test_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"user-server": {"type": "stdio", "command": "user-cmd"}}}
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    assert len(spec.mcp_servers) == 2
    names = {s.name for s in spec.mcp_servers}
    assert names == {"agent-server", "user-server"}


def test_user_mcp_overrides_agent_yml_same_name(tmp_path):
    """Test user MCP server with same name overrides agent.yml server."""
    root = tmp_path / "project"
    root.mkdir()

    _write(
        root / "project.yml",
        """product:
  name: test_project
allow_user_mcp_servers: true
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
mcp_servers:
  - name: shared-server
    type: stdio
    params:
      command: agent-cmd
""",
    )

    user_mcp_dir = root / ".test_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"shared-server": {"type": "stdio", "command": "user-cmd"}}}
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    assert len(spec.mcp_servers) == 1
    assert spec.mcp_servers[0].name == "shared-server"
    # User config should win
    assert spec.mcp_servers[0].params["command"] == "user-cmd"


def test_full_precedence_chain(tmp_path, monkeypatch):
    """Test: project-local > user-global > agent.yml precedence."""
    root = tmp_path / "project"
    root.mkdir()

    # Set up fake XDG config
    xdg_config = tmp_path / "xdg_config"
    xdg_config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    _write(
        root / "project.yml",
        """product:
  name: test_project
allow_user_mcp_servers: true
paths:
  agents: agents
""",
    )

    # Agent-defined server (lowest precedence)
    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
mcp_servers:
  - name: shared-server
    type: stdio
    params:
      command: agent-cmd
  - name: agent-only
    type: stdio
    params:
      command: agent-only-cmd
""",
    )

    # User-global config (middle precedence)
    global_dir = xdg_config / "test_project"
    global_dir.mkdir()
    (global_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared-server": {"type": "stdio", "command": "global-cmd"},
                    "global-only": {"type": "stdio", "command": "global-only-cmd"},
                }
            }
        )
    )

    # Project-local config (highest precedence)
    local_dir = root / ".test_project"
    local_dir.mkdir()
    (local_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared-server": {"type": "stdio", "command": "local-cmd"},
                    "local-only": {"type": "stdio", "command": "local-only-cmd"},
                }
            }
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    # Should have 4 servers total
    assert len(spec.mcp_servers) == 4
    servers_by_name = {s.name: s for s in spec.mcp_servers}

    # Shared server should use project-local (highest precedence)
    assert servers_by_name["shared-server"].params["command"] == "local-cmd"

    # Each unique server should be present
    assert "agent-only" in servers_by_name
    assert "global-only" in servers_by_name
    assert "local-only" in servers_by_name


# =============================================================================
# Voice Spec Tests
# =============================================================================


def test_voice_spec_gets_user_mcp_servers(tmp_path):
    """Test that voice_spec also receives user MCP servers when enabled."""
    root = tmp_path / "project"
    root.mkdir()

    _write(
        root / "project.yml",
        """product:
  name: test_project
allow_user_mcp_servers: true
paths:
  agents: agents
agents:
  entrypoint: main
  voice_entrypoint: voice
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: main agent
""",
    )

    _write(
        root / "agents" / "voice" / "agent.yml",
        """name: voice
model: gpt-realtime
instructions: voice agent
realtime_mode: true
""",
    )

    user_mcp_dir = root / ".test_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"user-server": {"type": "stdio", "command": "user-cmd"}}}
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    # Both main spec and voice spec should have user MCP servers
    assert len(spec.mcp_servers) == 1
    assert spec.mcp_servers[0].name == "user-server"

    assert spec.voice_spec is not None
    assert len(spec.voice_spec.mcp_servers) == 1
    assert spec.voice_spec.mcp_servers[0].name == "user-server"


# =============================================================================
# Project Name Handling Tests
# =============================================================================


def test_project_name_slugified_for_config_path(tmp_path):
    """Test that project names are properly slugified for config paths."""
    root = tmp_path / "project"
    root.mkdir()

    # Project name with spaces and special chars
    _write(
        root / "project.yml",
        """product:
  name: "My Project Name"
allow_user_mcp_servers: true
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
""",
    )

    # Config should be in slugified directory name
    user_mcp_dir = root / ".my_project_name"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"user-server": {"type": "stdio", "command": "user-cmd"}}}
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    assert len(spec.mcp_servers) == 1
    assert spec.mcp_servers[0].name == "user-server"


def test_missing_project_name_uses_directory(tmp_path):
    """Test that missing project name falls back to directory name."""
    root = tmp_path / "my_custom_project"
    root.mkdir()

    # Project without name field
    _write(
        root / "project.yml",
        """allow_user_mcp_servers: true
paths:
  agents: agents
""",
    )

    _write(
        root / "agents" / "main" / "agent.yml",
        """name: main
model: gpt-4o
instructions: hello
""",
    )

    # Config should use directory name
    user_mcp_dir = root / ".my_custom_project"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"user-server": {"type": "stdio", "command": "user-cmd"}}}
        )
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    assert len(spec.mcp_servers) == 1
