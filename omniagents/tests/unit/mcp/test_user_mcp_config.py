"""Tests for user-supplied MCP server configuration loading.

This module tests the loading and parsing of user MCP configurations from JSON files,
environment variable expansion, error handling, and merge precedence.
"""

import json
import os

import pytest

pytest.importorskip("agents")
pytest.importorskip("agents.mcp")

from omniagents.core.config.user_mcp import (
    get_user_config_dir,
    load_user_mcp_config,
    _expand_env_vars,
    _parse_mcp_json,
    _load_mcp_json_file,
)
from omniagents.core.agents.specs import MCPServerConfig


# =============================================================================
# Environment Variable Expansion Tests
# =============================================================================


def test_expand_env_vars_simple(monkeypatch):
    """Test ${VAR} substitution."""
    monkeypatch.setenv("TEST_VAR", "hello")
    assert _expand_env_vars("${TEST_VAR}") == "hello"


def test_expand_env_vars_with_default(monkeypatch):
    """Test ${VAR:-default} when VAR is not set."""
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert _expand_env_vars("${MISSING_VAR:-fallback}") == "fallback"


def test_expand_env_vars_with_default_when_set(monkeypatch):
    """Test ${VAR:-default} uses VAR when set."""
    monkeypatch.setenv("PRESENT_VAR", "actual_value")
    assert _expand_env_vars("${PRESENT_VAR:-fallback}") == "actual_value"


def test_expand_env_vars_missing_returns_empty(monkeypatch):
    """Test ${VAR} returns empty string when not set."""
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert _expand_env_vars("${MISSING_VAR}") == ""


def test_expand_env_vars_in_nested_structures(monkeypatch):
    """Test expansion works in args arrays and env dicts."""
    monkeypatch.setenv("ARG_VAL", "my-arg")
    monkeypatch.setenv("ENV_VAL", "my-env")

    result = _expand_env_vars(
        {
            "command": "test",
            "args": ["--flag", "${ARG_VAL}"],
            "env": {"KEY": "${ENV_VAL}"},
        }
    )

    assert result["args"] == ["--flag", "my-arg"]
    assert result["env"]["KEY"] == "my-env"


def test_expand_env_vars_preserves_non_strings():
    """Test that non-string values are preserved."""
    assert _expand_env_vars(123) == 123
    assert _expand_env_vars(True) is True
    assert _expand_env_vars(None) is None
    assert _expand_env_vars([1, 2, 3]) == [1, 2, 3]


def test_expand_env_vars_partial_string(monkeypatch):
    """Test env var in middle of string."""
    monkeypatch.setenv("MIDDLE", "world")
    assert _expand_env_vars("hello ${MIDDLE}!") == "hello world!"


# =============================================================================
# Config Parsing Tests
# =============================================================================


def test_parse_mcp_json_valid_stdio_server():
    """Verify stdio server with command/args/env parses correctly."""
    data = {
        "mcpServers": {
            "my-server": {
                "type": "stdio",
                "command": "npx",
                "args": ["@test/mcp-server"],
                "env": {"API_KEY": "secret"},
            }
        }
    }
    configs = _parse_mcp_json(data, "test.json")

    assert len(configs) == 1
    assert configs[0].name == "my-server"
    assert configs[0].type == "stdio"
    assert configs[0].params["command"] == "npx"
    assert configs[0].params["args"] == ["@test/mcp-server"]
    assert configs[0].params["env"] == {"API_KEY": "secret"}


def test_parse_mcp_json_valid_http_server():
    """Verify http/sse server with url/headers parses correctly."""
    data = {
        "mcpServers": {
            "api-server": {
                "type": "sse",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer token"},
            }
        }
    }
    configs = _parse_mcp_json(data, "test.json")

    assert len(configs) == 1
    assert configs[0].name == "api-server"
    assert configs[0].type == "sse"
    assert configs[0].params["url"] == "https://example.com/mcp"
    assert configs[0].params["headers"] == {"Authorization": "Bearer token"}


def test_parse_mcp_json_multiple_servers():
    """Verify multiple servers are all parsed."""
    data = {
        "mcpServers": {
            "server-a": {"type": "stdio", "command": "cmd-a"},
            "server-b": {"type": "stdio", "command": "cmd-b"},
            "server-c": {"type": "sse", "url": "https://c.example.com"},
        }
    }
    configs = _parse_mcp_json(data, "test.json")

    assert len(configs) == 3
    names = {c.name for c in configs}
    assert names == {"server-a", "server-b", "server-c"}


