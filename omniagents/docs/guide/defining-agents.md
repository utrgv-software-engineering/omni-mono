# Defining Agents

OmniAgents uses YAML configuration files to define agents. This approach separates agent configuration from code, making it easy to version control and modify agent behavior without changing code.

## Basic Structure

A minimal agent configuration requires only a name and instructions:

```yaml
name: my_agent
instructions_file: instructions.md
```

A more complete example with commonly used fields:

```yaml
name: data_analyst
label: Data Analysis Agent
description: "Analyzes data and generates insights"

model: gpt-4o-mini
instructions_file: instructions.md

tools:
  - read_csv
  - calculate_statistics
  - generate_chart

use_safe_agent: true
layout: tabs
welcome_text: "Hello! I can help you analyze your data."

model_settings:
  temperature: 0.3
  max_tokens: 2000
```

### Instructions File

The `instructions_file` should be a markdown file (relative to `agent.yml`) containing the agent's system instructions:

**instructions.md:**
```markdown
# Data Analyst

You are a data analysis specialist with expertise in statistics and visualization.

## Your Capabilities
- Read and parse CSV files
- Calculate statistical measures (mean, median, mode, standard deviation)
- Generate charts and visualizations
- Identify trends and patterns in data

## Guidelines
1. Always validate data before analysis
2. Explain your methodology clearly
3. Provide context for statistical findings
4. Suggest visualizations when appropriate
5. Ask for clarification if data format is unclear
```

### Tool Organization

Tools are discovered automatically from your project structure:

```
myproject/
├── project.yml
└── agents/
    └── data_analyst/
        ├── agent.yml
        ├── instructions.md
        └── tools/              # Agent-specific tools
            ├── read_csv.py
            └── calculate_statistics.py
```

In your agent.yml, reference tools by their function names:

```yaml
tools:
  - read_csv
  - calculate_statistics
```

See [Tools](tools.md) for details on creating tool functions.

### Example Projects

See the examples directory:
- `examples/mcp_demo/` - Agent with MCP server integration
- `examples/handoff_yaml/` - Multi-agent system with handoffs
- `examples/minimal_with_model_settings/` - Simple agent with custom model settings

## Agent Configuration Reference

### Core Fields

#### name (required)
The agent's identifier. Must be lowercase, start with a letter, and contain only letters, numbers, and underscores.

```yaml
name: my_agent
```

**Note**: When defining agents in a project structure (`agents/<agent_name>/agent.yml`), the name must match the directory name.

#### label (optional)
Display-friendly name shown in UIs.

```yaml
label: My Amazing Agent
```

#### description (optional)
Short description of the agent's purpose.

```yaml
description: "An agent that helps with data analysis tasks."
```

#### model (optional)
The LLM model to use. Defaults to `gpt-4o`.

```yaml
model: gpt-4o-mini
```

### Instructions

Define the agent's system instructions using one of these methods:

#### instructions_file (recommended)
Path to a markdown file containing instructions, relative to the agent.yml file:

```yaml
instructions_file: instructions.md
```

#### instructions (inline)
Inline instructions as a string or multiline YAML string:

```yaml
instructions: |
  You are a helpful assistant specialized in data analysis.

  ## Guidelines
  - Always explain your reasoning
  - Ask clarifying questions when needed
```

#### Shared instruction fragments ({% include %})

Instructions are Jinja2 templates and may include shared fragments so
sibling agents (e.g. a text agent and its voice counterpart) don't drift
apart by copying the same lines:

```jinja
{# agents/voice/instructions.md #}
You are the voice interface.
{% include "core_rules.md" %}
{% include "tone.md" %}
```

For `{% include "name.md" %}` the fragment is looked up, in order, in:

1. the directory containing the instructions file (for inline
   `instructions:`, the directory containing `agent.yml`) — relative
   subpaths like `{% include "fragments/safety.md" %}` work too,
2. a `_shared/` subdirectory of that directory,
3. a `_shared/` directory that is a *sibling* of that directory — with the
   strict `agents/<key>/agent.yml` layout this is `agents/_shared/`, the
   recommended home for fragments shared across agents,
4. any directories listed under `paths.instruction_fragments` in
   `project.yml` (resolved relative to the project root; applies when the
   agent is loaded through the project loader):

```yaml
# project.yml
paths:
  instruction_fragments: prompts/shared   # string or list
```

The framework ships no instruction prose of its own — every fragment an
agent includes is project content. `omniagents new` scaffolds starter
fragments under `agents/_shared/` (`personality.md`, `proactiveness.md`,
`agents_md_spec.md`, `task_execution.md`) that the generated entrypoint
agent includes; edit or delete them freely, they are yours.

Includes nest (a fragment may itself `{% include %}` another) and render
with the same Jinja2 context as the parent template. A missing fragment
raises an error naming the searched directories. Templates that use no
includes render exactly as before.

