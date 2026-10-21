from __future__ import annotations

import json
import inspect
import logging
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple, cast

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from mcp import StdioServerParameters, stdio_client

from . import mcp_ui
from .specs import AgentSpec, MCPServerConfig

logger = logging.getLogger(__name__)


async def _capture_ui_after_call(
    server: MCPServer, tool_name: str, result: Any
) -> None:
    """Stash any MCP-Apps UI payload from ``result`` for the bridge to pop.

    Two flavors are supported:

    * **Inline** (mcp-ui demo) — the tool's response contains an
      ``EmbeddedResource`` block with ``ui://`` URI or
      ``text/html;profile=mcp-app`` mime. We record the whole block.

    * **Resource URI** (FastMCP / Prefab) — the tool was registered with
      ``_meta.ui.resourceUri`` (captured at ``list_tools`` time). The
      response carries ``structuredContent`` describing what the
      already-shared renderer should display. We record the URI plus
      the structured content; the host fetches the renderer HTML
      separately via ``mcp.read_resource``.

    Both can coexist — prefer the resource-URI form when present since
    it's the canonical MCP Apps shape.
    """
    server_name = getattr(server, "name", "") or ""

    # Resource-URI form: prefer this when we have a cached URI for the tool.
    cached = mcp_ui.lookup_tool_ui_resource(tool_name, server_name)
    if cached is not None:
        structured = getattr(result, "structuredContent", None)
        if structured is None and isinstance(result, dict):
            structured = result.get("structuredContent")
        # Some Pydantic models — coerce to JSON-safe shape for transport.
        if structured is not None and hasattr(structured, "model_dump"):
            try:
                structured = structured.model_dump(mode="json", by_alias=True)
            except Exception:
                pass
        mcp_ui.record_resource_uri_payload(
            server_name,
            tool_name,
            cached.get("resource_uri", ""),
            structured,
        )
        return

    # Inline form: extract UI resource blocks from result.content.
    try:
        blocks = mcp_ui.extract_ui_resources(result)
    except Exception:
        logger.debug("mcp_ui.extract_ui_resources failed", exc_info=True)
        return
    if not blocks:
        return
    for block in blocks:
        mcp_ui.record(server_name, tool_name, block)


# MCP Apps' tool visibility (``_meta.ui.visibility``), per (server, tool) as
# last listed: who may call it. ``"model"``: the agent; ``"app"``: that
# server's own app, through mcp_call_tool. Unstated, both.
_TOOL_VISIBILITY: Dict[Tuple[str, str], Tuple[str, ...]] = {}
_DEFAULT_VISIBILITY = ("model", "app")


def _visibility_of(tool: Any) -> Tuple[str, ...]:
    meta = getattr(tool, "meta", None)
    if meta is None and isinstance(tool, dict):
        meta = tool.get("meta") or tool.get("_meta")
    ui = (
        (meta.get("ui") if isinstance(meta, dict) else getattr(meta, "ui", None))
        if meta
        else None
    )
    raw = (
        (
            ui.get("visibility")
            if isinstance(ui, dict)
            else getattr(ui, "visibility", None)
        )
        if ui
        else None
    )
    if isinstance(raw, (list, tuple)) and raw:
        return tuple(str(v) for v in raw)
    return _DEFAULT_VISIBILITY


def tool_visibility(server_name: str, tool_name: str) -> Optional[Tuple[str, ...]]:
    """Who may call the tool, as last listed; None when it hasn't been."""
    return _TOOL_VISIBILITY.get((server_name, tool_name))


def _tool_name_of(tool: Any) -> Optional[str]:
    name = getattr(tool, "name", None)
    if not name and isinstance(tool, dict):
        name = tool.get("name")
    return str(name) if name else None


def _harvest_tool_ui_meta(server_name: str, tools: Any) -> None:
    """Scan ``list_tools()`` output for ``_meta.ui.resourceUri`` entries
    and seed ``mcp_ui``'s tool→URI cache so we recognize the resource-URI
    flavor on subsequent ``call_tool`` invocations.
    """
    if tools is None:
        return
    items = tools if isinstance(tools, list) else getattr(tools, "tools", None) or []
    for tool in items:
        name = getattr(tool, "name", None)
        if not name and isinstance(tool, dict):
            name = tool.get("name")
        if not name:
            continue
        meta = getattr(tool, "meta", None)
        if meta is None and isinstance(tool, dict):
            meta = tool.get("meta") or tool.get("_meta")
        if not meta:
            continue
        ui_meta = (
            meta.get("ui") if isinstance(meta, dict) else getattr(meta, "ui", None)
        )
        if not ui_meta:
            continue
        uri = (
            ui_meta.get("resourceUri")
            if isinstance(ui_meta, dict)
            else getattr(ui_meta, "resourceUri", None)
        )
        if isinstance(uri, str) and uri:
            mcp_ui.register_tool_ui_resource(server_name, str(name), uri)


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


