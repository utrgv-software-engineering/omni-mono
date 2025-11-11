# Quick Start

This guide will help you create and run your first OmniAgents agent.

## Step 1: Set Up API Key

OmniAgents uses the OpenAI API. Set your API key as an environment variable:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

Or create a `.env` file in your project directory:

```
OPENAI_API_KEY=your-api-key-here
```

## Step 2: Create a Project Directory

```bash
mkdir my-first-agent
cd my-first-agent
```

## Step 3: Define Your Agent

Create an `agent.yml` file:

```yaml
name: MyFirstAgent
instructions_file: instructions.md
tools: [get_current_time]
use_safe_agent: false
```

Create an `instructions.md` file:

```markdown
You are a helpful assistant that can tell the current time.
Be friendly and concise in your responses.
```

## Step 4: Create a Tool

Create a `tools` directory with a Python file:

```bash
mkdir tools
```

Create `tools/time_tool.py`:

```python
from omniagents.core import function_tool
from datetime import datetime

@function_tool
def get_current_time() -> str:
    """Get the current time in a human-readable format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

## Step 5: Run Your Agent

### CLI Mode

Run in the terminal:

```bash
omniagents run -P agent.yml --mode cli
```

### GUI Mode

Launch the desktop interface:

```bash
omniagents run -P agent.yml --mode gui
```

### TUI Mode

Launch the Bubble Tea terminal UI:

```bash
omniagents run -P agent.yml --mode tui
```

## Example Conversation

```
You: What time is it?
Agent: Let me check the current time for you.
[Tool: get_current_time]
Agent: The current time is 2025-11-11 14:30:45.
```

## Next Steps

- [Configuration](configuration.md) - Learn about advanced configuration options
- [Defining Agents](../guide/defining-agents.md) - Explore agent customization
- [Tools](../guide/tools.md) - Create more sophisticated tools
- [Safe Agent Mode](../guide/safe-agent.md) - Add approval requirements
