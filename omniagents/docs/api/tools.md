# Tools API Reference

API reference for the tool system.

## Decorators

### @function_tool

Decorator to mark a function as an agent tool.

```python
from omniagents.core import function_tool

@function_tool
def my_tool(param: str) -> str:
    """Tool description that the AI will see."""
    return f"Result: {param}"
```

**Usage:**

1. Import the decorator from `omniagents.core`
2. Decorate your function
3. Add a docstring (used by AI to understand the tool)
4. Use type hints for parameters and return value

**Requirements:**

- Function must have a docstring
- Parameters should have type hints
- Return type should be specified

### @rich_function_tool

Decorator for tools that return structured output with metadata.

```python
from omniagents.core import rich_function_tool, RichToolOutput

@rich_function_tool
def my_rich_tool(param: str) -> RichToolOutput:
    """Tool with rich output and metadata."""
    return RichToolOutput(
        output={"result": param, "status": "success"},
        metadata={
            "type": "analysis",
            "display": "table"
        }
    )
```

**Use Cases:**

- Tools that generate visualizations
- Tools that produce structured data
- Tools that need custom UI rendering

### @approval_metadata

Add rich metadata for tool approval dialogs (Safe Agent mode).

```python
from omniagents.core import function_tool, approval_metadata

@function_tool
@approval_metadata(
    title="Delete File",
    description="Permanently removes a file from the filesystem",
    risk_level="high",
    category="file_operations"
)
def delete_file(path: str) -> str:
    """Delete a file from the filesystem."""
    import os
    os.remove(path)
    return f"Deleted {path}"
```

**Parameters:**

- `title` (str, optional): Display name for the operation
- `description` (str, optional): Detailed explanation shown to user
- `risk_level` (str, optional): "low", "medium", or "high"
- `category` (str, optional): Logical grouping of related operations

**When to Use:**

Always use approval metadata for tools that:
- Modify or delete files
- Send network requests
- Execute system commands
- Access sensitive data

## Tool Output

### RichToolOutput

Container for tool results with metadata.

```python
from omniagents.core import RichToolOutput

output = RichToolOutput(
    output={"key": "value"},
    metadata={
        "display_type": "chart",
        "chart_data": {...}
    }
)
```

**Fields:**

- `output` (Any): The actual tool result sent to the AI agent
- `metadata` (dict): Additional UI-specific metadata

**Metadata Examples:**

```python
# Table data
metadata = {
    "display_type": "table",
    "columns": ["Name", "Value"],
    "rows": [["A", 1], ["B", 2]]
}

# Chart data
metadata = {
    "display_type": "chart",
    "chart_type": "line",
    "data": {...}
}

# File preview
metadata = {
    "display_type": "file",
    "filename": "report.pdf",
    "size": 1024
}
```

## Tool Discovery

### discover_functions_in_dir

Discover all functions decorated with `@function_tool` in a directory.

```python
from omniagents.core import discover_functions_in_dir

tools = discover_functions_in_dir("path/to/tools")
```

**Parameters:**

- `directory` (str): Path to directory containing tool modules

**Returns:**

- `list`: List of discovered tool functions

**Behavior:**

- Recursively scans Python files in the directory
- Imports modules and finds decorated functions
- Returns list of callable tool functions

**Example Directory Structure:**

```
tools/
├── __init__.py
├── file_tools.py
├── api_tools.py
└── data_tools.py
```

All `@function_tool` decorated functions in these files will be discovered.

### get_approval_metadata

Get approval metadata attached to a tool function.

```python
from omniagents.core import get_approval_metadata

metadata = get_approval_metadata(my_tool)
```

**Parameters:**

- `func` (callable): Tool function

**Returns:**

- `dict` or `None`: Approval metadata if present

## Complete Example

```python
from omniagents.core import (
    function_tool,
    rich_function_tool,
    RichToolOutput,
    approval_metadata,
    discover_functions_in_dir
)

# Basic tool
@function_tool
def get_time() -> str:
    """Get the current time."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Rich tool with metadata
@rich_function_tool
def analyze_data(file_path: str) -> RichToolOutput:
    """Analyze data from a CSV file."""
    import pandas as pd
    df = pd.read_csv(file_path)

    return RichToolOutput(
        output=f"Analyzed {len(df)} rows",
        metadata={
            "type": "analysis",
            "rows": len(df),
            "columns": list(df.columns)
        }
    )

# Tool with approval metadata
@function_tool
@approval_metadata(
    title="Delete File",
    description="Permanently delete a file",
    risk_level="high"
)
def delete_file(path: str) -> str:
    """Delete a file from the filesystem."""
    import os
    os.remove(path)
    return f"Deleted {path}"

# Discover tools
tools = discover_functions_in_dir("./my_tools")
```

## Best Practices

1. **Type Hints**: Always use type hints for parameters and return values
2. **Docstrings**: Write clear, detailed docstrings (the AI reads these!)
3. **Error Handling**: Handle errors gracefully and return meaningful messages
4. **Side Effects**: Document any side effects in the docstring
5. **Idempotency**: Make tools idempotent when possible
6. **Approval Metadata**: Always add approval metadata for destructive operations

## Next Steps

- [Tools Guide](../guide/tools.md) - Learn how to create tools
- [Core API](core.md) - Core functionality reference
- [Safe Agent Mode](../guide/safe-agent.md) - Tool approval system
