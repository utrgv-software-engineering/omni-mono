# Examples

OmniAgents includes several example projects demonstrating different features and use cases.

## Basic Examples

### Basic Python Agent

Location: `examples/basic_python/`

Define an agent entirely in Python code:

```python
from omniagents.core import function_tool, create_agent
from datetime import datetime

@function_tool
def get_current_time() -> str:
    """Get the current time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

instructions = "You are a helpful assistant that can tell the time."

agent = create_agent(
    name="TimeAgent",
    instructions=instructions,
    tools=[get_current_time]
)
```

**Run:**
```bash
cd examples/basic_python
omniagents run -P agent.yml --mode gui
```

### Basic YAML Agent

Location: `examples/basic_yaml/`

Define an agent using YAML configuration:

```yaml
name: DemoAgent
instructions_file: instructions.md
tools: [get_current_time, flip_coin]
layout: tabs
```

**Run:**
```bash
omniagents run -P examples/basic_yaml/agent.yml --mode cli
```

## Safe Agent Examples

### Safe Agent (Python)

Location: `examples/safe_agent_python/`

Agent with tool approval requirements:

```python
agent = create_agent(
    name="SafeAgent",
    instructions=instructions,
    tools=[delete_file, send_email],
    use_safe_agent=True
)
```

Tools require user approval before execution.

**Run:**
```bash
cd examples/safe_agent_python
omniagents run -P agent.yml --mode gui
```

### Safe Agent (YAML)

Location: `examples/safe_agent_yaml/`

YAML configuration with safe mode:

```yaml
name: SafeAssistant
instructions_file: instructions.md
tools: [dangerous_tool]
use_safe_agent: true
```

**Run:**
```bash
omniagents run -P examples/safe_agent_yaml/agent.yml --mode gui
```

## Remote Agent Example

Location: `examples/remote_agent/`

Demonstrates server mode and remote client connections.

### Server

Start the agent server:

```bash
cd examples/remote_agent
omniagents run -P agent.yml --mode server --port 8000
```

### Client

Connect from another process:

```python
from omniagents.rpc.agents import RemoteAgent

agent = RemoteAgent("ws://localhost:8000/ws")

# Stream responses
result = agent.run_streamed([{
    "role": "user",
    "content": "Hello, remote agent!"
}])

async for event in result.stream_events():
    print(event)
```

**Run:**
```bash
cd examples/remote_agent
python client.py
```

## Tool Examples

### File Operations

```python
@function_tool
def read_file(path: str) -> str:
    """Read contents of a file."""
    with open(path, 'r') as f:
        return f.read()

@function_tool
@approval_metadata(
    title="Write File",
    risk_level="medium"
)
def write_file(path: str, content: str) -> str:
    """Write content to a file."""
    with open(path, 'w') as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {path}"
```

### API Calls

```python
@function_tool
def fetch_data(url: str) -> dict:
    """Fetch data from an API."""
    import requests
    response = requests.get(url)
    return response.json()
```

### Rich Tools

```python
@rich_function_tool
def analyze_csv(file_path: str) -> RichToolOutput:
    """Analyze a CSV file and return statistics."""
    import pandas as pd
    df = pd.read_csv(file_path)

    stats = {
        "rows": len(df),
        "columns": len(df.columns),
        "summary": df.describe().to_dict()
    }

    return RichToolOutput(
        output=stats,
        metadata={
            "type": "analysis",
            "display": "table",
            "chart_data": {...}
        }
    )
```

## Running Examples

### CLI Mode

```bash
omniagents run -P examples/basic_yaml/agent.yml --mode cli
```

### GUI Mode

```bash
omniagents run -P examples/basic_yaml/agent.yml --mode gui
```

### TUI Mode

```bash
omniagents run -P examples/basic_yaml/agent.yml --mode tui
```

### Server Mode

```bash
omniagents run -P examples/basic_yaml/agent.yml --mode server
```

## Creating Your Own Examples

1. **Create project directory**:
   ```bash
   mkdir my-agent
   cd my-agent
   ```

2. **Define agent** (`agent.yml`):
   ```yaml
   name: MyAgent
   instructions_file: instructions.md
   tools: [my_tool]
   ```

3. **Write instructions** (`instructions.md`):
   ```markdown
   You are a helpful assistant.
   ```

4. **Create tools** (`tools/my_tool.py`):
   ```python
   from omniagents.core import function_tool

   @function_tool
   def my_tool() -> str:
       """My custom tool."""
       return "Result"
   ```

5. **Run**:
   ```bash
   omniagents run -P agent.yml --mode gui
   ```

## Next Steps

- [Defining Agents](guide/defining-agents.md) - Learn agent configuration
- [Tools](guide/tools.md) - Create custom tools
- [Backends](backends/overview.md) - Explore interface modes
