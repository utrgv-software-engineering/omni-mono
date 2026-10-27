"""Compact, token-budgeted transcript rendering for one-shot reviewers.

Extracted from :mod:`omniagents.core.agents.guardian` so the plan-completion
reviewer (:mod:`omniagents.core.agents.plan_review`) shares one transcript
shape with the security guardian instead of duplicating it. The budgets and
line rendering are byte-for-byte the guardian's originals; ``guardian``
re-exports them under their historical module-level names.
"""

from __future__ import annotations

from typing import Any

#: Transcript budgets (approximate tokens; 4 chars ≈ 1 token). Recent
#: entries, with per-entry caps so one giant tool result can't crowd out the
#: user's actual intent; and, as codex's guardian does, the user's messages
#: are kept apart from that window, so tool traffic can never evict the
#: request that authorizes (or forbids) an action.
MAX_TRANSCRIPT_ENTRIES = 40
MAX_MESSAGE_ENTRY_CHARS = 8_000
MAX_TOOL_ENTRY_CHARS = 4_000
MAX_TRANSCRIPT_CHARS = 80_000
MAX_ACTION_CHARS = 64_000
#: The share of the transcript budget the user's earlier messages may take.
MAX_USER_MESSAGES_CHARS = MAX_TRANSCRIPT_CHARS // 2


async def compact_transcript(service: Any, session: Any) -> str:
    try:
        items = await service.get_session_history(session.id)
    except Exception:
        return ""
    if not isinstance(items, list):
        return ""
    start = max(0, len(items) - MAX_TRANSCRIPT_ENTRIES)
    earlier = _earlier_user_messages(items[:start])
    recent = "\n".join(line for line in map(transcript_line, items[start:]) if line)
    budget = MAX_TRANSCRIPT_CHARS - len(earlier)
    if len(recent) > budget:
        recent = "[earlier transcript truncated]\n" + recent[-budget:]
    return f"{earlier}{recent}"


def _earlier_user_messages(items: list) -> str:
    """The user's messages from before the recent window, in order.

    The first (the task) is always kept; when they exceed their budget, the
    latest are kept after it and the omission is stated, never silent.
    """
    lines = [
        transcript_line(item)
        for item in items
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    lines = [line for line in lines if line]
    if not lines:
        return "[earlier transcript truncated]\n" if items else ""
    kept = [lines[0]]
    used = len(lines[0])
    tail: list[str] = []
    for line in reversed(lines[1:]):
        if used + len(line) > MAX_USER_MESSAGES_CHARS:
            break
        tail.insert(0, line)
        used += len(line)
    omitted = len(lines) - 1 - len(tail)
    if omitted:
        kept.append(f"[{omitted} earlier user messages omitted]")
    kept.extend(tail)
    return (
        "[earlier user messages, in order]\n"
        + "\n".join(kept)
        + "\n[earlier tool activity truncated; recent transcript follows]\n"
    )


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
