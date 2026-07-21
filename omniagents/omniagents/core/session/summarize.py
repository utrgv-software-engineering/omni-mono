"""Offline session summarization: a structured JSON digest of a stored session.

``summarize_session`` loads a session from the sessions DB, trims the
history tail, runs the installed summarizer over the transcript (with
the standard shrink-on-context-error retry), and returns a JSON
envelope — identity keys (session id / project / agent / created_at /
message_count) plus a product-shapeable set of content keys. The
``sessions summarize`` CLI subcommand (``core/session/cli.py``) prints
that envelope.

Like recap (``core/runtime/recap.py``) and checkpoint compaction, the
*summarizer* is product policy: products install one with
:func:`set_session_summarizer` (typically a one-shot run of their
session-summarizer agent on their resolved model).
:data:`DEFAULT_SESSION_SUMMARY_INSTRUCTIONS` is a ready-made system
prompt for products that don't want to write their own, shaped to emit
the neutral :data:`DEFAULT_SESSION_ENVELOPE` keys; products with their
own envelope keys pass them via the ``envelope`` parameter (CLI mounts
read it from ``args.summary_envelope``).

Lifted from omni-code.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any, Awaitable, Callable, Mapping, Optional

from omniagents.core.paths import get_sessions_db_path
from omniagents.core.runtime.compaction import (
    extract_transcript,
    summarize_with_window_shrink,
)
from omniagents.core.session.history_db import load_history
from omniagents.core.session.query import (
    ensure_sessions_schema,
    populate_session_stats,
)

__all__ = [
    "DEFAULT_SESSION_SUMMARY_INSTRUCTIONS",
    "DEFAULT_SESSION_ENVELOPE",
    "set_session_summarizer",
    "session_summarizer",
    "trim_history_tail",
    "build_session_summary_prompt",
    "summarize_session",
]


# Lifted from omni-code's session_summarizer agent.
DEFAULT_SESSION_SUMMARY_INSTRUCTIONS = """\
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
"""


# The neutral default content keys of the summary envelope. Products with
# domain-flavored keys (e.g. a coding product's key_files/commands/tests)
# pass their own mapping as ``envelope=`` — keys are ensured on the
# summarizer's parsed output with these values as per-call defaults.
DEFAULT_SESSION_ENVELOPE: dict[str, Any] = {
    "summary": "",
    "decisions": [],
    "constraints": [],
    "open_questions": [],
    "next_steps": [],
    "notable_errors": [],
}


# async (prompt: str, *, model, project, agent, session_id) -> str
SessionSummarizer = Callable[..., Awaitable[str]]

_SUMMARIZER: Optional[SessionSummarizer] = None

_NO_SUMMARIZER_MESSAGE = (
    "Session summarization is unavailable: no session summarizer is installed "
    "(see omniagents.core.session.summarize.set_session_summarizer)."
)


def set_session_summarizer(summarizer: Optional[SessionSummarizer]) -> None:
    """Install the product's session summarizer.

    The callable receives ``(prompt, *, model, project, agent,
    session_id)`` and returns the summary text — typically a one-shot
    run of the product's session-summarizer agent on its resolved model
    (see :data:`DEFAULT_SESSION_SUMMARY_INSTRUCTIONS` for a ready-made
    prompt). ``model`` is the caller's model reference (the CLI's
    ``--model``), unresolved — resolution is product policy.
    """
    global _SUMMARIZER
    _SUMMARIZER = summarizer


def session_summarizer() -> Optional[SessionSummarizer]:
    return _SUMMARIZER


def trim_history_tail(items: list[dict], *, tail: int | None) -> list[dict]:
    """Keep the last *tail* items; ``None`` keeps everything."""
    if not items:
        return []
    if tail is None:
        return list(items)
    if tail <= 0:
        return []
    if len(items) <= tail:
        return list(items)
    return list(items[-tail:])


def build_session_summary_prompt(
    window: list[dict], *, session_meta: Mapping[str, Any]
) -> str:
    """The summarizer input: session metadata + the window transcript."""
    return (
        "Session metadata:\n"
        f"{json.dumps(dict(session_meta), ensure_ascii=False, default=str)}\n\n"
        "Transcript (chronological raw JSON items):\n"
        f"{extract_transcript(window)}"
    )


def _load_session_meta(
    session_id: str, *, project: str | None, agent: str | None
) -> dict[str, Any] | None:
    db_path = get_sessions_db_path(project, agent)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT session_id, archived, created_at FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "archived": bool(row[1]),
        "created_at": row[2],
        "message_count": None,
    }


async def summarize_session(
    session_id: str,
    *,
    project: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    tail: int | None = None,
    envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize a stored session into a JSON envelope dict.

    Loads the session, trims the history tail, runs the installed
    summarizer (:func:`set_session_summarizer`) with the standard
    shrink-on-context-error retry, and ensures the envelope keys on the
    parsed output. ``envelope`` maps content key -> default value
    (:data:`DEFAULT_SESSION_ENVELOPE` when omitted); identity keys
    (session_id/project/agent/created_at/message_count) are always
    ensured. Raises :class:`RuntimeError` when no summarizer is
    installed and :class:`LookupError` when the session doesn't exist.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise LookupError("session_id is required")

    summarizer = session_summarizer()
    if summarizer is None:
        raise RuntimeError(_NO_SUMMARIZER_MESSAGE)

    ensure_sessions_schema(project=project, agent=agent)
    session_meta = _load_session_meta(session_id, project=project, agent=agent)
    if session_meta is None:
        raise LookupError(f"Session not found: {session_id}")
    populate_session_stats(project=project, agent=agent, sessions=[session_meta])

    history = load_history(session_id, project_slug=project, agent_slug=agent)
    window = trim_history_tail(history, tail=tail)

    async def _summarize(prompt: str) -> str:
        return await summarizer(
            prompt,
            model=model,
            project=project,
            agent=agent,
            session_id=session_id,
        )

    raw = await summarize_with_window_shrink(
        window,
        build_prompt=lambda w: build_session_summary_prompt(
            w, session_meta=session_meta
        ),
        summarize=_summarize,
    )

    out: dict[str, Any]
    try:
        parsed = json.loads(raw)
        out = parsed if isinstance(parsed, dict) else {"raw": raw}
    except Exception:
        out = {"raw": raw}

    out.setdefault("session_id", session_id)
    out.setdefault("project", project)
    out.setdefault("agent", agent)
    out.setdefault("created_at", session_meta.get("created_at"))
    out.setdefault("message_count", session_meta.get("message_count"))

    content_keys = DEFAULT_SESSION_ENVELOPE if envelope is None else envelope
    for key, default in content_keys.items():
        out.setdefault(key, copy.deepcopy(default))

    return out
