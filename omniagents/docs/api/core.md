# Core API Reference

Core functionality for creating and running agents.

## Agent Specifications

### AgentSpec

The main configuration class for defining agents.

```python
from omniagents.core import AgentSpec

spec = AgentSpec(
    name="MyAgent",
    instructions="You are a helpful assistant.",
    model="gpt-4o-mini",
    tools=[],
    use_safe_agent=False
)
```

**Parameters:**

- `name` (str): Agent display name
- `instructions` (str): System-level instructions
- `model` (str, optional): Model to use (default: "gpt-4o-mini")
- `tools` (list, optional): List of tool functions
- `use_safe_agent` (bool, optional): Enable approval mode (default: False)

### SafeAgentOptions

Options for configuring Safe Agent behavior.

```python
from omniagents.core import SafeAgentOptions

options = SafeAgentOptions(
    require_approval=True,
    auto_approve=[]
)
```

## Configuration Loading

### load_agent_spec_from_yaml

Load agent configuration from a YAML file.

```python
from omniagents.core import load_agent_spec_from_yaml

spec = load_agent_spec_from_yaml("agent.yml")
```

**Parameters:**

- `path` (str): Path to YAML configuration file

**Returns:**

- `AgentSpec`: Agent specification object

## Running Agents

Agents are typically run through the CLI or backend systems:

```bash
# CLI
omniagents run -P agent.yml --mode cli

# GUI
omniagents run -P agent.yml --mode gui

# Server
omniagents run -P agent.yml --mode server
```

For programmatic usage with the underlying agents library, refer to the [openai-agents documentation](https://github.com/openai/openai-agents-python).

## Tool System

See [Tools API](tools.md) for detailed tool system documentation.

## Server Functions

### server_function

Decorator to mark a function as a server-side capability.

```python
from omniagents.core import server_function

@server_function
def my_server_function(arg: str) -> str:
    """Server-side function."""
    return f"Result: {arg}"
```

### discover_server_functions_in_dir

Discover server functions in a directory.

```python
from omniagents.core import discover_server_functions_in_dir

functions = discover_server_functions_in_dir("path/to/functions")
```

## Artifacts

### display_artifact

Display an artifact to the UI.

```python
from omniagents.core import display_artifact

display_artifact(
    title="Report",
    content="# Summary\n...",
    mode="markdown"
)
```

**Parameters:**

- `title` (str): Artifact title
- `content` (str): Artifact content
- `mode` (str): Display mode ("markdown", "code", etc.)

## Example Usage

```python
from omniagents.core import AgentSpec, load_agent_spec_from_yaml

# Load from YAML
spec = load_agent_spec_from_yaml("agent.yml")

# Or create programmatically
spec = AgentSpec(
    name="MyAgent",
    instructions="You are a helpful assistant.",
    tools=[]
)
```

## Next Steps

- [Tools API](tools.md) - Tool-specific API reference
- [RPC Protocol](rpc-protocol.md) - Remote agent protocol
