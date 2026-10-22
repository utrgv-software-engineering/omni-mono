# Tracing Configuration in OmniAgents

OmniAgents supports configurable tracing providers to help you monitor and debug your agents. **Tracing is off by default.** The `studio` provider stores traces locally and never sends data to external servers. Users must explicitly opt-in to OpenAI tracing via `provider: openai`.

## Configuration

Add a `tracing` section to your agent's YAML configuration:

```yaml
tracing:
  provider: studio  # Options: none (default), studio, openai
  project: my_project
  name: my_trace
  settings:
    resume: allow
```

## Providers

### `openai` (OpenAI Tracing)
Enables the OpenAI SDK's default behavior - traces are sent to OpenAI's servers.

```yaml
tracing:
  provider: openai
```

**Note:** You must explicitly opt-in to OpenAI tracing by specifying `provider: openai`.

### `studio` (Local Trace Storage)
Records traces locally through `omniagents.core.tracing.local` into
`~/.omniagents/traces/<project>.db`, the store OmniAgents Studio reads.

```yaml
tracing:
  provider: studio
  project: my_project     # Project name for grouping traces
  name: my_agent_trace    # Optional: specific trace name
  settings:               # Optional: recorder settings
    resume: allow         # Options: must, allow, never
    db_path: /custom/path/traces.db  # Optional: custom database path
```

Recording is built into `omniagents`. To browse traces, install the separate
`omniagents-studio` package and run `omniagents-studio serve --project my_project`
(it also exports traces as JSON, CSV or Parquet).

### `none` (Disable Tracing)
Completely disables all tracing.

```yaml
tracing:
  provider: none
```

This prevents any traces from being collected or sent anywhere.

## Examples

### Example 1: Using Studio for Local Tracing

```yaml
name: Customer Support Agent
model: gpt-4o
instructions: You are a helpful customer support agent.

# Store traces locally with OmniAgents Studio
tracing:
  provider: studio
  project: customer_support
  name: support_sessions
  settings:
    resume: allow  # Resume existing traces if name matches

tools:
  - search_knowledge_base
  - create_ticket
```

### Example 2: Enable OpenAI Tracing (Opt-in)

```yaml
name: Production Agent
model: gpt-4o
instructions: Production agent with OpenAI tracing.

# Explicitly opt-in to OpenAI tracing
tracing:
  provider: openai

tools:
  - production_tool
```

### Example 3: Default Behavior (No Tracing)

```yaml
name: Development Agent
model: gpt-4o
instructions: Agent for development testing.

# No tracing section needed - tracing is disabled by default
tools:
  - debug_tool
```

Or explicitly disable with:

```yaml
tracing:
  provider: none
```

## How It Works

1. When OmniAgents loads your YAML configuration, it reads the `tracing` section
2. During Runner initialization, before creating the agent, it configures the tracing provider
3. **No config or `default`**: Same as `studio`
4. For `studio`: Records traces locally (no external data)
5. For `openai`: Enables OpenAI's default tracing behavior (explicit opt-in required)
6. For `none`: Disables all tracing

## Fallback Behavior

- If the local recorder fails to start (e.g. `resume: must` naming a trace
  that does not exist), OmniAgents will:
  - Print an error message
  - Disable tracing (no data sent to OpenAI)
  - Continue running normally

- If an unknown provider is specified, OmniAgents will:
  - Print a warning message
  - Disable tracing (no data sent to OpenAI)

## Programmatic Usage

When using OmniAgents programmatically with Python, tracing is configured automatically based on your YAML:

```python
from omniagents import Runner

# Tracing is configured automatically from YAML
runner = Runner(config_path="agent.yaml")
runner.run(mode="cli")
```

For Python-based agents without YAML, start the local recorder directly:

```python
from omniagents.core.tracing import local as studio_tracing
from agents import Agent
from omniagents import Runner

# Start local trace recording before creating the agent
studio_tracing.init(project="my_project", name="my_trace")

agent = Agent(
    name="My Agent",
    instructions="You are a helpful assistant.",
    model="gpt-4o"
)

runner = Runner.from_agent(agent)
runner.run(mode="cli")
```

## Privacy and Security

- **`default`**: Traces are sent to OpenAI's servers (requires API key)
- **`studio`**: Traces stored locally on your machine (no external data transfer)
- **`none`**: No traces collected or stored anywhere

Choose the appropriate provider based on your privacy and compliance requirements.
