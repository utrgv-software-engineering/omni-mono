"""Small local protocol server for real MCP lifecycle verification."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("lifecycle-test")


@server.tool()
def echo(text: str) -> str:
    return text


if __name__ == "__main__":
    server.run(transport="stdio")