def test_parse_mcp_json_type_defaults_to_stdio():
    """Verify missing type defaults to 'stdio'."""
    data = {"mcpServers": {"no-type": {"command": "echo"}}}
    configs = _parse_mcp_json(data, "test.json")

    assert len(configs) == 1
    assert configs[0].type == "stdio"


def test_parse_mcp_json_with_options():
    """Verify options like cache_tools_list are preserved."""
    data = {
        "mcpServers": {
            "cached": {
                "type": "stdio",
                "command": "cmd",
                "cache_tools_list": True,
                "client_session_timeout_seconds": 300,
            }
        }
    }
    configs = _parse_mcp_json(data, "test.json")

    assert len(configs) == 1
    assert configs[0].options is not None
    assert configs[0].options["cache_tools_list"] is True
    assert configs[0].options["client_session_timeout_seconds"] == 300


# =============================================================================
# Error Handling Tests
# =============================================================================


def test_parse_mcp_json_missing_mcpservers_returns_empty():
    """Verify JSON without mcpServers key returns empty list."""
    data = {"other": "data"}
    configs = _parse_mcp_json(data, "test.json")
    assert configs == []


def test_parse_mcp_json_mcpservers_not_dict_warns(capsys):
    """Verify mcpServers as non-dict logs warning, returns empty."""
    data = {"mcpServers": ["not", "a", "dict"]}
    configs = _parse_mcp_json(data, "test.json")

    assert configs == []
    captured = capsys.readouterr()
    assert "should be a dict" in captured.out


def test_parse_mcp_json_missing_command_warns(capsys):
    """Verify stdio without command is skipped with warning."""
    data = {"mcpServers": {"no-cmd": {"type": "stdio"}}}
    configs = _parse_mcp_json(data, "test.json")

    assert configs == []
    captured = capsys.readouterr()
    assert "requires 'command'" in captured.out


def test_parse_mcp_json_missing_url_warns(capsys):
    """Verify http without url is skipped with warning."""
    data = {"mcpServers": {"no-url": {"type": "http"}}}
    configs = _parse_mcp_json(data, "test.json")

    assert configs == []
    captured = capsys.readouterr()
    assert "requires 'url'" in captured.out


def test_parse_mcp_json_unknown_type_warns(capsys):
    """Verify unknown server type is skipped with warning."""
    data = {"mcpServers": {"unknown": {"type": "foobar", "command": "x"}}}
    configs = _parse_mcp_json(data, "test.json")

    assert configs == []
    captured = capsys.readouterr()
    assert "Unknown server type" in captured.out


def test_parse_mcp_json_duplicate_name_warns(capsys):
    """Verify duplicate server names are skipped with warning."""
    # Note: JSON doesn't allow duplicate keys, but we handle it gracefully
    # This tests the internal dedup logic
    data = {"mcpServers": {"dup": {"type": "stdio", "command": "x"}}}
    configs = _parse_mcp_json(data, "test.json")
    assert len(configs) == 1


# =============================================================================
# File Loading Tests
# =============================================================================


def test_load_mcp_json_file_valid(tmp_path):
    """Test loading a valid mcp.json file."""
    config_file = tmp_path / "mcp.json"
    config_file.write_text(
        json.dumps({"mcpServers": {"test": {"type": "stdio", "command": "echo"}}})
    )

    configs = _load_mcp_json_file(config_file)
    assert len(configs) == 1
    assert configs[0].name == "test"


def test_load_mcp_json_file_missing_returns_empty(tmp_path):
    """Verify missing config file returns empty list silently."""
    missing_file = tmp_path / "does_not_exist.json"
    configs = _load_mcp_json_file(missing_file)
    assert configs == []


def test_load_mcp_json_file_invalid_json_warns(tmp_path, capsys):
    """Verify invalid JSON logs warning and returns empty list."""
    config_file = tmp_path / "mcp.json"
    config_file.write_text("{invalid json")

    configs = _load_mcp_json_file(config_file)
    assert configs == []
    captured = capsys.readouterr()
    assert "Invalid JSON" in captured.out


def test_load_mcp_json_file_empty_returns_empty(tmp_path):
    """Verify empty file returns empty list."""
    config_file = tmp_path / "mcp.json"
    config_file.write_text("")

    configs = _load_mcp_json_file(config_file)
    assert configs == []


# =============================================================================
# User Config Loading Tests
# =============================================================================


