# Defining Agents

OmniAgents supports two ways to define agents: YAML configuration files and Python code.

## YAML-Based Agents

YAML-based agents are the simplest way to get started. They separate configuration from code.

### Basic Structure

```yaml
name: MyAgent
instructions_file: instructions.md
tools: [tool1, tool2]
use_safe_agent: false
layout: tabs
```

### Instructions File

The `instructions_file` should be a markdown file containing the agent's system instructions:

```markdown
# MyAgent Instructions

You are a helpful assistant specialized in data analysis.

## Capabilities
- Analyze CSV files
- Generate reports
- Answer questions about data

## Guidelines
- Always explain your reasoning
- Ask clarifying questions when needed
- Be concise but thorough
```

### Tool Specification

List the names of functions decorated with `@function_tool`:

```yaml
tools:
  - calculate_sum
  - fetch_data
  - generate_report
```

### Example Projects

See the examples directory:
- `examples/basic_yaml/` - Simple YAML-based agent
- `examples/safe_agent_yaml/` - YAML agent with approval requirements

## Python-Based Agents

For more complex scenarios, you can define agents entirely in Python:

```python
from omniagents.core import function_tool, create_agent

@function_tool
def my_tool(param: str) -> str:
    """Tool description."""
    return f"Result: {param}"

instructions = """
You are a helpful assistant.
"""

agent = create_agent(
    name="MyAgent",
    instructions=instructions,
    tools=[my_tool],
    use_safe_agent=False
)
```

### Example Projects

- `examples/basic_python/` - Python-based agent
- `examples/safe_agent_python/` - Python agent with approval

## Agent Properties

### Name

The agent's display name, shown in UI interfaces:

```yaml
name: DataAnalysisAgent
```

### Instructions

System-level instructions that define the agent's behavior, personality, and capabilities. Can be:

- A file path (YAML): `instructions_file: instructions.md`
- Inline string (Python): `instructions="You are..."`

### Tools

Functions the agent can call to interact with external systems or perform actions. See [Tools](tools.md) for details.

### Safe Agent Mode

When enabled, the agent must request user approval before executing any tool:

```yaml
use_safe_agent: true
```

See [Safe Agent Mode](safe-agent.md) for details.

### Layout

UI layout configuration (currently supports `tabs` mode):

```yaml
layout: tabs
```

The `tabs` layout separates the conversation and tool execution logs into different tabs in the GUI.

## Advanced Configuration

### Custom Context

You can provide additional context to agents at runtime:

```python
context = {
    "user_id": "123",
    "session_data": {...}
}

# Context is passed when running the agent
```

### Dynamic Tool Loading

Discover tools from directories:

```python
from omniagents.core.tools import discover_functions_in_dir

tools = discover_functions_in_dir("path/to/tools")
```

## Best Practices

1. **Clear Instructions**: Write detailed, specific instructions for your agent
2. **Tool Documentation**: Document each tool's purpose and parameters
3. **Safe Mode for Destructive Actions**: Enable `use_safe_agent` for tools that modify data
4. **Version Control**: Keep your agent YAML files in version control
5. **Modular Tools**: Create focused, single-purpose tools

## Next Steps

- [Tools](tools.md) - Learn how to create tools
- [Running Modes](running-modes.md) - Explore different ways to run agents
- [Safe Agent Mode](safe-agent.md) - Add approval requirements
