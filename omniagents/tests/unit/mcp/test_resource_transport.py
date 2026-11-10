import httpx2
import pytest
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route

from omniagents.core.agents.mcp import _instantiate_server
from omniagents.core.agents.specs import MCPServerConfig


@pytest.mark.asyncio
async def test_large_app_resource_survives_actual_sse_transport():
    html = "<!doctype html>" + "x" * (2 * 1024 * 1024)

    async def listed(ctx, params):
        return types.ListToolsResult(tools=[])

    async def read(ctx, params):
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=str(params.uri),
                    mime_type="text/html;profile=mcp-app",
                    text=html,
                )
            ]
        )

    manager = StreamableHTTPSessionManager(
        Server("large-app", on_list_tools=listed, on_read_resource=read),
        json_response=False,
        stateless=True,
    )

    async def endpoint(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    class Endpoint:
        async def __call__(self, scope, receive, send):
            await endpoint(scope, receive, send)

    app = Starlette(
        routes=[Route("/mcp", Endpoint(), methods=["GET", "POST", "DELETE"])]
    )
    params = {
        "url": "http://testserver/mcp",
        "httpx_client_factory": lambda **kwargs: httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), **kwargs
        ),
    }
    config = MCPServerConfig(name="large-app", type="streamable_http", params=params)
    async with manager.run():
        async with _instantiate_server(config, params) as client:
            resource = await client.session.read_resource("ui://large-app")
            assert resource.contents[0].text == html
