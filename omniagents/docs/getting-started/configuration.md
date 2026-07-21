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
  # Optional extra search roots for {% include %} fragments in agent
  # instructions (string or list). The file-relative _shared/ conventions
  # and the framework's builtin fragments (searched last; project fragments
  # shadow them by name) are always active — see the "Shared instruction
  # fragments" section in the Defining Agents guide.
  # instruction_fragments: prompts/shared
agents:
  entrypoint: agent_name  # Default agent to run
  include: ["*"]          # Agent patterns to include (optional)
  exclude: []             # Agent patterns to exclude (optional)

tracing:
  provider: studio        # Options: default, studio, none
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
  - **model_catalog**: A YAML file (manifest-relative, e.g. `models.yml`)
    declaring the project's shipped provider/model catalog — `providers:`,
    `default_model:`, `default_provider:`. The framework builds the
    process-wide model store from it with conventional hooks at product
    bootstrap and project load; an explicit `set_model_store` wins. See
    `omniagents.core.models.catalog` for the full schema and timing contract.
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

## Product Identity

Projects that ship as installable products (their own CLI, config
directory, platform slug, self-update channel) declare their identity
once, in a top-level `product:` section of `project.yml`, instead of
handing the same constants to each framework subsystem:

```yaml
product:
  name: omni-code          # distribution/package name (default: slug with "_" -> "-")
  prog: omni               # CLI program name for --help rendering (default: name)
  label: Omni Code         # human display label, e.g. in `omni model` (default: name)
  slug: omni_code          # platform/project slug for policy scoping
  config_dir: omni_code    # per-user config dir name (default: slug)
  windows_config_dir: OmniCode   # %APPDATA% dir name on Windows (default: config_dir)
  env_prefix: OMNI_CODE    # env-var prefix (default: slug upper-cased)
  update:                  # optional; presence enables self-update defaults
    index_url: https://pypi.fury.io/me/omni-code/   # PEP-503 simple project page (required)
    package: omni-code     # pipx/metadata distribution name (default: name)
    command_hint: omni update    # shown in update notices (default: "<prog> update")
```

Every key has a sensible default, so a minimal `product: {slug: x}`
works. At least one of `slug` / `name` is required; unknown keys are
rejected (typo protection).

### How identity is installed

- `omniagents.core.product.bootstrap_product(project_path)` — the single
  call a product's `cli.py` makes first. It light-parses the manifest's
  `product:` key (`load_product_info`; a plain YAML read — no agent
  discovery, no overlays) and installs the result process-wide via
  `set_product_info()`.
- `load_project` also installs the manifest's `product:` section
  automatically — but an explicit `set_product_info()` /
  `bootstrap_product()` always wins and is never clobbered by a later
  project load.
- Without either, `product_info()` returns the framework's own
  `omniagents` identity.

### What defaults from it

With identity installed, the framework subsystems no longer need
identity arguments (explicit arguments still win everywhere):

- `run_model_command` / `run_provider_command` / `run_codex_auth_command`
  / `run_platform_auth_command`: `prog` (and the model CLI's
  `product_label`) default from the product.
- `product_config_dir()` (argless): the product's per-user config
  directory; also available as `product_info().config_dir()`.
- Platform client: policy slug (`get_product_slug()`) and the
  `platform.json` credential store directory.
- Codex token store: `codex.json` directory (after the
  `OMNIAGENTS_CODEX_HOME` env override).
- Self-update: `UpdaterConfig.from_product()` builds the updater config
  from the `product.update:` block, and `run_update_command()` with no
  `config=` uses it (a clear error is raised when no `update:` block is
  declared).
- Sandbox profiles: `resolve_profile()` reads the user profile layers
  from the product config dir when `config_dir` is not passed.

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

OmniAgents supports multiple tracing providers for monitoring and debugging agent execution. **By default, OmniAgents uses Studio tracing** which stores traces locally and never sends data to external servers.

### Tracing Providers

Optionally configure tracing in your `project.yml`:

```yaml
tracing:
  provider: studio        # Options: studio, openai, none
  project: myproject      # Project name for trace organization
  name: trace_name        # Optional trace name
  settings:
    resume: never         # Options: allow, must, never
```

**Provider Options:**

- **studio** (default): Uses OmniAgents Studio for local trace storage and visualization
  - Requires: `pip install omniagents[studio]`
  - If not installed, tracing is disabled (no data sent externally)
- **openai**: Opt-in to OpenAI SDK's default tracing (sends traces to OpenAI)
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