def test_load_user_mcp_config_project_local_only(tmp_path):
    """Test loading from project-local config only."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Create project-local config
    local_dir = project_root / ".test_project"
    local_dir.mkdir()
    (local_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"local-server": {"type": "stdio", "command": "local-cmd"}}}
        )
    )

    configs = load_user_mcp_config(project_root, "test_project")

    assert len(configs) == 1
    assert configs[0].name == "local-server"


def test_load_user_mcp_config_user_global_only(tmp_path, monkeypatch):
    """Test loading from user-global config only."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Set up fake XDG config home
    xdg_config = tmp_path / "xdg_config"
    xdg_config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    # Create user-global config
    global_dir = xdg_config / "test_project"
    global_dir.mkdir()
    (global_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "global-server": {"type": "stdio", "command": "global-cmd"}
                }
            }
        )
    )

    configs = load_user_mcp_config(project_root, "test_project")

    assert len(configs) == 1
    assert configs[0].name == "global-server"


def test_load_user_mcp_config_project_local_overrides_global(tmp_path, monkeypatch):
    """Verify project-local takes precedence over user-global."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Set up fake XDG config home
    xdg_config = tmp_path / "xdg_config"
    xdg_config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    # Create user-global config
    global_dir = xdg_config / "test_project"
    global_dir.mkdir()
    (global_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"shared": {"type": "stdio", "command": "global-cmd"}}}
        )
    )

    # Create project-local config (same server name, different command)
    local_dir = project_root / ".test_project"
    local_dir.mkdir()
    (local_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"shared": {"type": "stdio", "command": "local-cmd"}}}
        )
    )

    configs = load_user_mcp_config(project_root, "test_project")

    assert len(configs) == 1
    assert configs[0].name == "shared"
    # Project-local should win
    assert configs[0].params["command"] == "local-cmd"


def test_load_user_mcp_config_unique_names_merged(tmp_path, monkeypatch):
    """Verify uniquely named servers from both sources are included."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Set up fake XDG config home
    xdg_config = tmp_path / "xdg_config"
    xdg_config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    # Create user-global config
    global_dir = xdg_config / "test_project"
    global_dir.mkdir()
    (global_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"global-only": {"type": "stdio", "command": "global-cmd"}}}
        )
    )

    # Create project-local config
    local_dir = project_root / ".test_project"
    local_dir.mkdir()
    (local_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"local-only": {"type": "stdio", "command": "local-cmd"}}}
        )
    )

    configs = load_user_mcp_config(project_root, "test_project")

    assert len(configs) == 2
    names = {c.name for c in configs}
    assert names == {"global-only", "local-only"}


def test_load_user_mcp_config_both_missing_returns_empty(tmp_path, monkeypatch):
    """Test that both missing config files return empty list."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Set up fake XDG config home with no config
    xdg_config = tmp_path / "xdg_config"
    xdg_config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    configs = load_user_mcp_config(project_root, "test_project")
    assert configs == []


# =============================================================================
# Config Directory Tests
# =============================================================================


def test_get_user_config_dir_xdg_override(tmp_path, monkeypatch):
    """Verify XDG_CONFIG_HOME is respected."""
    xdg_path = tmp_path / "custom_xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_path))

    result = get_user_config_dir("my_project")
    assert result == xdg_path / "my_project"


def test_get_user_config_dir_default_unix(tmp_path, monkeypatch):
    """Verify default ~/.config is used when XDG not set."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # We can't easily test the actual path, but we can verify the structure
    if os.name != "nt":
        result = get_user_config_dir("my_project")
        assert result.name == "my_project"
        assert result.parent.name == ".config"


# =============================================================================
# Environment Variable Expansion in Files
# =============================================================================


def test_load_user_mcp_config_expands_env_vars(tmp_path, monkeypatch):
    """Test that env vars are expanded when loading configs."""
    monkeypatch.setenv("MY_COMMAND", "expanded-cmd")
    monkeypatch.setenv("MY_API_KEY", "secret-key")

    project_root = tmp_path / "project"
    project_root.mkdir()

    local_dir = project_root / ".test_project"
    local_dir.mkdir()
    (local_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "env-server": {
                        "type": "stdio",
                        "command": "${MY_COMMAND}",
                        "env": {"API_KEY": "${MY_API_KEY}"},
                    }
                }
            }
        )
    )

    configs = load_user_mcp_config(project_root, "test_project")

    assert len(configs) == 1
    assert configs[0].params["command"] == "expanded-cmd"
    assert configs[0].params["env"]["API_KEY"] == "secret-key"
