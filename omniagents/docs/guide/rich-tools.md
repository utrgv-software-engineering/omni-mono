# Rich Tools

Rich tools extend basic tools by providing enhanced UI rendering capabilities. While basic tools return simple values to the AI agent, rich tools can separate what the AI sees from how the UI displays the result.

## When to Use Rich Tools

Use rich tools when you need:

- **Enhanced visualization**: Charts, tables, diffs, or custom UI elements
- **Separate LLM and UI content**: Keep the AI context concise while showing detailed UI
- **Structured metadata**: Attach display hints and formatting information
- **Better user experience**: Provide previews, syntax highlighting, or interactive elements

## Quick Start

```python
from omniagents.core import rich_function_tool, RichToolOutput

@rich_function_tool
def analyze_data(file_path: str) -> RichToolOutput:
    """Analyze a CSV file and return statistics.

    Args:
        file_path: Path to the CSV file

    Returns:
        Analysis results with visualization metadata
    """
    import pandas as df
    data = pd.read_csv(file_path)

    # Brief summary for the AI
    llm_output = f"Analyzed {len(data)} rows with {len(data.columns)} columns"

    # Detailed metadata for the UI
    ui_metadata = {
        "display_type": "table",
        "summary": f"Analysis of {file_path}",
        "value": data.to_dict(),
        "preview": data.head().to_string(),
        "truncated": len(data) > 5,
        "metadata": {
            "rows": len(data),
            "columns": list(data.columns),
            "file_path": file_path
        }
    }

    return RichToolOutput(llm_output, ui_metadata)
```

## RichToolOutput

The `RichToolOutput` class separates LLM and UI content:

```python
class RichToolOutput:
    def __init__(
        self,
        llm_output: str,
        ui_metadata: Dict[str, Any] = None
    ):
        """
        Args:
            llm_output: Text sent to the AI agent (keeps context concise)
            ui_metadata: Dict with display information for UIs
        """
```

### Key Features

- **String conversion**: When converted to string (for the AI), only `llm_output` is returned
- **UI metadata**: Preserved separately for client-side rendering
- **Context efficiency**: Prevents large UI data from polluting the AI's context window

### Example

```python
result = RichToolOutput(
    llm_output="Found 3 anomalies in the data",
    ui_metadata={
        "display_type": "chart",
        "value": {...},  # Full chart data
        "summary": "Anomaly Detection Results"
    }
)

# When sent to AI: "Found 3 anomalies in the data"
print(str(result))  # Only LLM output

# UI receives both llm_output and ui_metadata
```

## UI Metadata Format

The `ui_metadata` dictionary follows a standard format:

```python
ui_metadata = {
    "value": Any,              # The actual data to display
    "display_type": str,       # Hint for UI rendering
    "summary": str,            # Brief description
    "preview": str,            # Preview text
    "truncated": bool,         # Whether content was truncated
    "metadata": dict           # Additional context
}
```

### Standard Fields

#### value
The main content to display:

```python
ui_metadata = {
    "value": "Full command output here...",
    # ...
}
```

#### display_type
Hint for how UIs should render the content:

- `"command"` - Shell command output
- `"error"` - Error messages
- `"conversion"` - File conversion results
- `"diff"` - Code or text diffs
- `"file_write"` - File write operations
- `"table"` - Tabular data
- `"chart"` - Chart/graph data
- `"code"` - Source code with syntax highlighting

```python
ui_metadata = {
    "display_type": "diff",
    # ...
}
```

#### summary
One-line description shown in UI headers:

```python
ui_metadata = {
    "summary": "$ ls -la /home/user",
    # ...
}
```

#### preview
Truncated preview of the content:

```python
ui_metadata = {
    "preview": content[:500],  # First 500 characters
    "truncated": len(content) > 500,
    # ...
}
```

#### metadata
Additional context and details:

```python
ui_metadata = {
    "metadata": {
        "file_path": "/path/to/file.txt",
        "success": True,
        "line_count": 150,
        "error_type": None
    }
}
```

## Complete Examples

### Shell Command Tool

