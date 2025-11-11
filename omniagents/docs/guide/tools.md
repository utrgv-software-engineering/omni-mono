# Tools

Tools are Python functions that agents can call to interact with external systems or perform actions.

## Basic Tools

Use the `@function_tool` decorator to create a tool:

```python
from omniagents.core import function_tool

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

1. **Decorator**: Must use `@function_tool` or `@rich_function_tool`
2. **Docstring**: Must include a description (used by the AI to understand the tool)
3. **Type Hints**: Parameters and return type should be annotated
4. **Documentation**: Document parameters and return values

## Rich Tools

Rich tools can return structured output with metadata:

```python
from omniagents.core import rich_function_tool, RichToolOutput

@rich_function_tool
def analyze_data(file_path: str) -> RichToolOutput:
    """Analyze data from a CSV file.

    Args:
        file_path: Path to the CSV file

    Returns:
        Analysis results with metadata
    """
    # Perform analysis
    result = {
        "rows": 100,
        "columns": 5,
        "summary": "..."
    }

    return RichToolOutput(
        output=result,
        metadata={
            "type": "analysis",
            "file": file_path
        }
    )
```

## Tool Discovery

OmniAgents automatically discovers tools from:

1. **tools/ directory**: Place Python files in a `tools/` directory
2. **Specified modules**: Use `discover_functions_in_dir()` to scan directories
3. **Direct specification**: Pass tool functions directly

### Example Structure

```
my-agent/
├── agent.yml
├── instructions.md
└── tools/
    ├── __init__.py
    ├── weather.py
    └── data.py
```

In `agent.yml`:

```yaml
name: MyAgent
instructions_file: instructions.md
tools: [get_weather, analyze_data]
```

## Approval Metadata

For Safe Agent mode, you can add rich approval dialogs:

```python
from omniagents.core import function_tool, approval_metadata

@function_tool
@approval_metadata(
    title="Delete File",
    description="This will permanently delete the file.",
    risk_level="high"
)
def delete_file(path: str) -> str:
    """Delete a file from the filesystem."""
    os.remove(path)
    return f"Deleted {path}"
```

## Best Practices

1. **Single Responsibility**: Each tool should do one thing well
2. **Clear Documentation**: Write detailed docstrings
3. **Error Handling**: Handle errors gracefully and return meaningful messages
4. **Type Safety**: Use type hints for all parameters and return values
5. **Idempotency**: When possible, make tools idempotent
6. **Side Effects**: Document any side effects in the docstring

## Example Tools

### File Operations

```python
@function_tool
def read_file(path: str) -> str:
    """Read contents of a file.

    Args:
        path: Path to the file

    Returns:
        File contents as a string
    """
    with open(path, 'r') as f:
        return f.read()
```

### API Calls

```python
@function_tool
def fetch_data(url: str) -> dict:
    """Fetch data from an API endpoint.

    Args:
        url: The API endpoint URL

    Returns:
        JSON response as a dictionary
    """
    import requests
    response = requests.get(url)
    return response.json()
```

### Calculations

```python
@function_tool
def calculate_statistics(numbers: list[float]) -> dict:
    """Calculate basic statistics for a list of numbers.

    Args:
        numbers: List of numbers to analyze

    Returns:
        Dictionary with mean, median, and standard deviation
    """
    import statistics
    return {
        "mean": statistics.mean(numbers),
        "median": statistics.median(numbers),
        "stdev": statistics.stdev(numbers)
    }
```

## Next Steps

- [Safe Agent Mode](safe-agent.md) - Add approval requirements
- [API Reference](../api/tools.md) - Detailed API documentation
- [Examples](../examples.md) - Example tool implementations
