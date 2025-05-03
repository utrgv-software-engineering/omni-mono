from mcp.server.fastmcp import FastMCP
# from starlette.applications import Starlette
# from starlette.routing import Mount

mcp = FastMCP("Echo", sse_path="/mcp/sse")


@mcp.resource("echo://{message}")
def echo_resource(message: str) -> str:
    """Echo a message as a resource"""
    return f"Resource echo: {message}"


@mcp.tool()
def echo_tool(message: str) -> str:
    """Echo a message as a tool"""
    return f"Tool echo: {message}"


@mcp.prompt()
def echo_prompt(message: str) -> str:
    """Create an echo prompt"""
    return f"Please process this message: {message}"

# To run this server, you can use the command:
# mcp run server.py
# It will typically run on http://localhost:3000

# If you need to run it programmatically (e.g., with uvicorn):
if __name__ == "__main__":
    import uvicorn

    # Create a Starlette app and mount the MCP SSE app at /mcp/sse
    # app = Starlette(
    #     routes=[
    #         # Mount with a trailing slash
    #         Mount('/mcp/sse/', app=mcp.sse_app()),
    #     ]
    # )

    # Run the Starlette app with uvicorn
    # uvicorn.run(app, host="127.0.0.1", port=3000)

    # Run the FastMCP SSE app directly with uvicorn
    # The sse_path will be configured in the FastMCP constructor
    uvicorn.run(mcp.sse_app(), host="127.0.0.1", port=3000) 