```python
from omniagents.core import rich_function_tool, RichToolOutput
import subprocess

@rich_function_tool(client_status="Running command...")
def execute_command(command: str, timeout: int = 30) -> RichToolOutput:
    """Execute a shell command and return the output.

    Args:
        command: Shell command to execute
        timeout: Maximum execution time in seconds

    Returns:
        Command output with execution metadata
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Create concise output for AI
        if result.returncode == 0:
            llm_output = f"Command executed successfully:\n{result.stdout}"
        else:
            llm_output = f"Command failed (exit code {result.returncode}):\n{result.stderr}"

        # Full output for UI
        combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
        lines = combined.split("\n")
        preview = "\n".join(lines[:10])

        ui_metadata = {
            "value": combined,
            "display_type": "command",
            "summary": f"$ {command}",
            "preview": preview,
            "truncated": len(lines) > 10,
            "metadata": {
                "command": command,
                "exit_code": result.returncode,
                "success": result.returncode == 0,
                "line_count": len(lines)
            }
        }

        return RichToolOutput(llm_output, ui_metadata)

    except subprocess.TimeoutExpired:
        llm_output = f"Command timed out after {timeout} seconds"
        ui_metadata = {
            "value": llm_output,
            "display_type": "error",
            "summary": "Timeout error",
            "preview": llm_output,
            "truncated": False,
            "metadata": {
                "error_type": "timeout",
                "command": command,
                "timeout": timeout
            }
        }
        return RichToolOutput(llm_output, ui_metadata)
```

### File Conversion Tool

```python
@rich_function_tool
def convert_to_markdown(file_path: str) -> RichToolOutput:
    """Convert a document to Markdown format.

    Args:
        file_path: Path to file or URL

    Returns:
        Markdown content with conversion metadata
    """
    try:
        # Conversion logic here
        markdown_content = convert(file_path)  # Your conversion function

        # Concise summary for AI
        llm_output = markdown_content

        # Detailed UI information
        preview = markdown_content[:500]
        ui_metadata = {
            "value": markdown_content,
            "display_type": "conversion",
            "summary": f"Converted {file_path}",
            "preview": preview,
            "truncated": len(markdown_content) > 500,
            "metadata": {
                "success": True,
                "source_type": "pdf",
                "content_length": len(markdown_content),
                "file_path": file_path
            }
        }

        return RichToolOutput(llm_output, ui_metadata)

    except Exception as e:
        llm_output = f"Error converting file: {str(e)}"
        ui_metadata = {
            "value": llm_output,
            "display_type": "error",
            "summary": "Conversion failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "success": False,
                "error_type": "conversion_error",
                "file_path": file_path,
                "error": str(e)
            }
        }
        return RichToolOutput(llm_output, ui_metadata)
```

### Data Analysis Tool

```python
@rich_function_tool
def analyze_csv(file_path: str) -> RichToolOutput:
    """Analyze a CSV file and provide statistics.

    Args:
        file_path: Path to the CSV file

    Returns:
        Statistical analysis with table visualization
    """
    import pandas as pd

    try:
        df = pd.read_csv(file_path)

        # Summary for AI
        llm_output = (
            f"Analyzed {file_path}:\n"
            f"- Rows: {len(df)}\n"
            f"- Columns: {len(df.columns)}\n"
            f"- Columns: {', '.join(df.columns)}"
        )

        # Detailed statistics for UI
        stats = df.describe().to_dict()

        ui_metadata = {
            "value": stats,
            "display_type": "table",
            "summary": f"Analysis: {file_path}",
            "preview": df.head(5).to_string(),
            "truncated": len(df) > 5,
            "metadata": {
                "rows": len(df),
                "columns": list(df.columns),
                "column_types": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "missing_values": df.isnull().sum().to_dict(),
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
            "metadata": {
                "success": False,
                "error": str(e),
                "file_path": file_path
            }
        }
        return RichToolOutput(llm_output, ui_metadata)
```

## Approval Metadata

For tools used with Safe Agent mode, you can provide rich approval dialogs:

