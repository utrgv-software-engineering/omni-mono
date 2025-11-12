# Tools API Reference

Complete API reference for the OmniAgents tool system.

## Decorators

### @function_tool

Decorator that wraps a Python function to make it available as an agent tool.

```python
from omniagents import function_tool

@function_tool
def my_tool(param: str) -> str:
    """Tool description that the AI will see."""
    return f"Result: {param}"
```

**Signature:**

```python
def function_tool(func: Callable = None, **kwargs) -> Callable
```

**Parameters:**

- `func` (Callable, optional): The function to decorate (auto-filled when used as @function_tool)
- `**kwargs`: Additional arguments passed to the underlying agents library:
  - `client_status` (str, optional): Status message shown in UIs while tool executes
  - `strict_mode` (bool, optional): Enable strict JSON schema validation (default: True)
  - Other arguments supported by `agents.tool.function_tool`

**Returns:**

- Decorated function with tool markers and async wrapping

**Features:**

- **Auto-async wrapping**: Synchronous functions are automatically wrapped to run asynchronously using `asyncio.to_thread`
- **Marker attribute**: Adds `_is_omniagents_core_tool` attribute for discovery
- **Client status**: Optional `client_status` parameter for UI feedback
- **Passthrough**: Accepts all kwargs from the underlying `agents.tool.function_tool`

**Examples:**

```python
# Basic usage
@function_tool
def calculate(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y

# With client status
@function_tool(client_status="Calculating...")
def complex_calc(data: list) -> float:
    """Perform complex calculation."""
    return sum(data) / len(data)

# With non-strict schema
@function_tool(strict_mode=False)
def flexible_tool(data: dict) -> str:
    """Tool with flexible schema."""
    return "OK"

# Async function (no wrapping needed)
@function_tool
async def async_operation(url: str) -> dict:
    """Fetch data asynchronously."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()
```

**Requirements:**

- Must have a docstring (the AI uses this to understand the tool)
- Should have type hints for all parameters and return value
- Should handle errors gracefully and return meaningful messages

### @rich_function_tool

Decorator for tools that return `RichToolOutput` with separate LLM and UI content.

```python
from omniagents.core import rich_function_tool, RichToolOutput

@rich_function_tool
def my_rich_tool(param: str) -> RichToolOutput:
    """Tool with rich output and metadata."""
    llm_output = "Brief summary for AI"
    ui_metadata = {
        "display_type": "table",
        "value": {"result": param},
        "summary": "Operation complete"
    }
    return RichToolOutput(llm_output, ui_metadata)
```

**Signature:**

```python
def rich_function_tool(func: Callable = None, **kwargs) -> Callable
```

**Parameters:**

- Same as `@function_tool`
- All parameters passed through to `function_tool`

**Returns:**

- Decorated function with tool and rich tool markers

**Features:**

- All features of `@function_tool`
- **Rich marker**: Adds `_is_rich_tool` attribute
- **Separate content**: Tools must return `RichToolOutput` to separate AI and UI content

**Use Cases:**

- Tools that generate visualizations (charts, graphs)
- Tools with large output (keep AI context concise)
- Tools requiring custom UI rendering (diffs, tables, syntax highlighting)
- Tools with metadata for enhanced UX

**Example:**

```python
@rich_function_tool(client_status="Running analysis...")
def analyze_data(file_path: str) -> RichToolOutput:
    """Analyze CSV file."""
    import pandas as pd
    df = pd.read_csv(file_path)

    # Concise for AI
    llm_output = f"Analyzed {len(df)} rows, {len(df.columns)} columns"

    # Detailed for UI
    ui_metadata = {
        "value": df.describe().to_dict(),
        "display_type": "table",
        "summary": f"Analysis: {file_path}",
        "preview": df.head().to_string(),
        "truncated": len(df) > 5,
        "metadata": {
            "rows": len(df),
            "columns": list(df.columns)
        }
    }

    return RichToolOutput(llm_output, ui_metadata)
```

### @approval_metadata

Decorator for functions that generate approval metadata for tools in Safe Agent mode.

