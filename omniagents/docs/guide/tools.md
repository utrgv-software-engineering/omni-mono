# Tools

Tools are Python functions that agents can call to interact with external systems, perform calculations, or take actions. OmniAgents provides two types of tools: **basic tools** and **rich tools**.

## Quick Start

Create a basic tool with the `@function_tool` decorator:

```python
from omniagents import function_tool

@function_tool
def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        city: The name of the city

    Returns:
        A description of the weather
    """
    # Your implementation here
    return f"Weather in {city}: Sunny, 72°F"
```

## Tool Requirements

All tools must meet these requirements:

1. **Decorator**: Use `@function_tool` or `@rich_function_tool`
2. **Docstring**: Include a clear description (the AI reads this to understand what the tool does)
3. **Type Hints**: Annotate all parameters and return types
4. **Error Handling**: Handle errors gracefully and return meaningful messages

## Creating Basic Tools

Basic tools return simple values (strings, numbers, dicts, lists, etc.) directly to the AI agent.

### The @function_tool Decorator

```python
from omniagents import function_tool

@function_tool
def calculate_sum(a: float, b: float) -> float:
    """Add two numbers together.

    Args:
        a: First number
        b: Second number

    Returns:
        The sum of a and b
    """
    return a + b
```

### Features

- **Automatic async wrapping**: Synchronous functions are automatically wrapped to run asynchronously
- **Type validation**: Parameter types are validated based on type hints
- **Docstring parsing**: The docstring is used to generate the tool description for the AI

### Supported Return Types

Tools can return any JSON-serializable type:

```python
@function_tool
def get_user_info(user_id: str) -> dict:
    """Get user information."""
    return {
        "id": user_id,
        "name": "John Doe",
        "email": "john@example.com"
    }

@function_tool
def list_files(directory: str) -> list[str]:
    """List files in a directory."""
    import os
    return os.listdir(directory)

@function_tool
def check_status() -> bool:
    """Check if service is running."""
    return True
```

### Optional Parameters

#### client_status

Show a status message in UIs while the tool executes:

```python
@function_tool(client_status="Searching database...")
def search_records(query: str) -> list[dict]:
    """Search database records."""
    # ... implementation
    return results
```

#### strict_mode

Control JSON schema validation (default: True):

```python
@function_tool(strict_mode=False)
def flexible_tool(data: dict) -> str:
    """Tool with non-strict schema validation."""
    return "Processed"
```

## Tool Organization

### Project Structure

Tools are automatically discovered from your project:

```
myproject/
├── project.yml
└── agents/
    └── my_agent/
        ├── agent.yml
        ├── instructions.md
        └── tools/              # Agent-specific tools
            ├── __init__.py
            ├── weather.py
            └── calculations.py
```

Or at the project level for shared tools:

```
myproject/
├── project.yml
├── tools/                      # Shared across all agents
│   ├── __init__.py
│   └── common_tools.py
└── agents/
    └── my_agent/
        └── agent.yml
```

### Referencing Tools

In your `agent.yml`, reference tools by their function names:

```yaml
name: my_agent
instructions_file: instructions.md
tools:
  - get_weather
  - calculate_sum
  - search_records
```

## Example Tools

### File Operations

```python
from omniagents import function_tool

@function_tool
def read_file(path: str) -> str:
    """Read and return the contents of a file.

    Args:
        path: Path to the file to read

    Returns:
        File contents as a string

    Raises:
        Returns error message if file cannot be read
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error reading file: {str(e)}"
```

### API Calls

```python
@function_tool
def fetch_weather(city: str) -> dict:
    """Fetch current weather data for a city.

    Args:
        city: Name of the city

    Returns:
        Dictionary with temperature, conditions, and humidity
    """
    import requests

    try:
        # Example API call (replace with real API)
        response = requests.get(
            f"https://api.weather.example.com/current?city={city}",
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        return {
            "city": city,
            "temperature": data["temp"],
            "conditions": data["conditions"],
            "humidity": data["humidity"]
        }
    except Exception as e:
        return {"error": str(e)}
```

### Calculations

