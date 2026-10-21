"""User-supplied MCP server configuration loading.

This module handles loading MCP server configurations from user-supplied JSON files,
following the Claude Code `.mcp.json` format for familiarity and ecosystem compatibility.

Configuration locations (in order of precedence):
1. Project-local: `<project-root>/.<project-name>/mcp.json`
2. User-global: `~/.config/<project-name>/mcp.json` (or $XDG_CONFIG_HOME/<project-name>/mcp.json)

The format is compatible with Claude Code's mcp.json:

    {
      "mcpServers": {
        "server-name": {
          "type": "stdio",
          "command": "npx",
          "args": ["@some/mcp-server"],
          "env": {"API_KEY": "${MY_API_KEY:-default}"}
        }
      }
    }

Environment variable expansion is supported using ${VAR} or ${VAR:-default} syntax.
Missing variables return empty string (lenient behavior).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from omniagents.core.agents.specs import MCPServerConfig


def get_user_config_dir(project_name: str) -> Path:
    """Get user-global config directory for a project.

    On Windows: %APPDATA%/<project-name>
    On Unix: $XDG_CONFIG_HOME/<project-name> or ~/.config/<project-name>

    Args:
        project_name: The project name (used as directory name)

    Returns:
        Path to the user config directory for this project
    """
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / project_name
    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / project_name
    return Path.home() / ".config" / project_name


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} in strings.

    Missing variables return empty string (lenient behavior matching Claude Code).

    Args:
        value: Value to expand (string, dict, list, or other)

    Returns:
        Value with environment variables expanded
    """
    if isinstance(value, str):
        # Pattern matches ${VAR} or ${VAR:-default}
        pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"

        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)
            env_value = os.getenv(var_name)
            if env_value is not None:
                return env_value
            if default is not None:
                return default
            return ""  # Lenient: return empty string for missing vars

        return re.sub(pattern, replace_var, value)
    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    else:
        return value


def _parse_mcp_json(
    data: Dict[str, Any],
    source: str,
) -> List[MCPServerConfig]:
    """Parse Claude Code format mcp.json into MCPServerConfig list.

    Args:
        data: Parsed JSON data
        source: Source file path (for error messages)

    Returns:
        List of MCPServerConfig objects
    """
    mcp_servers = data.get("mcpServers")
    if mcp_servers is None:
        return []

    if not isinstance(mcp_servers, dict):
        print(f"Warning: 'mcpServers' in {source} should be a dict. Ignoring.")
        return []

    configs: List[MCPServerConfig] = []
    seen_names: set = set()

    for name, server_config in mcp_servers.items():
        if not isinstance(name, str) or not name.strip():
            print(f"Warning: Invalid server name in {source}. Skipping.")
            continue

        if name in seen_names:
            print(f"Warning: Duplicate server name '{name}' in {source}. Skipping.")
            continue

        if not isinstance(server_config, dict):
            print(f"Warning: Server '{name}' in {source} should be a dict. Skipping.")
            continue

        # Expand environment variables in the config
        server_config = _expand_env_vars(server_config)

        # Get server type (default to stdio)
        server_type = server_config.get("type", "stdio")
        if not isinstance(server_type, str):
            server_type = "stdio"
        server_type = server_type.lower()

        # Validate and build params based on type
        params: Dict[str, Any] = {}

        if server_type in {"stdio"}:
            command = server_config.get("command")
            if not command:
                print(
                    f"Warning: Server '{name}' in {source} requires 'command' for stdio type. Skipping."
                )
                continue
            params["command"] = command
            if "args" in server_config:
                params["args"] = server_config["args"]
            if "env" in server_config:
                params["env"] = server_config["env"]

        elif server_type in {"http", "sse", "streamable_http"}:
            url = server_config.get("url")
            if not url:
                print(
                    f"Warning: Server '{name}' in {source} requires 'url' for {server_type} type. Skipping."
                )
                continue
            params["url"] = url
            if "headers" in server_config:
                params["headers"] = server_config["headers"]

        else:
            print(
                f"Warning: Unknown server type '{server_type}' for '{name}' in {source}. Skipping."
            )
            continue

        # Build options from additional fields
        options: Optional[Dict[str, Any]] = None
        option_fields = [
            "cache_tools_list",
            "client_session_timeout_seconds",
            "tool_filter",
            "use_structured_content",
        ]
        extracted_options = {
            k: server_config[k] for k in option_fields if k in server_config
        }
        if extracted_options:
            options = extracted_options

        configs.append(
            MCPServerConfig(
                name=name,
                type=server_type,
                params=params,
                options=options,
            )
        )
        seen_names.add(name)

    return configs


def _load_mcp_json_file(path: Path) -> List[MCPServerConfig]:
    """Load and parse a single mcp.json file.

    Args:
        path: Path to the mcp.json file

    Returns:
        List of MCPServerConfig objects (empty list on error)
    """
    if not path.is_file():
        return []

    try:
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return []
        data = json.loads(content)
        if not isinstance(data, dict):
            print(f"Warning: {path} should contain a JSON object. Ignoring.")
            return []
        return _parse_mcp_json(data, str(path))
    except json.JSONDecodeError as e:
        print(f"Warning: Invalid JSON in {path}: {e}. Ignoring.")
        return []
    except PermissionError:
        print(f"Warning: Permission denied reading {path}. Ignoring.")
        return []
    except Exception as e:
        print(f"Warning: Error reading {path}: {e}. Ignoring.")
        return []


def load_user_mcp_config(
    project_root: Path,
    project_name: str,
) -> List[MCPServerConfig]:
    """Load and merge user MCP configs from project-local and user-global.

    Precedence (highest to lowest):
    1. Project-local: `<project-root>/.<project-name>/mcp.json`
    2. User-global: `~/.config/<project-name>/mcp.json`

    Same-name servers: higher precedence completely replaces lower.

    Args:
        project_root: Path to project root directory
        project_name: Project name (used for config directory names)

    Returns:
        Merged list of MCPServerConfig with project-local taking precedence
    """
    configs_by_name: Dict[str, MCPServerConfig] = {}

    # 1. Load user-global config (lowest precedence)
    global_path = get_user_config_dir(project_name) / "mcp.json"
    for config in _load_mcp_json_file(global_path):
        configs_by_name[config.name] = config

    # 2. Load project-local config (higher precedence, overwrites)
    local_path = project_root / f".{project_name}" / "mcp.json"
    for config in _load_mcp_json_file(local_path):
        configs_by_name[config.name] = config

    return list(configs_by_name.values())
