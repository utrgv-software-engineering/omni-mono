# OmniAgents

OmniAgents provides a lightweight wrapper around the [OpenAI Agents](https://github.com/openai/openai-agents-python) library.
It offers several ways to run your assistant:

- **CLI** – text chat in the terminal
- **TUI** – terminal UI (Bubble Tea or React Ink)
- **GUI** – desktop-style interface built with [Flet](https://flet.dev)
- **Server** – expose an agent through a JSON‑RPC WebSocket API

Agents are described using a small YAML file which lists available tools and where to load them from.  You can also build an agent directly in Python and run it with the same interface.


## Installation

1. Clone this repository and install dependencies:

```bash
pip install -r requirements.txt
```

After publication to PyPI you will also be able to `pip install omniagents` directly.

Release wheels include prebuilt TUI assets, so end users do not need Go or Node:
- Bubble Tea: a native `omni-tui` executable for macOS, Linux, and Windows.
- Ink: a prebuilt `dist/index.js` bundle.

When running from source or editable installs, the backends can auto-build the
missing client if the respective toolchain is available, and otherwise provide
instructions.

## Configuration

Most examples expect an OpenAI API key which can be provided via the `OPENAI_API_KEY` environment variable or entered in the GUI settings screen.  Additional fields defined in your YAML file are persisted locally.  Conversation history for CLI and server sessions is stored in `sessions.db`.


## Defining an agent

A minimal YAML specification might look like:

```yaml
name: DemoAgent
instructions_file: instructions.md
# tools directory is scanned automatically
tools: [get_current_time, flip_coin]
use_safe_agent: true
# Optional UI layout: "tabs" separates conversation and tool logs
layout: tabs
```

Each tool is a Python function decorated with `@function_tool` from `omniagents.core`.  See `examples/basic_python/tools.py` for reference.

## Running

### CLI mode

Run an agent entirely in the terminal:

```bash
omniagents run -P path/to/project.yml --mode cli
```

Use `--session-id <id>` to resume a previous conversation stored in `sessions.db`.

### GUI mode

```bash
omniagents run -P path/to/project.yml --mode gui
```

The GUI persists settings such as API keys in the browser local storage.

### Server mode

```bash
omniagents run -P path/to/project.yml --mode server --host 0.0.0.0 --port 8000
```
### TUI modes

- Bubble Tea (Go):

```bash
omniagents run -P path/to/project.yml --mode tui
```

`--resume` opens a session picker. Official wheels include prebuilt binaries; from
source, the backend can auto-build if Go is available or use a vendored binary.

- React Ink (Node):

```bash
omniagents run -P path/to/project.yml --mode ink
```

Official wheels include a prebuilt bundle; from source, the backend can auto-build
if Node/npm are available.

### Studio

Serve the Studio web UI for viewing and managing traces:

```bash
omniagents studio serve --project <name>
```

Production builds serve packaged static assets; use `--dev` to run the web dev
server (requires Node/npm).

This launches a FastAPI app exposing a WebSocket endpoint at `/ws`.  The protocol is described in [`docs/remote_agent_protocol.md`](docs/remote_agent_protocol.md).  A client can connect using `RemoteAgentProxy` from this package or any JSON‑RPC library.

## Examples

- `examples/basic_python` – build an agent directly in Python
- `examples/basic_yaml` – define the agent in YAML
- `examples/safe_agent_python` and `safe_agent_yaml` – require approval before running tools
- `examples/remote_agent` – run the server and connect from another process

## Development

Run the test suite with:

```bash
pytest -q
```

Run coverage locally with:

```bash
make coverage-all
```

Individual coverage targets:

- `make coverage-python` – Python agents, FastAPI server, and backends
- `make coverage-web` – React Studio UI via Vitest
- `make coverage-go` – Bubble Tea TUI backend
