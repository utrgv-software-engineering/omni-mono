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

### Web Settings

When using web mode, you can enter API keys in the settings screen. These are persisted in browser local storage.

## Project Configuration

OmniAgents uses a project-based structure defined by a `project.yml` file. This allows you to organize multiple agents, shared tools, guardrails, and evaluations in a single project.

### Project Structure

A typical OmniAgents project has the following structure:

```
myproject/
├── project.yml           # Project configuration
├── agents/              # Agent definitions
│   └── agent_name/
│       └── agent.yml    # Individual agent config
├── tools/               # Shared tools (optional)
├── guardrails/          # Input/output validation (optional)
├── server_functions/    # Server-side RPC functions (optional)
├── evaluations/         # Evaluation configs (optional)
└── target/              # Build artifacts (auto-generated)
```

### project.yml

Here's a complete example:

```yaml
name: myproject
paths:
  agents: agents
  tools: tools
  guardrails: guardrails
  server_functions: server_functions
  evaluations: evaluations
  target: target
agents:
  entrypoint: agent_name  # Default agent to run
  include: ["*"]          # Agent patterns to include (optional)
  exclude: []             # Agent patterns to exclude (optional)

tracing:
  provider: studio        # Options: default, studio, agentio, none
  project: myproject
  name: trace_name
  settings:
    resume: never         # Options: allow, must, never
```

### Project Configuration Fields

- **name** (required): Project identifier
- **paths** (optional): Custom directory paths for project components
  - **agents**: Directory containing agent definitions (default: `agents`)
  - **tools**: Shared tools directory (default: `tools`)
  - **guardrails**: Guardrails directory (default: `guardrails`)
  - **server_functions**: Server functions directory (default: `server_functions`)
  - **evaluations**: Evaluation configs directory (default: `evaluations`)
  - **target**: Build output directory (default: `target`)
- **agents** (required): Agent configuration
  - **entrypoint**: Default agent key to run
  - **include**: Glob patterns for agents to include
  - **exclude**: Glob patterns for agents to exclude
- **tracing** (optional): Tracing configuration (see Tracing Configuration below)

### Agent Configuration

Individual agents are defined in `agents/<agent_name>/agent.yml`:

```yaml
name: agent_name
instructions_file: instructions.md
tools: [tool1, tool2, tool3]
use_safe_agent: true
layout: tabs
```

### Agent Configuration Fields

- **name** (required): The agent's name (must match directory name, lowercase)
- **instructions_file** (required): Path to a markdown file containing the agent's system instructions
- **tools** (optional): List of tool function names to make available to the agent
- **use_safe_agent** (optional, default: false): When true, requires user approval before executing tools
- **layout** (optional): UI layout mode. Options: `tabs` (separates conversation and tool logs)

## Session Storage

Conversation history is stored in SQLite databases organized by project and agent.

### Storage Location

By default, sessions are stored in:
```
~/.omniagents/sessions/<project_slug>/<agent_slug>/sessions.db
```

You can customize the location using the `OMNIAGENTS_HISTORY_DB` environment variable:

```bash
export OMNIAGENTS_HISTORY_DB="/path/to/custom/sessions.db"
```

You can also change the base OmniAgents home directory:

```bash
export OMNIAGENTS_HOME="/path/to/custom/omniagents"
# Sessions will be stored in: /path/to/custom/omniagents/sessions/
```

### Resuming Sessions

Resume a previous conversation using the session ID:

```bash
# From within project directory
omniagents run --session-id <session-id>

# Or from outside project directory
omniagents run -P /path/to/myproject --session-id <session-id>
```

In web mode, use the session management UI to browse and resume sessions.

## Tracing Configuration

OmniAgents supports multiple tracing providers for monitoring and debugging agent execution.

### Tracing Providers

Configure tracing in your `project.yml`:

```yaml
tracing:
  provider: studio        # Options: default, studio, none
  project: myproject      # Project name for trace organization
  name: trace_name        # Optional trace name
  settings:
    resume: never         # Options: allow, must, never
```

**Provider Options:**

- **default**: Uses OpenAI SDK's default tracing (sends traces to OpenAI)
- **studio**: Uses OmniAgents Studio for local trace storage and visualization
  - Requires: `pip install omniagents[studio]`
- **none**: Disables all tracing

### Trace Storage

When using the `studio` provider, traces are stored locally at:

```
~/.omniagents/traces/
```

You can customize this location with the `OMNIAGENTS_HOME` environment variable:

```bash
export OMNIAGENTS_HOME="/path/to/custom/omniagents"
# Traces will be stored in: /path/to/custom/omniagents/traces/
```

### Viewing Traces

To view traces collected with the Studio provider:

```bash
omniagents studio
```

This launches the OmniAgents Studio web interface for trace visualization and analysis.

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
- `OMNIAGENTS_HOME`: Base directory for OmniAgents data (default: `~/.omniagents`)
  - Sessions stored in: `$OMNIAGENTS_HOME/sessions/`
  - Traces stored in: `$OMNIAGENTS_HOME/traces/`
- `OMNIAGENTS_HISTORY_DB`: Override session database path (default: `~/.omniagents/sessions/<project>/<agent>/sessions.db`)
- `OMNIAGENTS_AUDIO_STORAGE`: Override audio storage directory (default: `~/.omniagents/sessions/<project>/<agent>/audio/`)

**Other API Keys:**
- `SERPAPI_API_KEY`: SerpAPI key for web search tools (optional)

## Server Configuration

When running in server mode, additional options are available:

```bash
# From within project directory
omniagents run --mode server --host 0.0.0.0 --port 8000

# Or from outside project directory
omniagents run -P /path/to/myproject --mode server --host 0.0.0.0 --port 8000
```

- `--host`: Server host address (default: 127.0.0.1)
- `--port`: Server port (default: 8000)

## Running Projects

OmniAgents runs projects, not individual agent files. When you're inside a project directory (containing `project.yml`), the project is automatically discovered:

```bash
# Navigate to your project directory
cd /path/to/myproject

# Run the default agent in web mode (default)
omniagents run

# Run in ink mode
omniagents run --mode ink

# Run in server mode
omniagents run --mode server
```

You can also specify the project path using `-P` or `--project` if you're not in the project directory:

```bash
# Specify project directory
omniagents run -P /path/to/myproject

# Or specify project.yml directly
omniagents run -P /path/to/myproject/project.yml
```

If your project has multiple agents, specify which one to run:

```bash
omniagents run --agent agent_name
```

Enable realtime voice controls from the web interface settings when needed for audio capture/playback.

## Next Steps

- [Defining Agents](../guide/defining-agents.md) - Learn more about agent configuration
- [Tools](../guide/tools.md) - Create custom tools
- [Running Modes](../guide/running-modes.md) - Explore different interface modes
