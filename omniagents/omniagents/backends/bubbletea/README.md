# Bubble Tea TUI Backend for OmniAgents

A beautiful terminal user interface (TUI) backend for OmniAgents, built with Go and the Bubble Tea framework.

## Features

- 🎨 **Rich Terminal UI**: Interactive interface with colors and Unicode characters
- 💬 **Real-time Streaming**: See agent responses as they're generated
- 🔒 **Tool Approval**: Interactive approval dialogs for safe mode
- 📝 **Session Management**: List, select, and resume previous conversations
- 🎯 **Full Feature Parity**: Supports all OmniAgents features including MCP servers

## Prerequisites

- Go 1.21 or later (for building the TUI client)
- Python with OmniAgents installed
- Server backend dependencies: `pip install omniagents[server]`

## Building

1. Install Go from https://golang.org/dl/

2. Build the TUI binary:
   ```bash
   cd omniagents/backends/bubbletea
   ./build.sh
   ```

   Or manually:
   ```bash
   cd omniagents/backends/bubbletea/cmd/omni-tui
   go mod download
   go build -o ../../bin/omni-tui
   ```

## Usage

### Basic Usage

Run an agent with the Bubble Tea TUI:

```bash
python -m omniagents.cli run -c path/to/agent.yml --mode bubbletea
```

Or use the alias:

```bash
python -m omniagents.cli run -c path/to/agent.yml --mode tui
```

### With Session Resume

Resume a previous conversation:

```bash
python -m omniagents.cli run -c path/to/agent.yml --mode tui --session-id <session-id>
```

### Debug Mode

Enable debug output to see tool results:

```bash
python -m omniagents.cli run -c path/to/agent.yml --mode tui --debug
```

### Safe Mode

When the agent is configured with `use_safe_agent: true`, you'll see approval dialogs for tool calls:

```
⚠ Tool Approval Required

Tool: search_web
Arguments: query: "latest AI news"

Do you want to allow this tool call?
  [1] Yes
  [2] Yes, and don't ask again for this session
  [3] No, cancel this tool call

Your choice [1-3]: _
```

## Interface Guide

### Message Types

- `>` Green text: Your messages
- `⏺` White text: Agent responses  
- `⏺` Cyan text: Tool calls with formatted arguments
- Gray text (debug mode): Tool results

### Keyboard Shortcuts

- **Enter**: Send message or confirm approval
- **Arrow Keys**: Navigate session list
- **Tab**: Create new session (in session list)
- **Ctrl+C** or **Esc**: Exit

### States

The TUI displays the current state in the header:
- **Ready**: Waiting for input
- **Thinking...**: Agent is processing
- **Approval Required**: Waiting for tool approval
- **Connecting...**: Establishing server connection

## Architecture

The Bubble Tea backend uses a client-server architecture:

1. **Python Backend** (`__init__.py`):
   - Launches the FastAPI server in background
   - Finds and executes the Go binary
   - Passes configuration via command-line flags

2. **Go TUI Client** (`cmd/omni-tui/`):
   - Connects to server via WebSocket
   - Implements JSON-RPC 2.0 protocol
   - Renders interactive terminal UI
   - Handles user input and events

3. **Communication**:
   - WebSocket with JSON-RPC 2.0
   - Real-time event streaming
   - Bidirectional tool approval flow

## Development

### Project Structure

```
omniagents/backends/bubbletea/
├── __init__.py          # Python backend wrapper
├── build.sh             # Build script
├── README.md            # This file
├── bin/                 # Compiled binary (after build)
│   └── omni-tui
└── cmd/omni-tui/        # Go source code
    ├── main.go          # Entry point
    ├── model.go         # Bubble Tea model
    ├── rpc.go           # RPC client
    ├── events.go        # Event types
    ├── ui.go            # UI rendering
    └── go.mod           # Go module file
```

### Testing

Test with example agents:

```bash
# Basic test
python -m omniagents.cli run -c examples/basic_yaml/hello.yml --mode tui

# Safe mode test
python -m omniagents.cli run -c examples/safe_agent_yaml/safe_weather.yml --mode tui

# MCP server test (if configured)
python -m omniagents.cli run -c examples/mcp_yaml/mcp_agent.yml --mode tui
```

### Debugging

1. **Enable debug mode** to see tool results:
   ```bash
   export OMNI_DEBUG=1
   python -m omniagents.cli run -c agent.yml --mode tui --debug
   ```

2. **Check server logs** if connection fails:
   ```bash
   # The server runs on a random port, shown in error messages
   ```

3. **Verify binary exists**:
   ```bash
   ls -la omniagents/backends/bubbletea/bin/omni-tui
   ```

## Comparison with Other Backends

| Feature | CLI | Bubble Tea TUI | Flet GUI | Server |
|---------|-----|----------------|----------|--------|
| Rich UI | ❌ | ✅ | ✅ | N/A |
| Colors | ✅ | ✅ | ✅ | N/A |
| Mouse Support | ❌ | ✅ | ✅ | N/A |
| Session List | ❌ | ✅ | ✅ | N/A |
| Remote Access | ❌ | ❌ | ✅ | ✅ |
| No Browser | ✅ | ✅ | ❌ | ✅ |

## Troubleshooting

### "omni-tui binary not found"

Build the binary first:
```bash
cd omniagents/backends/bubbletea
./build.sh
```

### "Server dependencies not installed"

Install the server backend:
```bash
pip install omniagents[server]
```

### "Failed to connect to server"

The Python backend automatically starts a server. If it fails:
1. Check if another process is using ports
2. Verify firewall settings allow localhost connections
3. Try with debug mode for more information

### Session Management Issues

Sessions are stored in SQLite. To reset:
```bash
rm sessions.db
```

## License

Same as OmniAgents project.
