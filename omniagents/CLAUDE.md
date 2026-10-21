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

# Typecheck (configured in pyproject.toml [tool.mypy]; runs in CI):
mypy
```

mypy must exit clean before pushing. A legacy `typecheck-debt` override
list in `pyproject.toml` exempts specific modules — fix and remove
entries when you touch those modules; never add new ones.

### Coverage
```bash
make coverage-all          # All coverage (Python, web)
make coverage-python       # Python agents, FastAPI server, backends
make coverage-web          # React Studio UI via Vitest
```

### Running the Application

**Web Mode (default):**
```bash
python -m omniagents.cli run -c path/to/agent.yaml --mode web
```

**Ink TUI Mode:**
```bash
python -m omniagents.cli run -c path/to/agent.yaml --mode ink
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
   - **Web**: React-based web UI (default, includes optional realtime voice controls)
   - **Ink**: React Ink terminal UI
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
  - `/omniagents/backends/`: UI/interface implementations (server, web, ink)
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
   - Sessions can be resumed via `--session-id` flag

5. **Configuration**:
   - API keys via environment variables (e.g., `OPENAI_API_KEY`)
   - Agent-specific settings in YAML files (name, instructions_file, tools, use_safe_agent, layout)
  - Optional dependencies controlled via `pyproject.toml` extras: `[server]`, `[web]`, `[studio]`, `[studio-server]`, `[all]`, `[dev]`

6. **Multi-Language Components**:
   - **Python**: Core framework, backends, RPC implementation
   - **JavaScript/React**: Web UIs in `omniagents/backends/web/`, `omniagents/backends/ink/`, and `omniagents/studio/server/web/`

### Testing

Tests use pytest with fixtures defined in `tests/conftest.py`. The test suite uses temporary databases to ensure isolation. Coverage is collected for Python and web (Vitest) components separately.

#### Sandbox-substrate suites (Docker)

`tests/integration/test_git_sandbox_substrate.py` and
`tests/integration/test_fs_sandbox_substrate.py` drive the `git_*` / `fs_*`
surfaces against a **real container**, because the substrate-dependent
defects they exist for (absolute `FileEntry` paths, an unconfined
`SandboxWorkspace.resolve`, `fs_stat` reporting `size: null`, a
bound-method `is_dir`) are all invisible to the host-only suites.

They skip unless `alpine/git:latest` is already present locally, and they
never pull it themselves:

```bash
docker pull alpine/git:latest
pytest tests/integration/test_git_sandbox_substrate.py \
       tests/integration/test_fs_sandbox_substrate.py
```

CI runs them in the separate `sandbox-substrate` job
(`.github/workflows/omniagents-tests.yml` at the repository root), which pulls the image and sets
`OMNIAGENTS_REQUIRE_SANDBOX_IMAGE=1` — that turns the skip into a
collection error, so the job cannot pass by skipping everything.

**Runtime: 1m42s for the job on `ubuntu-latest`** — 66s of that is pytest
(31 tests, each creating and deleting its own container), the rest is the
image pull and the pip install. Locally, ~53s for the two modules on a
warm image. Keep the job off the main matrix: the cost is wall-clock
container time, not CPU.

### Git Workflow Notes

- **Always check branch and working tree status** before making changes (`git status -sb`)
- **Work on `dev`**; `main` holds released code only, and `make publish` refuses to run off a clean, in-sync `main`. Release order and steps: the launcher's CLAUDE.md, "Branches and releases"
- **Never modify unrelated dirty files** - surface dirty state to user and wait for guidance
- **Run Black formatter on all touched Python files** before every commit
- **Do not commit `docs/TESTING_ISSUES.md`** from inside a worktree
- See `AGENTS.md` for detailed agent workflow rules
