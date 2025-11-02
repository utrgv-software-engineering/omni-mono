# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Installing Dependencies
```bash
pip install -r requirements.txt
# Or for development with all optional dependencies:
make dev
```

### Running Tests
```bash
pytest -q
# Run a single test file:
pytest tests/path/to/test_file.py -q
# Run with coverage:
pytest --cov=omniagents
```

### Code Quality
```bash
# Format Python code with Black before every commit:
black .
# Or format specific files:
black path/to/file.py
```

### Coverage
```bash
make coverage-all          # All coverage (Python, web, Go)
make coverage-python       # Python agents, FastAPI server, backends
make coverage-web          # React Studio UI via Vitest
make coverage-go           # Bubble Tea TUI backend
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

### Version Management
```bash
make bump-patch   # 0.1.0 -> 0.1.1
make bump-minor   # 0.1.0 -> 0.2.0
make bump-major   # 0.1.0 -> 1.0.0
```

## Architecture Overview

OmniAgents is a wrapper around OpenAI's agents library (`openai-agents==0.4.2`) that provides multiple interfaces for running AI agents.

### Core Components

1. **Agent Definition**: Agents can be defined via:
   - YAML configuration files (see `examples/basic_yaml/` or `examples/safe_agent_yaml/`)
   - Python code using the `@function_tool` decorator (see `examples/basic_python/`)

2. **Backend Modes** (selected via `--mode` flag or entry point):
   - **CLI**: Terminal-based chat interface using Bubble Tea TUI (Go)
   - **GUI/Flet**: Cross-platform desktop app built with Flet (Flutter-based)
   - **Server**: FastAPI server exposing agents via WebSocket JSON-RPC protocol
   - Backends are dynamically loaded via entry points defined in `pyproject.toml` (`[project.entry-points."omniagents.backends"]`)

3. **Key Directories**:
   - `/omniagents/core/`: Core functionality including:
     - `agents/`: Agent creation and lifecycle
     - `tools/`: Tool discovery and `@function_tool` decorator
     - `config/`: YAML configuration loading
     - `runtime/`: Agent execution runtime
     - `session/`: Session and conversation history management (SQLite)
     - `guardrails/`: Safety and validation rules
     - `server_functions/`: Server-side functions exposed to RPC clients
     - `evaluation/`: Agent evaluation framework
     - `project/`: Project scaffolding templates
   - `/omniagents/backends/`: UI/interface implementations (cli, flet, server, bubbletea)
   - `/omniagents/rpc/`: JSON-RPC protocol implementation for remote agent communication
   - `/omniagents/studio/`: Web-based UIs:
     - `tracing/`: Trace visualization UI
     - `server/`: Studio server with React frontend
   - `/examples/`: Various example implementations showcasing different features
   - `/tests/`: Test suite with fixtures in `conftest.py`

### Important Architectural Details

1. **Tool System**:
   - Tools are Python functions decorated with `@function_tool` or `@rich_function_tool`
   - The framework automatically discovers tools via `discover_functions_in_dir()` in specified directories or modules
   - Tools can return `RichToolOutput` with metadata for enhanced UI rendering
   - Approval metadata can be attached via `@approval_metadata()` decorator for rich approval dialogs

2. **Safe Agent Mode**:
   - When `use_safe_agent: true` is set in YAML config, the agent requires user approval before executing tools
   - Approval is handled via the RPC protocol's `client_request` mechanism with `function: "ui.request_tool_approval"`

3. **Remote Agent Protocol (JSON-RPC 2.0)**:
   - WebSocket-based bidirectional communication (server events + client RPC calls)
   - Stateful sessions with conversation history maintained server-side
   - **Client Functions**: Server can invoke capabilities on connected clients (e.g., `ui.request_tool_approval`, `ui.add_artifact`)
   - **Server Functions**: Clients can call server capabilities via generic `server_call()` RPC method
   - Supports streaming events: `tool_called`, `tool_result`, `message_output`, `run_end`, `token` (usage), `run_status` (retry notifications)
   - **Variables vs Context**: Clients send untyped `variables` dict; server constructs typed context via factory function
   - See full protocol details in `omniagents/rpc/protocol.md`

4. **Storage**:
   - Conversation history stored in SQLite (`sessions.db` at repo root or specified path)
   - GUI settings (API keys, etc.) persisted in browser local storage (Flet)
   - Sessions can be resumed via `--session-id` flag

5. **Configuration**:
   - API keys via environment variables (e.g., `OPENAI_API_KEY`)
   - Agent-specific settings in YAML files (name, instructions_file, tools, use_safe_agent, layout)
   - Runtime settings can be modified in GUI mode
   - Optional dependencies controlled via `pyproject.toml` extras: `[cli]`, `[flet]`, `[gui]`, `[server]`, `[studio]`, `[studio-server]`, `[all]`, `[dev]`

6. **Multi-Language Components**:
   - **Python**: Core framework, backends, RPC implementation
   - **Go**: Bubble Tea TUI in `omniagents/backends/bubbletea/cmd/omni-tui/`
   - **JavaScript/React**: Studio web UIs in `omniagents/studio/server/web/`
   - Binary wheels include prebuilt Bubble Tea TUI for macOS, Linux, Windows

### Testing

Tests use pytest with fixtures defined in `tests/conftest.py`. The test suite mocks the Flet UI framework (via a minimal stub module) and uses temporary databases to ensure isolation. Coverage is collected for Python, Go (Bubble Tea), and web (Vitest) components separately.

### Git Workflow Notes

- **Always check branch and working tree status** before making changes (`git status -sb`)
- **Never modify unrelated dirty files** - surface dirty state to user and wait for guidance
- **Run Black formatter on all touched Python files** before every commit
- **Do not commit `docs/TESTING_ISSUES.md`** from inside a worktree
- See `AGENTS.md` for detailed agent workflow rules