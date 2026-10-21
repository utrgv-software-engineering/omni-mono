## Conversation History

{{ history_json }}

## Expected Conventions

The AGENTS.md in this workspace specifies these conventions:

{% for convention in scenario.expect.conventions %}
- {{ convention }}
{% endfor %}

## Instructions

Examine every file the assistant created or modified. Check whether the code follows the conventions listed above.

Return your assessment as JSON with:
- `answer`: "Pass" or "Fail"
- `reasoning`: Brief explanation