```python
from omniagents.core import rich_function_tool, RichToolOutput, approval_metadata

@rich_function_tool
def execute_bash(command: str) -> RichToolOutput:
    """Execute a bash command."""
    # Implementation...

@approval_metadata()  # Auto-detects tool name from function name
def execute_bash_approval(command: str) -> dict:
    """Generate approval metadata for bash execution.

    Args:
        command: The command to be executed (same signature as tool)

    Returns:
        Metadata dict for approval UI
    """
    return {
        "display_type": "command",
        "summary": "Run shell command",
        "value": command,
        "preview": f"$ {command}",
        "truncated": False,
        "metadata": {
            "command": command,
            "risk_level": "high" if any(x in command for x in ["rm", "del"]) else "medium"
        }
    }
```

### Approval Metadata Rules

1. **Function name**: Must end with `_approval` or `_metadata`, or use `@approval_metadata(tool="tool_name")`
2. **Signature**: Must match the tool's parameter signature exactly
3. **Return value**: Dict with same format as `ui_metadata`
4. **Auto-detection**: If name is `tool_name_approval`, it's registered for `tool_name`

Example with explicit tool name:

```python
@approval_metadata(tool="dangerous_operation")
def show_danger_warning(file_path: str, force: bool = False) -> dict:
    return {
        "display_type": "warning",
        "summary": "⚠️ Destructive Operation",
        "value": f"This will permanently delete {file_path}",
        "preview": f"DELETE: {file_path}",
        "metadata": {
            "risk_level": "critical",
            "force": force
        }
    }
```

## Best Practices

### 1. Keep LLM Output Concise

The AI agent sees the `llm_output`, so keep it brief:

```python
# Good - concise for AI
llm_output = "Found 5 security vulnerabilities"

# Bad - too verbose for AI context
llm_output = """
Security Scan Results:
===================
Found 5 vulnerabilities:
1. SQL Injection in login.py line 42
2. XSS vulnerability in comments.html line 15
...
"""
```

### 2. Provide Meaningful Previews

Previews help users quickly understand the result:

```python
content = file.read()
ui_metadata = {
    "preview": content[:300] + "..." if len(content) > 300 else content,
    "truncated": len(content) > 300,
    #...
}
```

### 3. Use Appropriate Display Types

Choose display types that match your content:

```python
# For code/text differences
{"display_type": "diff"}

# For tabular data
{"display_type": "table"}

# For errors
{"display_type": "error"}

# For command output
{"display_type": "command"}
```

### 4. Include Success Indicators

Always include success/failure information:

```python
ui_metadata = {
    "metadata": {
        "success": True,  # or False
        "error": None,    # or error message
        # ...
    }
}
```

### 5. Handle Errors Consistently

Return RichToolOutput even for errors:

```python
try:
    result = perform_operation()
    return RichToolOutput(
        f"Operation successful: {result}",
        {"display_type": "success", ...}
    )
except Exception as e:
    return RichToolOutput(
        f"Operation failed: {str(e)}",
        {
            "display_type": "error",
            "summary": "Operation failed",
            "value": str(e),
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "success": False,
                "error_type": type(e).__name__,
                "error": str(e)
            }
        }
    )
```

## Advanced Features

### Client Status Messages

Show real-time status in UIs while the tool executes:

```python
@rich_function_tool(client_status="Analyzing large dataset...")
def analyze_big_data(file_path: str) -> RichToolOutput:
    """Analyze large dataset."""
    # Long-running operation...
    # UI shows "Analyzing large dataset..." while this runs
    return RichToolOutput(...)
```

### Context Access

Rich tools can access runtime context:

```python
from agents.run_context import RunContextWrapper
from typing import Any

@rich_function_tool
def workspace_operation(ctx: RunContextWrapper[Any], file: str) -> RichToolOutput:
    """Perform operation in workspace."""
    context = getattr(ctx, "context", None)
    workspace_root = context.get("workspace_root") if isinstance(context, dict) else None

    # Use workspace_root in operation...
    return RichToolOutput(...)
```

## Next Steps

- **[Tools](tools.md)** - Learn about basic tools
- **[Safe Agent Mode](safe-agent.md)** - Add approval requirements
- **[API Reference](../api/tools.md)** - Detailed API documentation
