"""Shared MCP 2 request adapters for Workplace service tools."""

from contextvars import ContextVar
from typing import Any
from mcp import types
from mcp.server.lowlevel import Server

_request: ContextVar[Any] = ContextVar("workplace_mcp_request")


def current_request() -> Any:
    return _request.get()


def tool_server(name: str, *, list_tools, call_tool, **options) -> Server:
    async def listed(ctx, params):
        token = _request.set(ctx.request)
        try:
            return types.ListToolsResult(tools=await list_tools())
        finally:
            _request.reset(token)

    async def called(ctx, params):
        token = _request.set(ctx.request)
        try:
            result = await call_tool(params.name, params.arguments)
            return (
                result
                if isinstance(result, types.CallToolResult)
                else types.CallToolResult(content=result)
            )
        finally:
            _request.reset(token)

    return Server(name, on_list_tools=listed, on_call_tool=called, **options)
