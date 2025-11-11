# Core API Reference

Core functionality for creating and running agents.

## Agent Creation

::: omniagents.core.agents.create_agent

## Function Tools

::: omniagents.core.tools.function_tool

::: omniagents.core.tools.rich_function_tool

## Tool Discovery

::: omniagents.core.tools.discover_functions_in_dir

## Tool Output

::: omniagents.core.tools.RichToolOutput

## Approval Metadata

::: omniagents.core.tools.approval_metadata

## Configuration

::: omniagents.core.config.AgentConfig

## Manual Usage

For advanced use cases, you can interact with agents programmatically:

```python
from omniagents.core import create_agent, function_tool

@function_tool
def my_tool() -> str:
    return "Result"

agent = create_agent(
    name="MyAgent",
    instructions="You are a helpful assistant.",
    tools=[my_tool]
)

# Run agent
response = agent.run("Hello!")
print(response)
```

## Next Steps

- [Tools API](tools.md) - Tool-specific API reference
- [RPC Protocol](rpc-protocol.md) - Remote agent protocol
