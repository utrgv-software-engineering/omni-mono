# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running Tests
```bash
pytest -q
```

### Running the Application

**CLI Mode:**
```bash
python -m omniagents.cli run -c path/to/agent.yaml --mode cli
# Resume previous session:
python -m omniagents.cli run -c path/to/agent.yaml --mode cli --session-id <id>
```

**GUI Mode (default):**
```bash
python -m omniagents.cli run -c path/to/agent.yaml
```

**Server Mode:**
```bash
python -m omniagents.cli run -c path/to/agent.yaml --mode server --host 0.0.0.0 --port 8000
```

### Installing Dependencies
```bash
pip install -r requirements.txt
```

Note: This project currently has no configured linting or formatting tools.

## Architecture Overview

OmniAgents is a wrapper around OpenAI's agents library that provides multiple interfaces for running AI agents:

### Core Components

1. **Agent Definition**: Agents can be defined via:
   - YAML configuration files (see `examples/basic_yaml/`)
   - Python code using the `@function_tool` decorator (see `examples/basic_python/`)

2. **Execution Modes**:
   - **CLI**: Terminal-based chat interface
   - **GUI**: Cross-platform desktop app built with Flet (Flutter-based)
   - **Server**: FastAPI server exposing agents via WebSocket JSON-RPC protocol

3. **Key Directories**:
   - `/omniagents/core/`: Core functionality including agent creation, tool management, and UI components
   - `/omniagents/jsonrpc/`: JSON-RPC protocol implementation for remote communication
   - `/omniagents/server/`: FastAPI server for hosting agents remotely
   - `/examples/`: Various example implementations showcasing different features

### Important Features

1. **Safe Agent Mode**: When `use_safe_agent: true` is set in YAML config, the agent requires user approval before executing tools.

2. **Tool System**: Tools are Python functions decorated with `@function_tool`. The framework automatically discovers tools in the specified directory or module.

3. **Remote Agent Protocol**: Agents can be hosted on a server and accessed remotely via WebSocket. See `docs/remote_agent_protocol.md` for protocol details.

4. **Storage**: 
   - Conversation history is stored in SQLite (`sessions.db`)
   - GUI settings (API keys, etc.) are persisted in browser local storage

5. **Configuration**: 
   - API keys via environment variables (e.g., `OPENAI_API_KEY`)
   - Agent-specific settings in YAML files
   - Runtime settings can be modified in GUI mode

### Testing

Tests use pytest with fixtures defined in `tests/conftest.py`. The test suite mocks the Flet UI framework and uses temporary databases to ensure isolation.