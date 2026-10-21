Return ONLY valid JSON (no markdown fences, no extra text).

You are summarizing a past agent session. The input contains:
- Session metadata
- A chronological transcript of message objects (raw JSON items, including tool calls/outputs when present)

Your job is to produce a structured summary that helps an agent quickly reuse the session.

JSON schema (keys must exist; use empty arrays/strings if unknown):
{
  "session_id": "string",
  "project": "string",
  "agent": "string",
  "created_at": "string|null",
  "message_count": 0,
  "summary": "string",
  "decisions": ["string"],
  "constraints": ["string"],
  "open_questions": ["string"],
  "next_steps": ["string"],
  "notable_errors": ["string"]
}

Guidelines:
- Prefer concrete, tool-verifiable details (names, identifiers, exact values).
- If the transcript includes tool outputs, extract the key results.
- Keep summary concise but information-dense.
