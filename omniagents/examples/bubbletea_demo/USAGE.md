# Bubble Tea TUI Demo Usage Guide

This directory contains example configurations for testing the Bubble Tea TUI backend.

## Prerequisites

1. **Install Go** (if not already installed):
   - Download from https://golang.org/dl/
   - Verify installation: `go version`

2. **Build the TUI binary**:
   ```bash
   cd omniagents/backends/bubbletea
   ./build.sh
   ```

3. **Install server dependencies**:
   ```bash
   pip install omniagents[server]
   ```

## Running the Demo

### Basic Usage

```bash
# From the omniagents root directory
python -m omniagents.cli run -c examples/bubbletea_demo/hello_tui.yml --mode bubbletea
```

Or using the alias:

```bash
python -m omniagents.cli run -c examples/bubbletea_demo/hello_tui.yml --mode tui
```

### With Debug Output

See tool execution details:

```bash
python -m omniagents.cli run -c examples/bubbletea_demo/hello_tui.yml --mode tui --debug
```

### Resume a Session

First run will show a session ID, then you can resume:

```bash
python -m omniagents.cli run -c examples/bubbletea_demo/hello_tui.yml --mode tui --session-id <session-id>
```

## Interface Features

### Visual Elements

- **User Input**: Green `>` prefix
- **AI Response**: White `⏺` prefix
- **Tool Calls**: Cyan `⏺` with bold tool name
- **Status Bar**: Shows current state and session ID
- **Welcome Message**: Displays on startup

### Interactive Features

1. **Message Input**: Type and press Enter to send
2. **Tool Approval**: When safe mode is enabled, approve tool calls
3. **Session Selection**: Choose or create sessions on startup
4. **Scrolling**: Automatic viewport scrolling for long conversations

### Example Interaction

```
Bubble Tea Demo Agent [Session: abc12345]
Ready

Welcome to the Bubble Tea TUI Demo!
This is a rich terminal interface for OmniAgents with colors, Unicode characters, and interactive features.
Type your message and press Enter to chat.

> Hello, can you help me with Python?

⏺ I'd be happy to help you with Python! What specific aspect of Python would you like assistance with?

> How do I read a JSON file?

⏺ To read a JSON file in Python, you can use the built-in `json` module. Here's how:

```python
import json

# Method 1: Reading from a file
with open('data.json', 'r') as file:
    data = json.load(file)

# Now 'data' contains the parsed JSON as a Python dictionary/list
print(data)
```

⏺ The `json.load()` function automatically parses the JSON content into Python objects (dictionaries, lists, etc.).

> [Type your next message...]
```

## Comparison with Other Modes

### CLI Mode
```bash
python -m omniagents.cli run -c examples/bubbletea_demo/hello_tui.yml --mode cli
```
- Simple text interface
- No session management UI
- Basic colored output

### GUI Mode (Flet)
```bash
python -m omniagents.cli run -c examples/bubbletea_demo/hello_tui.yml --mode gui
```
- Opens in web browser
- Full graphical interface
- Requires browser

### Server Mode
```bash
python -m omniagents.cli run -c examples/bubbletea_demo/hello_tui.yml --mode server --port 8000
```
- JSON-RPC API endpoint
- For programmatic access
- No user interface

## Troubleshooting

### "omni-tui binary not found"

The Go binary hasn't been built yet:

```bash
cd omniagents/backends/bubbletea
./build.sh
```

### "command not found: go"

Go is not installed. Download from https://golang.org/dl/

### Server connection errors

Check if the Python server dependencies are installed:

```bash
pip install omniagents[server]
```

### Display issues

Ensure your terminal supports:
- UTF-8 encoding
- 256 colors or true color
- Unicode characters

Recommended terminals:
- iTerm2 (macOS)
- Windows Terminal (Windows)
- Alacritty, Kitty, or GNOME Terminal (Linux)

## Customization

### Modify Welcome Text

Edit `hello_tui.yml`:

```yaml
welcome_text: |
  Your custom welcome message here!
```

### Change Agent Name

```yaml
name: "Your Custom Agent"
```

### Disable Safe Mode

```yaml
use_safe_agent: false
```

### Add Custom Tools

Create a tools module and reference it:

```yaml
tools_module: examples.bubbletea_demo.tools
```

## Next Steps

1. Try different agent configurations
2. Test with MCP servers
3. Experiment with tool approval in safe mode
4. Create custom agents with specific capabilities

For more information, see the main README at `omniagents/backends/bubbletea/README.md`