```python
from omniagents.core import approval_metadata

@approval_metadata()  # Auto-detects tool name
def my_tool_approval(param1: str, param2: int) -> dict:
    """Generate approval metadata for my_tool."""
    return {
        "display_type": "warning",
        "summary": "Confirm operation",
        "value": f"Will process {param1} with {param2}",
        "preview": f"Operation: {param1}",
        "truncated": False,
        "metadata": {
            "risk_level": "medium"
        }
    }
```

**Signature:**

```python
def approval_metadata(tool: str = None) -> Callable
```

**Parameters:**

- `tool` (str, optional): Explicit tool name. If not provided, auto-detects from function name

**Auto-detection Rules:**

1. Function name ends with `_approval` → Tool name is prefix (e.g., `my_tool_approval` → `my_tool`)
2. Function name ends with `_metadata` → Tool name is prefix (e.g., `my_tool_metadata` → `my_tool`)
3. Otherwise, must specify `tool` parameter explicitly

**Requirements:**

- **Signature must match tool**: Parameters must exactly match the tool function's signature
- **Returns dict**: Must return dictionary with metadata fields
- **Registered at import**: Registration happens when module is imported

**Examples:**

```python
# Auto-detection from name
@approval_metadata()
def delete_file_approval(path: str) -> dict:
    return {
        "display_type": "warning",
        "summary": f"Delete {path}",
        "value": f"This will permanently delete {path}",
        "metadata": {"risk_level": "high"}
    }

# Explicit tool name
@approval_metadata(tool="dangerous_operation")
def show_warning(file: str, force: bool = False) -> dict:
    return {
        "display_type": "warning",
        "summary": "⚠️ Destructive Operation",
        "value": f"Will delete {file}",
        "metadata": {
            "force": force,
            "risk_level": "critical" if force else "high"
        }
    }
```

**Return Format:**

The returned dict should follow the same format as `RichToolOutput.ui_metadata`:

```python
{
    "display_type": str,    # "command", "warning", "diff", etc.
    "summary": str,         # Brief one-line description
    "value": Any,           # Main content to display
    "preview": str,         # Preview text (optional)
    "truncated": bool,      # Whether content is truncated
    "metadata": dict        # Additional context
}
```

## Classes

### RichToolOutput

Container class that separates LLM-facing output from UI-specific metadata.

```python
from omniagents.core import RichToolOutput

output = RichToolOutput(
    llm_output="Brief summary for AI",
    ui_metadata={
        "display_type": "table",
        "value": {"key": "value"},
        "summary": "Operation completed"
    }
)
```

**Signature:**

```python
class RichToolOutput:
    def __init__(
        self,
        llm_output: str,
        ui_metadata: Dict[str, Any] = None
    )
```

**Parameters:**

- `llm_output` (str): Text output intended for the AI agent's context
- `ui_metadata` (Dict[str, Any], optional): Metadata for UI rendering (default: empty dict)

**Attributes:**

- `llm_output` (str): The text shown to the AI
- `ui_metadata` (dict): Metadata for UI rendering

**Special Methods:**

- `__str__()`: Returns only `llm_output` (keeps AI context concise)
- `__repr__()`: Debug representation showing both outputs

**Key Concept:**

When a tool returns `RichToolOutput`, the framework:
1. Sends `str(output)` (just `llm_output`) to the AI agent
2. Sends both `llm_output` and `ui_metadata` to the UI client

This prevents large UI data from bloating the AI's context window.

**Example:**

```python
result = RichToolOutput(
    llm_output="Processed 1000 records successfully",
    ui_metadata={
        "value": full_data_table,  # Large data not sent to AI
        "display_type": "table",
        "summary": "Data processing complete",
        "metadata": {"record_count": 1000}
    }
)

# To AI: "Processed 1000 records successfully"
print(str(result))

# To UI: Both llm_output and ui_metadata
```

**UI Metadata Standard Fields:**

```python
{
    "value": Any,           # Main content to display
    "display_type": str,    # Rendering hint (table, chart, diff, error, etc.)
    "summary": str,         # One-line description for headers
    "preview": str,         # Truncated preview of content
    "truncated": bool,      # Whether content was shortened
    "metadata": dict        # Additional structured context
}
```

