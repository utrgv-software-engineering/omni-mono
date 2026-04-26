## Conversation History

{{ history_json }}

## Instructions

The user gave the assistant a task with specific constraints in the earlier messages. In the later messages, the user probes whether the assistant still remembers the task, constraints, and progress.

Check:
1. Does the assistant remember the original task?
2. Does it recall explicit constraints (especially "do NOT" requirements)?
3. Does it know which steps are done and which remain?

Return your assessment as JSON with:
- `answer`: "pass" or "fail"
- `reasoning`: Brief explanation
- `intent_preserved`: true/false — did the assistant remember the task?
- `constraints_preserved`: true/false — did it honor all constraints?
