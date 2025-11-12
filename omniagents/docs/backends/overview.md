# Backends Overview

OmniAgents uses a backend architecture to provide multiple interface modes for running agents.

## Backend System

Backends are dynamically loaded via entry points defined in `pyproject.toml`. Each backend implements a common interface for agent execution.

## Available Backends

| Backend | Mode Flag | Description | Built With |
|---------|-----------|-------------|------------|
| Web | `--mode web` | Browser-based interface | TypeScript/React |
| Ink | `--mode ink` | React terminal UI | TypeScript/React |
| Voice | `--mode voice` | Voice interface | Python |
| Server | `--mode server` | JSON-RPC WebSocket API | Python/FastAPI |

## Backend Architecture

```
omniagents/backends/
├── web/          # Browser-based web interface
├── ink/          # React Ink TUI backend
├── voice/        # Voice interface
└── server/       # FastAPI server backend
```

## Choosing a Backend

Consider:

1. **User Experience**: Web for visual interface, Ink for terminal
2. **Environment**: Web/Voice for local, Server for remote
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

- **Web**: Visual approval dialogs, rich UI components
- **Ink**: Terminal-based interaction with React components
- **Voice**: Audio input/output, natural conversation
- **Server**: Remote client connections, JSON-RPC API

## Installation

Backends have optional dependencies:

```bash
# Web backend
pip install -e ".[web]"

# Server backend
pip install -e ".[server]"

# Voice backend
pip install -e ".[voice]"

# All backends
pip install -e ".[all]"
```

## Entry Points

Backends are registered in `pyproject.toml`:

```toml
[project.entry-points."omniagents.backends"]
web = "omniagents.backends.web:WebBackend"
ink = "omniagents.backends.ink:InkBackend"
voice = "omniagents.backends.voice:VoiceBackend"
server = "omniagents.backends.server:ServerBackend"
```

## Next Steps

- [Web Backend](web.md)
- [Ink Backend](ink.md)
- [Server Backend](server.md)
