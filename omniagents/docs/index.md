# OmniAgents

OmniAgents provides a lightweight wrapper around the [OpenAI Agents](https://github.com/openai/openai-agents-python) library, offering multiple ways to interact with your AI agents.

## Features

- **Multiple Interface Modes**: Choose the interface that best suits your needs
    - Web - browser-based interface (default, includes optional realtime voice controls)
    - Ink - terminal UI built with React
    - Server - expose agents through a JSON-RPC WebSocket API

- **Simple Configuration**: Define agents using YAML files or Python code

- **Tool System**: Easily create and integrate custom tools using simple decorators

- **Safe Agent Mode**: Require user approval before executing tools

- **Session Management**: Persist and resume conversation history

- **Multi-Language**: Core Python framework with React/TypeScript components for web UI and terminal

## Quick Example

Define an agent with a simple YAML file:

```yaml
name: DemoAgent
instructions_file: instructions.md
tools: [get_current_time, flip_coin]
use_safe_agent: true
layout: tabs
```

Create tools in Python:

```python
from omniagents.core import function_tool

@function_tool
def get_current_time() -> str:
    """Get the current time."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

Run your agent:

```bash
omniagents run -P path/to/project.yml
```

## Getting Started

- [Installation](getting-started/installation.md) - Install OmniAgents
- [Quick Start](getting-started/quickstart.md) - Build your first agent
- [Configuration](getting-started/configuration.md) - Configure API keys and settings

## Documentation

- [User Guide](guide/defining-agents.md) - Learn how to define and customize agents
- [Backends](backends/overview.md) - Explore different interface modes
- [API Reference](api/core.md) - Detailed API documentation
- [Examples](examples.md) - Example projects and use cases

## Project Status

OmniAgents is under active development. The project wraps `openai-agents==0.4.2` and provides enhanced tooling and interfaces for building and deploying AI agents.
