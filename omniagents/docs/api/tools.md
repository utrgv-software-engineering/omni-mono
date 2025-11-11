# Tools API Reference

API reference for the tool system.

## Decorators

### @function_tool

Decorator to mark a function as an agent tool.

```python
from omniagents.core import function_tool

@function_tool
def my_tool(param: str) -> str:
    """Tool description."""
    return f"Result: {param}"
```

::: omniagents.core.tools.function_tool

### @rich_function_tool

Decorator for tools that return structured output with metadata.

```python
from omniagents.core import rich_function_tool, RichToolOutput

@rich_function_tool
def my_rich_tool(param: str) -> RichToolOutput:
    """Tool with rich output."""
    return RichToolOutput(
        output={"result": param},
        metadata={"type": "analysis"}
    )
```

::: omniagents.core.tools.rich_function_tool

### @approval_metadata

Add rich metadata for tool approval dialogs.

```python
from omniagents.core import function_tool, approval_metadata

@function_tool
@approval_metadata(
    title="Delete File",
    description="Permanently removes a file",
    risk_level="high"
)
def delete_file(path: str) -> str:
    """Delete a file."""
    import os
    os.remove(path)
    return f"Deleted {path}"
```

::: omniagents.core.tools.approval_metadata

## Tool Output

### RichToolOutput

Container for tool results with metadata.

```python
class RichToolOutput:
    output: Any  # The actual tool result
    metadata: dict  # Additional metadata
```

::: omniagents.core.tools.RichToolOutput

## Tool Discovery

### discover_functions_in_dir

Discover all functions decorated with `@function_tool` in a directory.

```python
from omniagents.core.tools import discover_functions_in_dir

tools = discover_functions_in_dir("path/to/tools")
```

::: omniagents.core.tools.discover_functions_in_dir

## Best Practices

1. **Type Hints**: Always use type hints for parameters and return values
2. **Docstrings**: Write clear docstrings (used by AI to understand the tool)
3. **Error Handling**: Handle errors gracefully
4. **Side Effects**: Document side effects in docstrings
5. **Idempotency**: Make tools idempotent when possible

## Next Steps

- [Tools Guide](../guide/tools.md) - Learn how to create tools
- [Core API](core.md) - Core functionality reference
