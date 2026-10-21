# Dynamic Instructions Examples

This directory contains four examples showing different approaches to using dynamic instructions and variables with AI agents.

## Examples

### 1. OpenAI Agents SDK Native Approach (`1_sdk_native.py`)

Shows the standard OpenAI Agents SDK approach using a context object:
- Uses a dataclass for typed context
- Passes context through `Runner.run()`
- Access variables via `context.context.field_name`

```bash
python 1_sdk_native.py
```

### 2. OmniAgents String Instructions (`2_omniagents_string.py`)

String instructions with user_id → context factory pattern:
- Client only passes `user_id` in variables dict
- `build_context` factory fetches full user data from database/API
- Define static instructions with Jinja2 placeholders
- Simulates real-world pattern where minimal data is sent from client

```bash
python 2_omniagents_string.py
```

### 3. OmniAgents Dynamic Function (`3_omniagents_function.py`)

Dynamic function with user_id → context factory pattern:
- Client only passes `user_id` in variables dict
- Factory fetches complete user profile from database/API
- Same pattern works for local, server, and remote execution
- Factory handles missing fields, extra fields, and type conversions
- Function modifies typed context at runtime (e.g., current time)

```bash
# Run locally
python 3_omniagents_function.py

# Run as server (for remote clients)
python 3_omniagents_function.py --mode server --port 8000
```

### 4. OmniAgents YAML Approach (`4_omniagents_yaml.yml`)

Pure configuration approach with no Python code:
- Define instructions with `{variable}` placeholders
- Set variables in the `dynamic_variables` section
- Perfect for non-programmers or configuration-driven deployments

```bash
python -m omniagents.cli run -c examples/dynamic_instructions/4_omniagents_yaml.yml
```

## Key Differences

| Approach | Pros | Cons |
|----------|------|------|
| **SDK Native** | Type-safe, full control, tools get typed context | Requires context object, more boilerplate |
| **OmniAgents String** | Variables → context pattern, simple Jinja2 templates | Static instructions only |
| **OmniAgents Function** | Variables → context with runtime modifications | Requires function for dynamic content |
| **OmniAgents YAML** | No code required, env var substitution | No type safety at YAML level |

## When to Use Each

- **SDK Native**: When you need full control and tools that access typed context directly
- **OmniAgents String**: When you want consistent variables → context pattern with static instructions
- **OmniAgents Function**: When you need variables → context with runtime-generated content
- **OmniAgents YAML**: When you want configuration-driven agents with environment variable support

## Context Features in OmniAgents

OmniAgents extends the SDK's context system:
- **Type-safe contexts**: Full support for dataclasses, Pydantic models, and dicts
- **Jinja2 templates**: Use `{{ field }}` placeholders in instruction strings
- **Unified system**: Same context is available to both instructions and tools
- **YAML support**: Define default context in YAML configuration
- **SDK compatible**: Context is passed through to SDK's `Runner.run()` for tools
- **RPC support**: Remote clients can pass context dynamically per request

The context serves dual purposes:
1. Template substitution in instructions via Jinja2 `{{ field }}` syntax
2. Tool access via `RunContextWrapper` (standard SDK pattern)

OmniAgents automatically converts various context types:
- **Dataclasses**: Converted via `asdict()` for Jinja2 rendering
- **Pydantic models**: Converted via `model_dump()`
- **Plain objects**: Converted via `vars()`
- **Dictionaries**: Used directly

## Remote Client with Dynamic Context (`remote_client.py`)

Demonstrates how external applications (web widgets, mobile apps) can pass context to a remote agent:

1. Start the server with the string template example:
```bash
python examples/dynamic_instructions/2_omniagents_string.py --mode server --port 8000
```

2. Run the client that passes different contexts:
```bash
python examples/dynamic_instructions/remote_client.py
```

This simulates real-world scenarios like:
- Hotel booking widget passing `hotel_id` and `user_id`
- SaaS platform passing tenant and user context
- Multi-brand chatbot receiving brand-specific context
