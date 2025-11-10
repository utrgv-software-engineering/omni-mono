# mcp_agent

This project demonstrates how to bundle a FastMCP server with an OmniAgents project. The agent launches a local FastMCP server over the stdio transport and exposes its `fetch` tool so the assistant can retrieve live web content.

## Setup

```bash
pip install -e .[dev]
# or: pip install -r requirements.txt
mcp_agent-setup  # configure OpenAI credentials
```

## Run

```bash
mcp_agent                       # launch the GUI
omniagents run -P project.yml   # run from source
```

The agent automatically starts `python -m mcp_agent.mcp_server` using the stdio transport.

To experiment with the HTTP transport, modify `mcp_agent/mcp_server.py` to use:

```python
if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
```

Then start the server manually and adjust the agent YAML to connect via HTTP instead of stdio.
