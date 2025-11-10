import httpx
from fastmcp import FastMCP

mcp = FastMCP("mcp-agent")


def default_headers() -> dict[str, str]:
    return {"User-Agent": "OmniAgents MCP Example"}


@mcp.tool
async def fetch(url: str) -> str:
    """Fetches a URL and returns the body text

    Args:
        url: URL to fetch
    """
    async with httpx.AsyncClient(headers=default_headers()) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