### Tools

#### tools
List of tool function names to make available to the agent:

```yaml
tools:
  - calculate_sum
  - fetch_data
  - generate_report
```

Tools are discovered from:
1. The `tools/` directory adjacent to the agent.yml file
2. Project-level `tools/` directory (specified in project.yml)
3. Additional directories via `additional_tools_dirs`

See [Tools](tools.md) for details on creating tools.

#### additional_tools_dirs
Additional directories to search for tools, relative to the agent.yml file:

```yaml
additional_tools_dirs:
  - ../shared_tools
  - ../../common/tools
```

### Model Configuration

#### model_settings
Fine-tune model behavior with OpenAI API parameters:

```yaml
model_settings:
  temperature: 0.7          # Randomness (0.0 - 2.0)
  top_p: 0.9               # Nucleus sampling
  max_tokens: 1000         # Maximum response length
  frequency_penalty: 0.0   # Reduce repetition (-2.0 to 2.0)
  presence_penalty: 0.0    # Encourage topic diversity (-2.0 to 2.0)
  tool_choice: auto        # "auto", "required", "none", or specific tool name
  parallel_tool_calls: true  # Allow multiple tool calls in one response
```

#### max_turns
Maximum conversation turns per agent run (default: 20):

```yaml
max_turns: 50
```

### Agent Behavior

#### tool_use_behavior
How the agent handles tool calls:

```yaml
# Run LLM again after tool execution (default)
tool_use_behavior: run_llm_again

# Stop after first tool call
tool_use_behavior: stop_on_first_tool

# Stop only when specific tools are called
tool_use_behavior:
  stop_at_tool_names:
    - final_answer
    - submit_result
```

#### reset_tool_choice
Reset `tool_choice` to auto after each tool call (default: true):

```yaml
reset_tool_choice: false
```

### Safety & Approval

#### use_safe_agent
Require user approval before executing tools (default: false):

```yaml
use_safe_agent: true
```

#### safe_agent_options
Fine-grained safety configuration:

```yaml
safe_agent_options:
  skip_approvals: false          # Skip all approvals
  halt_on_rejection: true        # Stop run if user rejects a tool
  safe_tool_names:               # Tools that don't require approval
    - read_file
    - search
  safe_tool_patterns:            # Tool name patterns (regex)
    - "^get_.*"
    - ".*_read$"
```

See [Safe Agent Mode](safe-agent.md) for details.

### UI Configuration

#### welcome_text
Initial message shown to users:

```yaml
welcome_text: "Hello! I'm your data analysis assistant. How can I help?"
```

#### layout
UI layout mode:

```yaml
layout: tabs    # Separate conversation and tool logs
# or
layout: classic # Single unified view (default)
```

#### theme_color
Primary color for UI elements (hex code):

```yaml
theme_color: "#2196F3"
```

#### icon_map
Custom icons for specific tools:

```yaml
icon_map:
  search_web: search
  calculate: calculate
  send_email: mail
```

## Advanced Configuration

### Handoffs (Multi-Agent Systems)

Enable your agent to delegate tasks to other specialized agents.

Every agent in a project is project-defined: the framework ships builtin
capability packs, tools, instruction fragments, and prompt seams, but no
agents. Sub-agents referenced by `handoffs:`, `agent_tools:`, or a
capability pack's `agent_tools:` always resolve against the project's own
`agents/<key>/agent.yml` registry.

#### handoffs
List of agents this agent can hand off to:

```yaml
handoffs:
  # Reference external agent YAML file
  - yaml: ../math_agent/agent.yml
    tool_name_override: ask_math_expert
    tool_description_override: "Delegate mathematical problems to the math specialist"

  # Inline agent definition
  - inline:
      name: quick_responder
      model: gpt-4o-mini
      instructions: "Provide brief, concise answers."
      tools: []
    tool_name_override: get_quick_answer
```

#### handoff_description
Description shown when this agent is used as a handoff target:

```yaml
handoff_description: "Specialist in data analysis and visualization"
```

### Guardrails

Validate and filter inputs and outputs for safety and compliance.

#### input_guardrails
List of input guardrail names to apply before processing user messages:

```yaml
input_guardrails:
  - block_pii
  - check_content_policy
```

#### output_guardrails
List of output guardrail names to apply before returning responses:

```yaml
output_guardrails:
  - filter_sensitive_data
  - validate_json_output
```

Guardrails are discovered from the `guardrails/` directory in your project. See the guardrails documentation for details on creating custom guardrails.

### Server Functions

Expose server-side functions that clients can call via RPC.

#### server_functions
List of server function names to register, or `all` to register all discovered functions:

```yaml
# Register specific functions
server_functions:
  - get_user_profile
  - save_preferences

# Or register all discovered functions
server_functions: all
```

