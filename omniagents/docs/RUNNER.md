# Runner Architecture (RESOLVED)

## Previous State

There were previously two separate implementations for running agents in different modes:

### 1. CLI Tool (`omniagents/cli.py`)
The official command-line interface supports:
- `omniagents run --mode gui` - runs GUI backend
- `omniagents run --mode cli` - runs CLI backend  
- `omniagents server` - runs server backend

This properly uses the backend loader system to instantiate the appropriate backend class.

### 2. Runner (`omniagents/core/runner.py`)
A unified runner class in core that provides multi-mode functionality:
```python
runner = Runner(config_path="agent.yaml")
runner.run(mode="gui")     # Runs Flet GUI backend
runner.run(mode="cli")     # Runs CLI backend
runner.run(mode="server")  # Runs RPC server backend
```

## Previous Problems

1. **Misplaced Code**: `AgentApp` was a general-purpose multi-mode runner living in the Flet (GUI) backend directory.

2. **Duplicate Functionality**: Both the CLI tool and `AgentApp` implemented mode switching differently.

3. **Broken Imports**: Examples tried to import from `omniagents.core.app_base` (which didn't exist).

4. **Inconsistent Architecture**: The backend system was designed for single-purpose backends, but `AgentApp` broke this.

## Solution Implemented

The architecture has been refactored following Option 3:

1. **Created `omniagents/core/runner.py`**: A unified `Runner` class that provides the official programmatic API for multi-mode execution.

2. **Simplified `AgentApp`**: Now only handles GUI functionality, with the multi-mode `run()` method removed.

3. **Updated CLI tool**: Now uses the `Runner` internally for all modes.

4. **Updated all examples**: Changed from `AgentApp` imports to use the new `Runner`.

## Current Architecture

```python
# Programmatic usage
from omniagents.core.runner import Runner

# From YAML config
runner = Runner(config_path="agent.yaml")

# From AgentSpec
runner = Runner(spec=my_agent_spec)

# From existing Agent instance (convenience method)
runner = Runner.from_agent(
    my_agent,
    welcome_text="Welcome!",
    safe=True,
    safe_tool_names=["read_file", "list_directory"]
)

# Run in any mode
runner.run(mode="gui")     # Uses FletBackend
runner.run(mode="cli")     # Uses CLIBackend  
runner.run(mode="server")  # Uses ServerBackend
```

The `Runner` class:
- Lives in core where it belongs
- Uses the backend loader system internally
- Provides a single, consistent API
- Includes `from_agent()` convenience method to avoid duplication
- Eliminates code duplication
- Fixes all architectural issues

Each backend now has a single responsibility:
- `FletBackend`: GUI implementation only (still has `AgentApp` for GUI-specific features)
- `CLIBackend`: Terminal interface only
- `ServerBackend`: WebSocket/RPC server only

The `AgentApp` class remains in the Flet backend for GUI-specific functionality and still provides its own `from_agent()` method for direct GUI usage.