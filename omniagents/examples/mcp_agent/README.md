# mcp_agent

This project demonstrates how to bundle an MCP server with an OmniAgents project. The agent launches a local MCP server over the stdio transport and exposes its `fetch` tool so the assistant can retrieve live web content.

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

The agent automatically starts `python -m mcp_agent.mcp_server --transport stdio`. Update the `mcp_python` setting if you need a different interpreter.

To experiment with the SSE transport, start the server separately:

```bash
python -m mcp_agent.mcp_server --transport sse --host 127.0.0.1 --port 8000
```

Then adjust the agent YAML to point at the SSE endpoint and disable the stdio server entry.