```python
@function_tool
def calculate_statistics(numbers: list[float]) -> dict:
    """Calculate basic statistics for a list of numbers.

    Args:
        numbers: List of numbers to analyze

    Returns:
        Dictionary with mean, median, mode, and standard deviation
    """
    import statistics

    if not numbers:
        return {"error": "Empty list provided"}

    try:
        return {
            "count": len(numbers),
            "mean": round(statistics.mean(numbers), 2),
            "median": statistics.median(numbers),
            "stdev": round(statistics.stdev(numbers), 2) if len(numbers) > 1 else 0,
            "min": min(numbers),
            "max": max(numbers)
        }
    except Exception as e:
        return {"error": str(e)}
```

### Date and Time

```python
@function_tool
def get_current_time(timezone: str = "UTC") -> str:
    """Get the current time in a specific timezone.

    Args:
        timezone: Timezone name (e.g., "UTC", "America/New_York")

    Returns:
        Current time as a formatted string
    """
    from datetime import datetime
    import pytz

    try:
        tz = pytz.timezone(timezone)
        current_time = datetime.now(tz)
        return current_time.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception as e:
        return f"Error: {str(e)}"
```

## Best Practices

### 1. Write Clear Docstrings

The AI agent uses your docstring to understand when and how to use the tool:

```python
@function_tool
def good_example(query: str, limit: int = 10) -> list[dict]:
    """Search the database for matching records.

    This tool searches across all indexed fields and returns
    the most relevant results based on the query string.

    Args:
        query: Search query string (supports wildcards with *)
        limit: Maximum number of results to return (default: 10)

    Returns:
        List of matching records, each containing id, title, and score

    Example:
        search_database("python*", limit=5)
    """
    # Implementation...
```

### 2. Handle Errors Gracefully

Return meaningful error messages instead of raising exceptions:

```python
@function_tool
def divide(a: float, b: float) -> float | str:
    """Divide two numbers.

    Args:
        a: Numerator
        b: Denominator

    Returns:
        Result of a / b, or error message if b is zero
    """
    if b == 0:
        return "Error: Cannot divide by zero"
    return a / b
```

### 3. Use Type Hints

Type hints improve reliability and enable validation:

```python
from typing import Optional, Union

@function_tool
def process_data(
    data: dict,
    format: str = "json",
    compress: bool = False
) -> Union[str, dict]:
    """Process data with optional formatting and compression."""
    # Implementation...
```

### 4. Keep Tools Focused

Each tool should have a single, clear purpose:

```python
# Good - focused tool
@function_tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email message."""
    # Implementation...

# Avoid - does too many things
@function_tool
def handle_communication(
    mode: str,  # email, sms, slack, etc.
    recipient: str,
    message: str,
    attachments: list = None
) -> str:
    """Handle all types of communication."""  # Too broad!
    # Implementation...
```

### 5. Document Side Effects

Clearly document any actions that modify state:

```python
@function_tool
def delete_old_files(directory: str, days: int) -> str:
    """Delete files older than specified days.

    **Warning**: This tool permanently deletes files from the filesystem.

    Args:
        directory: Directory to scan for old files
        days: Delete files older than this many days

    Returns:
        Summary of files deleted
    """
    # Implementation...
```

## Advanced Features

### Async Tools

Both sync and async functions work with `@function_tool`:

```python
import aiohttp

@function_tool
async def async_fetch(url: str) -> dict:
    """Fetch data asynchronously.

    Args:
        url: URL to fetch

    Returns:
        JSON response as dictionary
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()
```

Sync functions are automatically wrapped to run asynchronously.

### Using Context

Access runtime context in your tools:

```python
from agents.run_context import RunContextWrapper
from typing import Any

@function_tool
def workspace_info(ctx: RunContextWrapper[Any]) -> dict:
    """Get information about the current workspace.

    Returns:
        Dictionary with workspace details
    """
    context = getattr(ctx, "context", None)
    if isinstance(context, dict):
        return {
            "workspace_root": context.get("workspace_root"),
            "user_id": context.get("user_id")
        }
    return {"error": "No context available"}
```

## Next Steps

- **[Rich Tools](rich-tools.md)** - Create tools with enhanced UI rendering
- **[Safe Agent Mode](safe-agent.md)** - Add approval requirements for tools
- **[API Reference](../api/tools.md)** - Detailed API documentation
