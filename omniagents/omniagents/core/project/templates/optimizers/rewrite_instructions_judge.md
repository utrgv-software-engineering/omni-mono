You are optimizing the LLM-as-Judge’s instructions to improve classification accuracy across scenarios.

# Judge Information
- Role: judge
- Name: {{ judge_label or judge_name }}
{% if judge_description -%}
- Description: {{ judge_description }}
{%- endif %}
{% if category -%}
- Target Category: {{ category }}
{%- endif %}
- Input: a conversation trace between a user and an AI Agent

# AI Agent Information
- Name: {{ agent_label or agent_name }}
{% if agent_description -%}
- Description: {{ agent_description }}
{%- endif %}

## Current Instructions
```
{{ current_instruction_doc }}
```

## Available Tools (if any)
{{ tools_schema_md }}

# Goal
Write improved, generalizable judge instructions that maximize agreement with labels without overfitting.

# Context
The reflective examples below include task inputs, the judge’s outputs, and feedback indicating where predictions matched or mismatched labels. Use them to refine decision rules and edge-case handling without memorizing examples.

```
{{ dataset_md }}
```

# Output Schema (Do Not Change)
- The judge must return ONLY JSON with keys: reasoning (string), answer ("Pass" or "Fail").
- Do not add, rename, or remove keys; do not change allowed values.
- Example:
```json
{
  "reasoning": "...",
  "answer": "Pass"
}
```

# Guidance
- Do: clarify decision rules; include edge-case handling and tie-breakers.
- Don’t: rely on example-specific artifacts; avoid leaking labels or overfitting.
- Tools: use tools for verification only; do not bias outcomes.
- Output format: return ONLY the new instructions in a single fenced code block. No commentary.
