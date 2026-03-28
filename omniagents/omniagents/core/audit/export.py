"""Export session transcripts and audit logs with optional redaction.

Supports PIA (Public Information Act) requests and compliance review
by producing structured, optionally redacted transcripts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from omniagents.core.providers.content_scanner.base import ContentScanner


async def _redact(text: str, scanner: Optional[ContentScanner]) -> str:
    """Redact *text* using *scanner* if available, otherwise return as-is."""
    if scanner is None or not text:
        return text
    result = await scanner.scan(text, direction="output")
    return result.redacted_content if result.redacted_content else text


async def export_session(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
    redactor: Optional[ContentScanner] = None,
) -> dict:
    """Produce a structured transcript for a session.

    Returns::

        {
            "session_id": "...",
            "exported_at": "ISO timestamp",
            "message_count": N,
            "messages": [ ... ]
        }

    If *redactor* is provided, text content in messages is scanned and
    redacted before inclusion.
    """
    from omniagents.core.session.history_db import load_history

    messages = load_history(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )
    exported: List[dict] = []
    for msg in messages:
        msg_copy = dict(msg)
        content = msg_copy.get("content")
        if isinstance(content, str) and redactor:
            msg_copy["content"] = await _redact(content, redactor)
        exported.append(msg_copy)

    return {
        "session_id": session_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "message_count": len(exported),
        "messages": exported,
    }


async def export_audit_log(
    path: str,
    *,
    filters: Optional[Dict[str, Any]] = None,
    redactor: Optional[ContentScanner] = None,
) -> List[dict]:
    """Export audit log entries with optional filtering and redaction.

    Filters (all optional):

    - ``session_id`` — match exact session ID
    - ``event_type`` — match exact event type
    - ``user_id`` — match exact user ID
    - ``after`` — ISO timestamp lower bound (inclusive)
    - ``before`` — ISO timestamp upper bound (inclusive)
    """
    filters = filters or {}
    entries: List[dict] = []
    log_path = Path(path)
    if not log_path.exists():
        return []

    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Apply filters
            if (
                filters.get("session_id")
                and entry.get("session_id") != filters["session_id"]
            ):
                continue
            if (
                filters.get("event_type")
                and entry.get("event_type") != filters["event_type"]
            ):
                continue
            if (
                filters.get("user_id")
                and entry.get("user_id") != filters["user_id"]
            ):
                continue
            if (
                filters.get("after")
                and entry.get("timestamp", "") < filters["after"]
            ):
                continue
            if (
                filters.get("before")
                and entry.get("timestamp", "") > filters["before"]
            ):
                continue

            # Redact sensitive fields in details
            if redactor and "details" in entry:
                details = entry["details"]
                for key in ("input", "output"):
                    if key in details and isinstance(details[key], str):
                        details[key] = await _redact(details[key], redactor)

            entries.append(entry)

    return entries
