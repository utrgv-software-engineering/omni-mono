from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, Dict, List

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from mcp import StdioServerParameters, stdio_client

from .specs import AgentSpec, MCPServerConfig


class QuietMCPServerStdio(MCPServerStdio):
    """MCPServerStdio subclass that suppresses stderr output.

    This prevents MCP server subprocess output from interfering with TUIs.
    """

    def create_streams(self):
        """Create streams with stderr redirected to /dev/null."""
        # Open /dev/null for stderr
        devnull = open(os.devnull, "w")

        @asynccontextmanager
        async def quiet_stdio_client():
            async with stdio_client(self.params, errlog=devnull) as streams:
                try:
                    yield streams
                finally:
                    pass
            # Close devnull after the context exits
            devnull.close()

        return quiet_stdio_client()


def _coerce_pathlike(value: Any) -> Any:
    if hasattr(value, "__fspath__"):
        return value.__fspath__()
    return value


def _resolve_setting_placeholders(value: Any, settings: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if "setting" in value and len(value) == 1:
            key = value["setting"]
            return settings.get(key)
        if "$setting" in value and len(value) == 1:
            key = value["$setting"]
            return settings.get(key)
        return {k: _resolve_setting_placeholders(v, settings) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_setting_placeholders(v, settings) for v in value]
    if isinstance(value, tuple):
        return tuple(_resolve_setting_placeholders(v, settings) for v in value)
    return _coerce_pathlike(value)


def _instantiate_server(cfg: MCPServerConfig, params: Dict[str, Any]) -> MCPServer:
    server_type = cfg.type.lower()
    options = cfg.options or {}
    common_kwargs = {
        "cache_tools_list": options.get("cache_tools_list", False),
        "name": options.get("name", cfg.name),
        "client_session_timeout_seconds": options.get("client_session_timeout_seconds"),
        "tool_filter": options.get("tool_filter"),
        "use_structured_content": options.get("use_structured_content", False),
    }
    if server_type in {"http", "sse"}:
        if "url" not in params or not params["url"]:
            raise ValueError(
                f"MCP server '{cfg.name}' requires a url for HTTP/SSE transport"
            )
        return MCPServerSse(params, **common_kwargs)
    if server_type == "stdio":
        if "command" not in params or not params["command"]:
            raise ValueError(
                f"MCP server '{cfg.name}' requires a command for stdio transport"
            )
        return QuietMCPServerStdio(params, **common_kwargs)
    if server_type == "streamable_http":
        if "url" not in params or not params["url"]:
            raise ValueError(
                f"MCP server '{cfg.name}' requires a url for streamable_http transport"
            )
        return MCPServerStreamableHttp(params, **common_kwargs)
    raise ValueError(
        f"Unsupported MCP server type '{cfg.type}' for server '{cfg.name}'"
    )


def build_mcp_servers(spec: AgentSpec, settings: Dict[str, Any]) -> List[MCPServer]:
    servers: List[MCPServer] = []
    for cfg in spec.mcp_servers:
        resolved_params = _resolve_setting_placeholders(deepcopy(cfg.params), settings)
        if resolved_params is None:
            raise ValueError(f"MCP server '{cfg.name}' has params that resolve to None")
        server = _instantiate_server(cfg, resolved_params)
        servers.append(server)
    return servers


def mcp_configuration_signature(spec: AgentSpec, settings: Dict[str, Any]) -> str:
    def _normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): _normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_normalize(v) for v in value]
        if hasattr(value, "__fspath__"):
            return value.__fspath__()
        return value

    descriptor: List[Dict[str, Any]] = []
    for cfg in spec.mcp_servers:
        resolved = _resolve_setting_placeholders(deepcopy(cfg.params), settings)
        descriptor.append(
            {
                "name": cfg.name,
                "type": cfg.type,
                "options": _normalize(cfg.options),
                "params": _normalize(resolved),
            }
        )
    descriptor.sort(key=lambda item: item["name"])
    return json.dumps(descriptor, sort_keys=True, default=str)
