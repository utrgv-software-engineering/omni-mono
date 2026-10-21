"""Compact, token-budgeted transcript rendering for one-shot reviewers.

Extracted from :mod:`omniagents.core.agents.guardian` so the plan-completion
reviewer (:mod:`omniagents.core.agents.plan_review`) shares one transcript
shape with the security guardian instead of duplicating it. The budgets and
line rendering are byte-for-byte the guardian's originals; ``guardian``
re-exports them under their historical module-level names.
"""

from __future__ import annotations

from typing import Any

#: Transcript budgets (approximate tokens; 4 chars ≈ 1 token). Mirrors the
#: shape of codex's guardian: recent entries only, with per-entry caps so one
#: giant tool result can't crowd out the user's actual intent.
MAX_TRANSCRIPT_ENTRIES = 40
MAX_MESSAGE_ENTRY_CHARS = 8_000
MAX_TOOL_ENTRY_CHARS = 4_000
MAX_TRANSCRIPT_CHARS = 80_000
MAX_ACTION_CHARS = 64_000


async def compact_transcript(service: Any, session: Any) -> str:
    try:
        items = await service.get_session_history(session.id)
    except Exception:
        return ""
    if not isinstance(items, list):
        return ""
    lines: list[str] = []
    for item in items[-MAX_TRANSCRIPT_ENTRIES:]:
        line = transcript_line(item)
        if line:
            lines.append(line)
    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = "[earlier transcript truncated]\n" + text[-MAX_TRANSCRIPT_CHARS:]
    return text


def transcript_line(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    role = item.get("role")
    if isinstance(role, str) and role:
        content = content_text(item.get("content"))
        if content:
            return f"{role}: {clip(content, MAX_MESSAGE_ENTRY_CHARS)}"
        return ""
    item_type = item.get("type")
    if item_type in ("function_call", "tool_call"):
        name = item.get("name") or item.get("tool_name") or "tool"
        arguments = item.get("arguments") or ""
        return f"tool-call {name}: {clip(str(arguments), MAX_TOOL_ENTRY_CHARS)}"
    if item_type in ("function_call_output", "tool_result"):
        output = item.get("output") or item.get("result") or ""
        return f"tool-result: {clip(content_text(output), MAX_TOOL_ENTRY_CHARS)}"
    return ""


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return "" if content is None else str(content)


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + " …[truncated]"


__all__ = [
    "MAX_ACTION_CHARS",
    "MAX_MESSAGE_ENTRY_CHARS",
    "MAX_TOOL_ENTRY_CHARS",
    "MAX_TRANSCRIPT_CHARS",
    "MAX_TRANSCRIPT_ENTRIES",
    "clip",
    "compact_transcript",
    "content_text",
    "transcript_line",
]
