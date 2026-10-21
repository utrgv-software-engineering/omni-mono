# Tracing Configuration in OmniAgents

OmniAgents supports configurable tracing providers to help you monitor and debug your agents. **By default, OmniAgents uses Studio tracing** which stores traces locally and never sends data to external servers. Users must explicitly opt-in to OpenAI tracing via `provider: openai`.

## Configuration

Add a `tracing` section to your agent's YAML configuration:

```yaml
tracing:
  provider: studio  # Options: default, studio, none
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
Uses the built-in OmniAgents Studio tracing module to store traces locally and provide a web UI for visualization.

```yaml
tracing:
  provider: studio
  project: my_project     # Project name for grouping traces
  name: my_agent_trace    # Optional: specific trace name
  settings:               # Optional: additional Studio settings
    resume: allow         # Options: must, allow, never
    db_path: /custom/path/traces.db  # Optional: custom database path
```

**Requirements:**
- Install Studio extras: `pip install omniagents[studio]`
- View traces: `omniagents studio serve --project my_project`

**Benefits:**
- Traces stored locally (no data sent to OpenAI)
- Web UI for visualization
- Export capabilities (JSON, CSV, Parquet)
- Multi-project support

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
3. **No config or `default`**: Uses Studio tracing (local storage, no external data)
4. For `studio`: Uses OmniAgents Studio for local trace storage
   - If Studio is not installed, tracing is disabled (no fallback to OpenAI)
5. For `openai`: Enables OpenAI's default tracing behavior (explicit opt-in required)
6. For `none`: Disables all tracing

## Fallback Behavior

- If `studio` is configured but dependencies are not installed, OmniAgents will:
  - Print a warning message
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

For Python-based agents without YAML, you can use OmniAgents Studio directly:

```python
from omniagents.studio import tracing as studio_tracing
from agents import Agent
from omniagents import Runner

# Initialize Studio tracing before creating the agent
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