def _attach_ui_capture(server: MCPServer) -> MCPServer:
    """Wrap ``server.call_tool`` so MCP-Apps UI resources get captured.

    The upstream SDK (``agents.mcp.util.invoke_mcp_tool``) collapses
    ``CallToolResult.content`` to plain text/image dicts before our bridge
    sees it, dropping ``EmbeddedResource`` blocks. We replace ``call_tool``
    on the instance with a passthrough that stashes any UI resource blocks
    via ``mcp_ui.record`` first. Done at the instance level (not via
    subclass) so existing tests that monkey-patch the SDK classes keep
    working unchanged.
    """
    original = getattr(server, "call_tool", None)
    if original is None or not callable(original):
        # Test doubles / transports without call_tool aren't intercepted —
        # they couldn't dispatch MCP tools anyway.
        return server

    async def call_tool(tool_name, arguments=None, *args, **kwargs):
        try:
            result = await original(tool_name, arguments, *args, **kwargs)
            await _capture_ui_after_call(server, tool_name, result)
            return result
        finally:
            mcp_ui.set_invocation_id(None)

    original_resolver = getattr(server, "tool_meta_resolver", None)

    async def resolve_meta(context):
        # Runs in the SDK invocation task, unlike parallel lifecycle hooks.
        # Keep correlation local; no internal identifiers are sent to MCP.
        mcp_ui.set_invocation_id(getattr(context.run_context, "tool_call_id", None))
        if original_resolver is not None:
            value = original_resolver(context)
            return await value if inspect.isawaitable(value) else value
        return None

    try:
        server.call_tool = call_tool  # type: ignore[method-assign]
        server.tool_meta_resolver = resolve_meta
    except (AttributeError, TypeError):
        # Server type doesn't allow attribute reassignment (e.g. frozen
        # dataclass). Fall back to the original method — losing UI capture
        # for this server but staying functional.
        logger.debug(
            "Could not attach UI capture to MCP server %r",
            server,
            exc_info=True,
        )

    # Also wrap ``list_tools`` so we harvest ``_meta.ui.resourceUri`` from
    # the response and recognize FastMCP/Prefab-style apps that use the
    # resource-URI flavor of MCP Apps. The wrapper is forgiving on the
    # signature — the SDK calls ``list_tools(run_context, agent)`` but
    # tests/test-doubles may call it with no args.
    list_orig = getattr(server, "list_tools", None)
    if list_orig is not None and callable(list_orig):

        async def list_tools(*args, **kwargs):
            # One server failing (gone away, a broken connection) costs the
            # agent that server's tools for this turn, not the whole turn:
            # listing every server's tools comes before the model is called.
            try:
                tools = await list_orig(*args, **kwargs)
            except Exception:
                logger.warning(
                    "MCP server %r couldn't list its tools; leaving them out this turn",
                    getattr(server, "name", server),
                    exc_info=True,
                )
                return []
            server_name = getattr(server, "name", "") or ""
            try:
                _harvest_tool_ui_meta(server_name, tools)
            except Exception:
                logger.debug("mcp_ui list_tools harvest failed", exc_info=True)
            # MCP Apps: each tool's visibility kept (mcp_call_tool checks an
            # app's calls against it), and the agent offered only the tools
            # visible to the model; an app-only tool is its app's alone
            if isinstance(tools, list):
                offered = []
                for tool in tools:
                    name = _tool_name_of(tool)
                    visible = _visibility_of(tool)
                    if name:
                        _TOOL_VISIBILITY[(server_name, name)] = visible
                    if "model" in visible:
                        offered.append(tool)
                return offered
            return tools

        try:
            server.list_tools = list_tools  # type: ignore[method-assign]
        except (AttributeError, TypeError):
            logger.debug(
                "Could not wrap list_tools on MCP server %r",
                server,
                exc_info=True,
            )

    return server


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
        return _attach_ui_capture(MCPServerSse(cast(Any, params), **common_kwargs))
    if server_type == "stdio":
        if "command" not in params or not params["command"]:
            raise ValueError(
                f"MCP server '{cfg.name}' requires a command for stdio transport"
            )
        return _attach_ui_capture(
            QuietMCPServerStdio(cast(Any, params), **common_kwargs)
        )
    if server_type == "streamable_http":
        if "url" not in params or not params["url"]:
            raise ValueError(
                f"MCP server '{cfg.name}' requires a url for streamable_http transport"
            )
        return _attach_ui_capture(
            MCPServerStreamableHttp(cast(Any, params), **common_kwargs)
        )
    raise ValueError(
        f"Unsupported MCP server type '{cfg.type}' for server '{cfg.name}'"
    )


def build_mcp_servers(
    spec: AgentSpec,
    settings: Dict[str, Any],
    *,
    auth_resolver: Any = None,
) -> List[MCPServer]:
    """Instantiate the spec's MCP servers.

    ``auth_resolver`` (optional) maps an ``MCPServerConfig`` to an
    ``httpx.Auth`` for HTTP-family transports — the MCP management
    surface injects stored OAuth credentials here. Explicit ``auth``
    params always win.
    """
    servers: List[MCPServer] = []
    for cfg in spec.mcp_servers:
        resolved_params = _resolve_setting_placeholders(deepcopy(cfg.params), settings)
        if resolved_params is None:
            raise ValueError(f"MCP server '{cfg.name}' has params that resolve to None")
        if (
            auth_resolver is not None
            and isinstance(resolved_params, dict)
            and "auth" not in resolved_params
        ):
            try:
                auth = auth_resolver(cfg)
            except Exception:
                auth = None
            if auth is not None:
                resolved_params = dict(resolved_params)
                resolved_params["auth"] = auth
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
