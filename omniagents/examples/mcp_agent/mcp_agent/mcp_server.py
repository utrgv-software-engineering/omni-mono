from typing import Any, cast

import anyio
import click
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.shared._httpx_utils import create_mcp_http_client


def default_headers() -> dict[str, str]:
    return {"User-Agent": "OmniAgents MCP Example"}


async def fetch_website(url: str) -> list[types.ContentBlock]:
    async with create_mcp_http_client(headers=default_headers()) as client:
        response = await client.get(url)
        response.raise_for_status()
        return [types.TextContent(type="text", text=response.text)]


@click.command()
@click.option("--port", default=8000, show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio", show_default=True)
def main(port: int, host: str, transport: str) -> int:
    app = Server("mcp-agent")

    @app.call_tool()
    async def fetch_tool(name: str, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        if name != "fetch":
            raise ValueError(f"Unknown tool: {name}")
        url = arguments.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("Missing required argument 'url'")
        return await fetch_website(url)

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="fetch",
                title="Website Fetcher",
                description="Fetches a URL and returns the body text",
                inputSchema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {"url": {"type": "string", "description": "URL to fetch"}},
                },
            )
        ]

    if transport == "sse":
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import Response
        from starlette.routing import Mount, Route
        from starlette.types import Receive, Scope, Send
        from mcp.server.sse import SseServerTransport

        sse = SseServerTransport("/messages/")

        async def handle_sse(request: Request) -> Response:
            scope = cast(Scope, request.scope)
            receive = cast(Receive, request.receive)
            send = cast(Send, request._send)
            async with sse.connect_sse(scope, receive, send) as streams:
                await app.run(streams[0], streams[1], app.create_initialization_options())
            return Response()

        starlette_app = Starlette(
            debug=True,
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages/", app=sse.handle_post_message),
            ],
        )

        import uvicorn

        uvicorn.run(starlette_app, host=host, port=port)
    else:
        from mcp.server.stdio import stdio_server

        async def run_stdio() -> None:
            async with stdio_server() as streams:
                await app.run(streams[0], streams[1], app.create_initialization_options())

        anyio.run(run_stdio)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
