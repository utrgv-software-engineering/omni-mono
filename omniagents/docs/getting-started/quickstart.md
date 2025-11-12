# Quick Start

This guide will help you create and run your first OmniAgents agent in under 5 minutes.

## Step 1: Create a New Project

Create a new OmniAgents project with the scaffolding command:

```bash
omniagents new my-first-agent
```

This creates a complete project structure with:
- A default agent configuration
- Sample tools (`echo` and `word_count`)
- Instructions template
- Environment configuration
- Project manifest (`project.yml`)

## Step 2: Navigate to Your Project

```bash
cd my-first-agent
```

## Step 3: Set Up API Key

OmniAgents uses the OpenAI API. Create a `.env` file in your project directory:

```bash
cp .env.example .env
```

Then edit `.env` and add your API key:

```
OPENAI_API_KEY=your-api-key-here
```

**Using a custom OpenAI provider?** If you're using Azure OpenAI, a local LLM, or another OpenAI-compatible API, also configure the base URL:

```
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://your-custom-endpoint.com/v1
```

Alternatively, export as environment variables:

```bash
export OPENAI_API_KEY="your-api-key-here"
export OPENAI_BASE_URL="https://your-custom-endpoint.com/v1"
```

## Step 4: Run Your Agent

The simplest way to run your agent is with the default web mode:

```bash
omniagents run
```

This automatically detects the `project.yml` in your current directory and launches the web interface.

### Other Run Modes

You can also specify different modes:

```bash
# Web mode (browser-based interface, default)
omniagents run --mode web

# Ink mode (React-based terminal UI)
omniagents run --mode ink

# Voice mode (voice-based interaction)
omniagents run --mode voice

# Server mode (JSON-RPC API)
omniagents run --mode server
```

## Step 5: Try the Sample Tools

Your agent comes with two sample tools you can try:

```
You: Echo "Hello, OmniAgents!"
Agent: [Uses echo tool]
Agent: Hello, OmniAgents!

You: Count the words in "The quick brown fox jumps"
Agent: [Uses word_count tool]
Agent: There are 5 words.
```

## Customizing Your Agent

### Edit Instructions

Customize your agent's behavior by editing `agents/my_first_agent/instructions.md`:

```markdown
You are a helpful assistant that can echo messages and count words.
Be friendly and concise in your responses.
```

### Add More Tools

Create new tool files in the `tools/` directory:

```python
from omniagents import function_tool
from datetime import datetime

@function_tool
def get_current_time() -> str:
    """Get the current time in a human-readable format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

Then add your tool to `agents/my_first_agent/agent.yml`:

```yaml
tools: [echo, word_count, get_current_time]
```

### Enable Safe Mode

To require approval before executing tools, edit `agents/my_first_agent/agent.yml`:

```yaml
use_safe_agent: true
```

## Next Steps

- [Configuration](configuration.md) - Learn about advanced configuration options
- [Defining Agents](../guide/defining-agents.md) - Explore agent customization
- [Tools](../guide/tools.md) - Create more sophisticated tools
- [Safe Agent Mode](../guide/safe-agent.md) - Add approval requirements
