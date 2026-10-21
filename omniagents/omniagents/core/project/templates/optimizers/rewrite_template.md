You are optimizing the judge input template used to render evaluation inputs.

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

## Current Template
```
{{ current_template }}
```

## Available Tools (if any)
{{ tools_schema_md }}

# Goal
Write an improved, generalizable input template that presents the right evidence and constraints for correct classification. Preserve and correctly place required placeholders (e.g., {{ text }}, {{ history_json }}, etc.).

# Context
The reflective examples below include input text (and history where applicable), the judge’s outputs, and feedback indicating where predictions matched or mismatched labels. Use them to adjust what evidence and instructions the template surfaces without overfitting to specific instances.

```
{{ dataset_md }}
```

# Guidance
- Preserve required placeholders and Jinja syntax.
- Do not leak or hint at ground-truth labels.
- Be concise but complete; surface the artifacts the judge needs (e.g., history_json when relevant).
- Output format: return ONLY the new template in a single fenced code block. No commentary.
