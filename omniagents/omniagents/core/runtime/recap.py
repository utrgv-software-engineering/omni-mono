"""Session recap: a short, human-friendly catch-up note.

``generate_recap`` flattens the session transcript, runs the installed
summarizer over it, and returns the text; ``generate_and_broadcast_recap``
additionally pushes it to the client via the ``ui.recap`` client function
(a notifications-style panel). Unlike checkpoint compaction, a recap is a
read-only side view — session history is never mutated.

Like compaction, the *summarizer* is product policy: products install one
with :func:`set_recap_summarizer` (typically a one-shot run of their
recapper agent on their resolved model). :data:`DEFAULT_RECAP_INSTRUCTIONS`
is a ready-made system prompt for products that don't want to write their
own. The builtin ``recap`` capability pack exposes ``/recap`` over this
module.

Lifted from omni-code.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from omniagents.core.runtime.compaction import (
    extract_transcript,
    extract_user_messages,
    format_user_messages_block,
)

__all__ = [
    "DEFAULT_RECAP_INSTRUCTIONS",
    "set_recap_summarizer",
    "recap_summarizer",
    "generate_recap",
    "generate_and_broadcast_recap",
]


DEFAULT_RECAP_INSTRUCTIONS = """\
You are writing a low-friction catch-up note for a user returning after stepping away from an agent session.

The input is a chronological transcript of the session, including tool calls and outputs where present. Use it to understand the current state, but do not retell the transcript.

Goal: help the user regain context in under 15 seconds.

Write 3–6 short bullets. Prioritize:
- What the user asked for.
- The current result, status, or blocker.
- What is still needed from the user, if anything.
- The next useful action.

Guidelines:
- Start with the current state, not the history.
- Keep it concise: usually 80–160 words.
- Use plain language with low cognitive overhead.
- Do not narrate every attempt, click, retry, command, tool call, or implementation detail.
- Mention failed attempts only as a short reason for a current blocker.
- Include file names, commands, or decisions only when they are essential for the user to know what to do next.
- No preamble ("Here is a recap..."). Start with the substance.
- If the session is short or nothing much happened, say so briefly rather than inventing detail.
"""


# async (prompt: str, *, service, session) -> str
RecapSummarizer = Callable[..., Awaitable[str]]

_SUMMARIZER: Optional[RecapSummarizer] = None


def set_recap_summarizer(summarizer: Optional[RecapSummarizer]) -> None:
    """Install the product's recap summarizer.

    The callable receives ``(prompt, *, service, session)`` and returns
    the recap text — typically a one-shot run of the product's recapper
    agent on its resolved model (see :data:`DEFAULT_RECAP_INSTRUCTIONS`
    for a ready-made prompt).
    """
    global _SUMMARIZER
    _SUMMARIZER = summarizer


def recap_summarizer() -> Optional[RecapSummarizer]:
    return _SUMMARIZER


def build_recap_prompt(history: list) -> str:
    """The transcript payload handed to the summarizer."""
    transcript = extract_transcript(history)
    user_messages = extract_user_messages(history)
    user_messages_block = format_user_messages_block(user_messages)
    return (
        "All user messages (verbatim; last is highlighted):\n"
        f"{user_messages_block}\n\n"
        "Conversation transcript (raw items; includes tool calls/outputs):\n"
        f"{transcript}"
    )


async def generate_recap(service: Any, session: Any) -> str:
    """Build a transcript and run the summarizer over it. Pure: returns
    the recap text, no history mutation, no UI side effects."""
    history = list(getattr(session, "history", []) or [])
    if not history:
        return "Nothing to recap yet — this session hasn't started."

    summarizer = recap_summarizer()
    if summarizer is None:
        return (
            "Recap is unavailable: no recap summarizer is installed "
            "(see omniagents.core.runtime.recap.set_recap_summarizer)."
        )

    prompt = build_recap_prompt(history)
    text = await summarizer(prompt, service=service, session=session)
    return (text or "").strip() or "(no recap produced)"


async def _broadcast_recap(service: Any, session: Any, text: str) -> None:
    if service is None or session is None:
        return
    try:
        await service.request_client_function(
            "ui.recap",
            {"text": text},
            session=session,
            expect_result=False,
        )
    except Exception:
        # Fire-and-forget; the slash path also returns the text so a missed
        # broadcast isn't fatal for the interactive case.
        pass


async def generate_and_broadcast_recap(service: Any, session: Any) -> str:
    """Programmatic entry point: produce the recap AND push it to the client
    panel. Use this from non-interactive triggers (idle hooks, ticket
    completion, etc.) where there's no slash-command return value to ride."""
    text = await generate_recap(service, session)
    await _broadcast_recap(service, session, text)
    return text