**Common Display Types:**

- `"table"` - Tabular data
- `"chart"` - Charts/graphs
- `"diff"` - Text/code diffs
- `"command"` - Shell command output
- `"error"` - Error messages
- `"warning"` - Warning messages
- `"conversion"` - File conversion results
- `"code"` - Source code with syntax highlighting

## Functions

### discover_functions_in_dir

Discover all functions decorated with `@function_tool` or `@rich_function_tool` in a directory.

```python
from omniagents.core import discover_functions_in_dir

tools = discover_functions_in_dir("path/to/tools")
```

**Signature:**

```python
def discover_functions_in_dir(
    base_dir: str = "tools",
    debug: bool = None
) -> Dict[str, Callable]
```

**Parameters:**

- `base_dir` (str): Directory path (relative or absolute) to scan for `.py` files (default: "tools")
- `debug` (bool, optional): Deprecated parameter (uses `Debug.enabled()` instead)

**Returns:**

- `Dict[str, Callable]`: Dictionary mapping function names to callable tool functions

**Behavior:**

1. Recursively scans all `.py` files in `base_dir` (except `__init__.py`)
2. Temporarily adds directory to `sys.path` for imports
3. Imports each module and inspects members
4. Finds functions with `_is_omniagents_core_tool` marker attribute
5. Cleans up `sys.path` and unloads modules after discovery
6. Returns dictionary of tool name → function

**Example:**

```python
# Discover from default 'tools/' directory
tools = discover_functions_in_dir()

# Discover from custom directory
tools = discover_functions_in_dir("/path/to/my_tools")

# Result:
# {
#     "get_weather": <function get_weather>,
#     "calculate_sum": <function calculate_sum>,
#     ...
# }
```

**Directory Structure:**

```
tools/
├── __init__.py              # Ignored
├── file_tools.py           # Scanned
├── api_tools.py            # Scanned
└── subdirectory/
    └── more_tools.py       # Scanned (recursive)
```

All `@function_tool` and `@rich_function_tool` decorated functions in these files will be discovered.

**Notes:**

- Only functions with the `_is_omniagents_core_tool` marker are discovered
- Duplicate function names will log a warning and use the last definition found
- Import errors, syntax errors, and other exceptions are caught and logged
- Modules are unloaded after discovery to avoid side effects

### get_approval_metadata

Get approval metadata for a specific tool by calling its registered metadata generator.

```python
from omniagents.core import get_approval_metadata

metadata = get_approval_metadata("tool_name", {"arg1": "value1", "arg2": 42})
```

**Signature:**

```python
def get_approval_metadata(
    tool_name: str,
    args: dict
) -> Optional[Dict[str, Any]]
```

**Parameters:**

- `tool_name` (str): Name of the tool
- `args` (dict): Arguments dictionary from the tool call

**Returns:**

- `Dict[str, Any]`: Metadata dictionary for approval UI, or `None` if no metadata generator registered

**Behavior:**

1. Looks up the registered metadata generator function for `tool_name`
2. Calls the generator with parameters from `args` (matched by name)
3. Uses default values from function signature for missing parameters
4. Returns the generated metadata dict

**Example:**

```python
# Tool and its metadata generator
@function_tool
def delete_file(path: str, force: bool = False) -> str:
    """Delete a file."""
    os.remove(path)
    return f"Deleted {path}"

@approval_metadata()
def delete_file_approval(path: str, force: bool = False) -> dict:
    return {
        "display_type": "warning",
        "summary": f"Delete {path}",
        "value": f"Permanently delete {path}",
        "metadata": {"risk_level": "high" if force else "medium"}
    }

# Later, when tool is about to execute:
metadata = get_approval_metadata("delete_file", {"path": "/tmp/file.txt", "force": True})
# Returns:
# {
#     "display_type": "warning",
#     "summary": "Delete /tmp/file.txt",
#     "value": "Permanently delete /tmp/file.txt",
#     "metadata": {"risk_level": "high"}
# }
```

