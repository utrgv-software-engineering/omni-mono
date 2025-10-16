# Tracing Configuration in OmniAgents

OmniAgents supports configurable tracing providers to help you monitor and debug your agents. By default, the OpenAI Agents SDK sends traces to OpenAI's servers. You can override this behavior using the `tracing` configuration in your YAML files.

## Configuration

Add a `tracing` section to your agent's YAML configuration:

```yaml
tracing:
  provider: studio  # Options: default, studio, agentio, none
  project: my_project
  name: my_trace
  settings:
    resume: allow
```

## Providers

### `default` (Default Behavior)
Keeps the OpenAI SDK's default behavior - traces are sent to OpenAI's servers.

```yaml
tracing:
  provider: default
```

Or simply omit the `tracing` section entirely.

### `studio` (Local Trace Storage)
Uses the built-in OmniAgents Studio tracing module to store traces locally and provide a web UI for visualization. The legacy value `agentio` is still accepted as an alias for compatibility, but new projects should use `studio`.

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

### Example 2: Disable Tracing for Development

```yaml
name: Development Agent
model: gpt-4o
instructions: Agent for development testing.

# Disable all tracing during development
tracing:
  provider: none

tools:
  - debug_tool
```

### Example 3: Keep Default OpenAI Tracing

```yaml
name: Production Agent
model: gpt-4o
instructions: Production agent with default tracing.

# Explicitly use default OpenAI tracing
tracing:
  provider: default

tools:
  - production_tool
```

## How It Works

1. When OmniAgents loads your YAML configuration, it reads the `tracing` section
2. During Runner initialization, before creating the agent, it configures the tracing provider
3. For `studio`: Replaces OpenAI's default trace processor with OmniAgents Studio's processor
4. For `none`: Clears all trace processors to disable tracing
5. For `default`: Leaves OpenAI's default configuration unchanged

## Fallback Behavior

- If `studio` is configured but dependencies are not installed, OmniAgents will:
  - Print a warning message
  - Fall back to default OpenAI tracing
  - Continue running normally

- If an unknown provider is specified, OmniAgents will:
  - Print a warning message
  - Use default OpenAI tracing

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
