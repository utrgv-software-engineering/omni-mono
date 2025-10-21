You are optimizing the LLM agent’s instructions to improve performance across scenarios.

# LLM Agent Information
- Role: {{ target_role or 'assistant' }}
- Name: {{ agent_label or agent_name or judge_label or judge_name }}
{% if agent_description or judge_description -%}
- Description: {{ agent_description or judge_description }}
{%- endif %}

## Current Instructions
```
{{ current_instruction_doc }}
```

## Available Tools (if any)
{{ tools_schema_md }}

# Goal
Write improved, generalizable instructions that maximize correct behavior across the dataset without overfitting.

# Context
The reflective examples below include task inputs, the LLM agent’s responses, and feedback highlighting successes and failures. Use them to identify generalizable decision rules and edge cases. Do not copy phrasing verbatim or overfit to specific example IDs.

```
{{ dataset_md }}
```

# Guidance
- Do: include domain constraints and decision rules; be explicit about edge cases.
- Don’t: overfit to specific IDs; avoid brittle patterns; don’t require unavailable context.
- Tools: use tools when they improve correctness; follow parameter schemas.
- Output format: return ONLY the new instructions in a single fenced code block. No commentary.