**Error Handling:**

- Returns `None` if no metadata generator is registered
- Returns `None` if metadata generator raises an exception
- Logs errors when `Debug.enabled()` is True

## Complete Example

```python
from omniagents import function_tool, rich_function_tool
from omniagents.core import RichToolOutput, approval_metadata

# ========== Basic Tool ==========
@function_tool
def get_time() -> str:
    """Get the current time in ISO format."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ========== Rich Tool ==========
@rich_function_tool(client_status="Analyzing data...")
def analyze_csv(file_path: str) -> RichToolOutput:
    """Analyze data from a CSV file.

    Args:
        file_path: Path to the CSV file

    Returns:
        Analysis results with table visualization
    """
    import pandas as pd

    try:
        df = pd.read_csv(file_path)

        # Concise output for AI
        llm_output = (
            f"Analyzed {file_path}:\n"
            f"- {len(df)} rows\n"
            f"- {len(df.columns)} columns: {', '.join(df.columns)}"
        )

        # Detailed output for UI
        ui_metadata = {
            "value": df.describe().to_dict(),
            "display_type": "table",
            "summary": f"Analysis: {file_path}",
            "preview": df.head(5).to_string(),
            "truncated": len(df) > 5,
            "metadata": {
                "rows": len(df),
                "columns": list(df.columns),
                "file_path": file_path
            }
        }

        return RichToolOutput(llm_output, ui_metadata)

    except Exception as e:
        llm_output = f"Error analyzing CSV: {str(e)}"
        ui_metadata = {
            "value": llm_output,
            "display_type": "error",
            "summary": "Analysis failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {"success": False, "error": str(e)}
        }
        return RichToolOutput(llm_output, ui_metadata)

# ========== Tool with Approval Metadata ==========
@function_tool
def delete_file(path: str) -> str:
    """Delete a file from the filesystem.

    **Warning**: This permanently deletes the file.

    Args:
        path: Path to the file to delete

    Returns:
        Confirmation message
    """
    import os
    try:
        os.remove(path)
        return f"Successfully deleted {path}"
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error deleting file: {str(e)}"

@approval_metadata()  # Auto-detects tool name "delete_file"
def delete_file_approval(path: str) -> dict:
    """Generate approval metadata for file deletion."""
    import os
    file_exists = os.path.exists(path)
    file_size = os.path.getsize(path) if file_exists else 0

    return {
        "display_type": "warning",
        "summary": f"Delete {os.path.basename(path)}",
        "value": f"Permanently delete: {path}",
        "preview": f"File: {path}\nSize: {file_size} bytes",
        "truncated": False,
        "metadata": {
            "risk_level": "high",
            "file_path": path,
            "file_exists": file_exists,
            "file_size": file_size
        }
    }

# ========== Tool Discovery ==========
from omniagents.core import discover_functions_in_dir

# Discover all tools in directory
tools = discover_functions_in_dir("./tools")
# Returns: {"get_time": <function>, "analyze_csv": <function>, "delete_file": <function>}

# Get approval metadata for a tool
from omniagents.core import get_approval_metadata

metadata = get_approval_metadata("delete_file", {"path": "/tmp/test.txt"})
# Returns the approval metadata dict
```

## Import Paths

```python
# Decorators and main classes
from omniagents import function_tool, rich_function_tool
from omniagents.core import RichToolOutput, approval_metadata

# Discovery functions
from omniagents.core import discover_functions_in_dir, get_approval_metadata

# Everything together
from omniagents.core import (
    function_tool,
    rich_function_tool,
    RichToolOutput,
    approval_metadata,
    discover_functions_in_dir,
    get_approval_metadata
)
```

## Next Steps

- **[Basic Tools Guide](../guide/tools.md)** - Learn how to create basic tools
- **[Rich Tools Guide](../guide/rich-tools.md)** - Create tools with enhanced UI
- **[Safe Agent Mode](../guide/safe-agent.md)** - Tool approval system
- **[Defining Agents](../guide/defining-agents.md)** - Configure agents to use tools
