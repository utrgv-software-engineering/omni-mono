"""Minimal stdio MCP server used by the MCP management integration tests.

Runs over stdio via FastMCP and exposes:

* ``ping`` — trivial tool (startup/status tests).
* ``ask_name`` — issues an MCP ``elicitation/create`` request (form
  mode) and echoes the accepted answer, exercising the #308 routing.
"""

from pydantic import BaseModel

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("elicit-fixture")


class NameSchema(BaseModel):
    name: str


@mcp.tool()
async def ping() -> str:
    return "pong"


@mcp.tool()
async def ask_name(ctx: Context) -> str:
    result = await ctx.elicit(message="What is your name?", schema=NameSchema)
    if result.action == "accept" and result.data is not None:
        return f"hello {result.data.name}"
    return f"no answer ({result.action})"


if __name__ == "__main__":
    mcp.run()
