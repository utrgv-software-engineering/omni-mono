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

Simple string instructions with `{variable}` placeholders:
- Define static instructions with placeholders
- Pass variables via `dynamic_variables` parameter
- Variables are automatically formatted into instructions

```bash
python 2_omniagents_string.py
```

### 3. OmniAgents Dynamic Function (`3_omniagents_function.py`)

Dynamic function that combines runtime content with variables:
- Function generates instructions at runtime (e.g., current time)
- Can still use `{variable}` placeholders in the returned string
- Best of both worlds: dynamic content + variable substitution

```bash
python 3_omniagents_function.py
```

### 4. OmniAgents YAML Approach (`4_omniagents_yaml.yaml`)

Pure configuration approach with no Python code:
- Define instructions with `{variable}` placeholders
- Set variables in the `dynamic_variables` section
- Perfect for non-programmers or configuration-driven deployments

```bash
python -m omniagents.cli run -c examples/dynamic_instructions/4_omniagents_yaml.yaml --mode cli
```

## Key Differences

| Approach | Pros | Cons |
|----------|------|------|
| **SDK Native** | Type-safe, full control, tools get typed context | Requires context object, more boilerplate |
| **OmniAgents String** | Simple context substitution in instructions | Context is dict, not typed |
| **OmniAgents Function** | Dynamic content + context substitution | Still uses dict context |
| **OmniAgents YAML** | No code required, easy to modify | Static context only |

## When to Use Each

- **SDK Native**: When you need type safety and tools that access typed context
- **OmniAgents String**: When you want simple context substitution in instruction strings
- **OmniAgents Function**: When you need dynamic instructions with context substitution
- **OmniAgents YAML**: When you want configuration-driven agents without writing code

## Context Features in OmniAgents

OmniAgents extends the SDK's context system:
- **Context substitution**: Use `{context.field}` placeholders in instruction strings
- **Unified system**: Same context is available to both instructions and tools
- **YAML support**: Define default context in YAML configuration
- **SDK compatible**: Context is passed through to SDK's `Runner.run()` for tools

The context serves dual purposes:
1. String substitution in instructions via `{context.field}` syntax
2. Tool access via `RunContextWrapper` (standard SDK pattern)