#### additional_server_functions_dirs
Additional directories to search for server functions:

```yaml
additional_server_functions_dirs:
  - ../shared_server_functions
```

Server functions are discovered from the `server_functions/` directory.

### MCP Servers

Integrate Model Context Protocol servers to extend agent capabilities.

#### mcp_servers
List of MCP server configurations:

```yaml
mcp_servers:
  - name: filesystem-server
    type: stdio
    params:
      command: mcp-server-filesystem
      args:
        - "/allowed/directory"
    options:
      name: Filesystem Tools
      cache_tools_list: true

  - name: web-fetch-server
    type: stdio
    params:
      command: demo_mcp_server
```

**MCP Server Configuration:**
- **name**: Unique identifier for the MCP server
- **type**: Server type (`stdio` for standard I/O communication)
- **params**: Server-specific parameters
  - **command**: Executable command to launch the server
  - **args**: Command-line arguments (optional)
- **options**: Additional options
  - **name**: Display name for UI
  - **cache_tools_list**: Cache tool list for performance (optional)

#### mcp_config
Extended MCP configuration options:

```yaml
mcp_config:
  timeout: 30
  retry_attempts: 3
```

### Environment Variables

Use environment variable substitution in your configuration:

```yaml
model_settings:
  temperature: ${AGENT_TEMPERATURE:-0.7}  # Use env var or default to 0.7

variables:
  api_endpoint: ${API_ENDPOINT:?API_ENDPOINT must be set}
  api_key: ${API_KEY}
```

**Syntax:**
- `${VAR_NAME}` - Required variable (error if not set)
- `${VAR_NAME:-default}` - Use default value if not set
- `${VAR_NAME:?error message}` - Custom error message if not set

### Variables & Context

Provide dynamic context to agent instructions:

#### variables
Key-value pairs accessible in instructions via template variables:

```yaml
variables:
  user_name: ${USER_NAME}
  environment: production
  api_endpoint: https://api.example.com
```

These variables can be referenced in your instructions file or used by your agent's runtime context.

### Output Configuration

#### output_type
Enforce structured output format:

```yaml
output_type: dict  # Options: str, dict, list, int, float, bool
```

#### output_type_strict
Use strict JSON schema validation (default: true):

```yaml
output_type_strict: true
```

#### output_schema
Define explicit JSON schema for outputs:

```yaml
output_schema:
  properties:
    summary:
      type: string
    confidence:
      type: number
    recommendations:
      type: array
```

## Best Practices

### 1. Use Instruction Files
Store instructions in separate markdown files rather than inline YAML strings:

```yaml
# Good
instructions_file: instructions.md

# Avoid
instructions: "You are a helpful assistant..."
```

This makes instructions easier to read, edit, and version control.

### 2. Start Simple
Begin with minimal configuration and add complexity as needed:

```yaml
name: my_agent
model: gpt-4o-mini
instructions_file: instructions.md
tools: []
```

### 3. Use Safe Mode for Destructive Operations
Enable `use_safe_agent` for agents that can modify data or make external changes:

```yaml
use_safe_agent: true
safe_agent_options:
  safe_tool_names:
    - read_only_tool  # These don't need approval
    - search
```

### 4. Organize Multi-Agent Systems
Use clear naming and directory structure for handoff agents:

```
agents/
  ├── coordinator/
  │   └── agent.yml
  ├── math_specialist/
  │   └── agent.yml
  └── research_assistant/
      └── agent.yml
```

### 5. Use Environment Variables for Secrets
Never hardcode sensitive data in YAML files:

```yaml
# Good
variables:
  api_key: ${API_KEY}
  database_url: ${DATABASE_URL}

# Bad - Don't do this!
variables:
  api_key: "sk-1234567890abcdef"
```

### 6. Document Your Agents
Use `label` and `description` fields for clarity:

```yaml
name: data_analyst
label: Data Analysis Agent
description: "Analyzes CSV files and generates statistical reports"
```

### 7. Test with Lower-Cost Models First
Use `gpt-4o-mini` for development and testing before switching to more expensive models:

```yaml
# Development
model: gpt-4o-mini

# Production (when ready)
# model: gpt-4o
```

### 8. Leverage Model Settings
Fine-tune model behavior for your use case:

```yaml
model_settings:
  temperature: 0.3  # Lower for factual/deterministic tasks
  # temperature: 0.9  # Higher for creative tasks
```

### 9. Version Control Everything
Keep all agent configurations, instructions, and tools in version control (git) to track changes and enable rollbacks.

## Next Steps

- [Tools](tools.md) - Learn how to create tools
- [Running Modes](running-modes.md) - Explore different ways to run agents
- [Safe Agent Mode](safe-agent.md) - Add approval requirements
