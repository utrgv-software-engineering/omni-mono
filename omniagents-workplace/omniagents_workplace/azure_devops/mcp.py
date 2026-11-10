import contextlib
import json

from fastapi import HTTPException
from mcp import types
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import ValidationError

from ..mcp_server import current_request, tool_server
from .tools import TOOLS, execute, model_for


class AzureDevOpsMCP:
    def __init__(self, connections):
        self.connections = connections
        self.server = tool_server(
            "azure-devops", list_tools=self.list_tools, call_tool=self.call_tool
        )
        self.manager = StreamableHTTPSessionManager(
            self.server, stateless=True, json_response=False
        )

    @contextlib.asynccontextmanager
    async def run(self):
        async with self.manager.run():
            yield

    def caller(self):
        return self.connections.app_for_token(
            current_request().headers.get("authorization", "")
        )

    async def list_tools(self):
        app_id = self.caller()
        permitted = set()
        for value in self.connections.records().values():
            if value["enabled"] and app_id in value["app_ids"]:
                permitted.update(value["write_tools"])
        return [
            types.Tool(
                name=name,
                description=tool.title,
                inputSchema=model_for(name).model_json_schema(),
                annotations=types.ToolAnnotations(
                    readOnlyHint=not tool.write,
                    destructiveHint=tool.destructive,
                    idempotentHint=not tool.write,
                    openWorldHint=True,
                ),
            )
            for name, tool in TOOLS.items()
            if not tool.write or name in permitted
        ]

    async def call_tool(self, name, arguments):
        try:
            value = await execute(self.connections, self.caller(), name, arguments)
            text = json.dumps(value, indent=2)
            if (arguments or {}).get("response_format") == "markdown":
                text = "## " + TOOLS[name].title + "\n\n```json\n" + text + "\n```"
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)]
            )
        except (HTTPException, ValidationError) as exc:
            message = (
                exc.detail
                if isinstance(exc, HTTPException)
                else "Invalid tool arguments"
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(message))],
                isError=True,
            )
        except Exception:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text="Azure DevOps tool failed; check the connection health and host configuration",
                    )
                ],
                isError=True,
            )

    async def __call__(self, scope, receive, send):
        headers = {
            key.decode().lower(): value.decode()
            for key, value in scope.get("headers", [])
        }
        try:
            self.connections.app_for_token(headers.get("authorization", ""))
        except HTTPException:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"www-authenticate", b"Bearer")],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self.manager.handle_request(scope, receive, send)
