"""Minimal stdio MCP server used by the MCP management integration tests.

Runs over stdio via MCPServer and exposes:

* ``ping`` — trivial tool (startup/status tests).
* ``ask_name`` — issues an MCP ``elicitation/create`` request (form
  mode) and echoes the accepted answer, exercising the #308 routing.
"""

from typing import Annotated
from pydantic import BaseModel

from mcp.server.mcpserver import Context, MCPServer, Elicit, ElicitationResult, Resolve

mcp = MCPServer("elicit-fixture")


class NameSchema(BaseModel):
    name: str


@mcp.tool()
async def ping() -> str:
    return "pong"


@mcp.tool()
async def ask_name(
    result: Annotated[
        ElicitationResult[NameSchema],
        Resolve(lambda: Elicit("What is your name?", NameSchema)),
    ],
) -> str:
    if result.action == "accept" and result.data is not None:
        return f"hello {result.data.name}"
    return f"no answer ({result.action})"


if __name__ == "__main__":
    mcp.run()
