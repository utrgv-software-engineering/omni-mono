# Backends Overview

OmniAgents uses a backend architecture to provide multiple interface modes for running agents.

## Backend System

Backends are dynamically loaded via entry points defined in `pyproject.toml`. Each backend implements a common interface for agent execution.

## Available Backends

| Backend | Mode Flag | Description | Built With |
|---------|-----------|-------------|------------|
| CLI | `--mode cli` | Text-based terminal chat | Python |
| TUI (Bubble Tea) | `--mode tui` | Rich terminal UI | Go |
| TUI (Ink) | `--mode ink` | React terminal UI | TypeScript/React |
| GUI | `--mode gui` | Desktop interface | Python/Flet |
| Server | `--mode server` | JSON-RPC WebSocket API | Python/FastAPI |

## Backend Architecture

```
omniagents/backends/
├── cli/          # Terminal text interface
├── bubbletea/    # Go TUI backend
├── ink/          # React Ink TUI backend
├── flet/         # Flet GUI backend
├── server/       # FastAPI server backend
├── voice/        # Voice interface (experimental)
└── web/          # Web interface (experimental)
```

## Choosing a Backend

Consider:

1. **User Experience**: CLI for simple, GUI for visual
2. **Environment**: TUI for SSH, Server for remote
3. **Dependencies**: Some backends require optional dependencies
4. **Platform**: All backends are cross-platform

## Backend Features

### Common Features

All backends support:
- Agent execution
- Tool calling
- Message streaming
- Session management

### Backend-Specific Features

- **GUI/TUI**: Visual approval dialogs
- **Server**: Remote client connections
- **CLI**: Scriptable, automatable

## Installation

Backends have optional dependencies:

```bash
# GUI backend
pip install -e ".[flet]"

# Server backend
pip install -e ".[server]"

# All backends
pip install -e ".[all]"
```

## Entry Points

Backends are registered in `pyproject.toml`:

```toml
[project.entry-points."omniagents.backends"]
cli = "omniagents.backends.cli:CLIBackend"
tui = "omniagents.backends.bubbletea:BubbleTeaBackend"
gui = "omniagents.backends.flet:FletBackend"
server = "omniagents.backends.server:ServerBackend"
```

## Next Steps

- [CLI Backend](cli.md)
- [GUI Backend](gui.md)
- [TUI Backend](tui.md)
- [Server Backend](server.md)
