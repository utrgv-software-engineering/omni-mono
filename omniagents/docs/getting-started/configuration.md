# Configuration

OmniAgents can be configured through environment variables, YAML files, and runtime settings.

## API Keys

### OpenAI API Key

Set your OpenAI API key via environment variable:

```bash
export OPENAI_API_KEY="sk-..."
```

Or in a `.env` file:

```
OPENAI_API_KEY=sk-...
```

### Custom OpenAI Providers

If you're using a custom OpenAI-compatible provider (Azure OpenAI, local LLMs, or other compatible APIs), configure the base URL:

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://your-custom-endpoint.com/v1"
```

Or in your `.env` file:

```
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-custom-endpoint.com/v1
```

**Common Custom Providers:**

- **Azure OpenAI**: Use Azure-specific environment variables:
  ```
  AZURE_OPENAI_API_KEY=your-azure-key
  AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
  AZURE_OPENAI_API_VERSION=2024-02-01
  AZURE_OPENAI_DEPLOYMENT=your-deployment-name
  ```

- **Local LLMs** (LM Studio, Ollama, etc.):
  ```
  OPENAI_API_KEY=not-needed
  OPENAI_BASE_URL=http://localhost:1234/v1
  ```

- **Other Providers** (OpenRouter, Together AI, etc.):
  ```
  OPENAI_API_KEY=your-provider-key
  OPENAI_BASE_URL=https://api.provider.com/v1
  ```

### GUI Settings

When using GUI mode, you can enter API keys in the settings screen. These are persisted in browser local storage.

## Agent Configuration

Agents are configured using YAML files. Here's a complete example:

```yaml
name: MyAgent
instructions_file: instructions.md
tools: [tool1, tool2, tool3]
use_safe_agent: true
layout: tabs
```

### Configuration Fields

- **name** (required): The agent's display name
- **instructions_file** (required): Path to a markdown file containing the agent's system instructions
- **tools** (optional): List of tool function names to make available to the agent
- **use_safe_agent** (optional, default: false): When true, requires user approval before executing tools
- **layout** (optional): UI layout mode. Options: `tabs` (separates conversation and tool logs)

## Session Storage

Conversation history is stored in `sessions.db` (SQLite database) at the repository root or a specified path.

### Resuming Sessions

Resume a previous conversation using the session ID:

```bash
omniagents run -P agent.yml --mode cli --session-id <session-id>
```

In TUI mode, use the `--resume` flag to open a session picker:

```bash
omniagents run -P agent.yml --mode tui --resume
```

## Tool Discovery

OmniAgents automatically discovers tools from:

1. A `tools/` directory in your project (if it exists)
2. Python files specified in your agent configuration
3. Modules specified via the `tools_dir` parameter

Tools must be decorated with `@function_tool` or `@rich_function_tool` from `omniagents.core`.

## Environment Variables

Common environment variables:

**OpenAI Configuration:**
- `OPENAI_API_KEY`: Your OpenAI API key (required)
- `OPENAI_BASE_URL`: Custom OpenAI-compatible API endpoint (optional)
- `OPENAI_ORG`: OpenAI organization ID (optional)
- `OPENAI_PROJECT`: OpenAI project ID (optional)

**Azure OpenAI Configuration:**
- `AZURE_OPENAI_API_KEY`: Azure OpenAI API key
- `AZURE_OPENAI_ENDPOINT`: Azure OpenAI endpoint URL
- `AZURE_OPENAI_API_VERSION`: Azure API version
- `AZURE_OPENAI_DEPLOYMENT`: Azure deployment name

**OmniAgents Configuration:**
- `OMNIAGENTS_DB_PATH`: Path to session database (default: `./sessions.db`)

**Other API Keys:**
- `SERPAPI_API_KEY`: SerpAPI key for web search tools (optional)

## Server Configuration

When running in server mode, additional options are available:

```bash
omniagents run -P agent.yml --mode server --host 0.0.0.0 --port 8000
```

- `--host`: Server host address (default: 127.0.0.1)
- `--port`: Server port (default: 8000)

## Next Steps

- [Defining Agents](../guide/defining-agents.md) - Learn more about agent configuration
- [Tools](../guide/tools.md) - Create custom tools
- [Running Modes](../guide/running-modes.md) - Explore different interface modes
