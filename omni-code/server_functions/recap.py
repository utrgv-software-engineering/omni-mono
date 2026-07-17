"""``/recap`` — produce a short, human-friendly recap of the session and
push it to the client for display (notifications-style panel).

Mechanism mirrors ``/compact``: flatten the session transcript, run a
throwaway ``recapper`` agent (``tools=[]``, ``max_turns=1``) over it, and
capture the text. Unlike compact, it does NOT mutate session history — a
recap is a read-only side view. The result is broadcast via
``request_client_function('ui.recap', ...)`` so display is decoupled from
the call: a future programmatic trigger (e.g. auto-recap on long idle or
on ticket completion) surfaces in the same panel by calling
``generate_and_broadcast_recap`` directly.

Reuses compact's model-resolution + provider-build helpers rather than
duplicating them — they're stable and this keeps the two paths in sync.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from omniagents import server_function
from omniagents.core.runtime.compaction import (
    extract_transcript,
    extract_user_messages,
    format_user_messages_block,
)
from omniagents.core.session.manager import Session

# Shared with /compact — model resolution stays on the exact same
# runtime path; the provider wiring lives in omni_code.utility_runs.
from server_functions.compact import _resolve_compaction_runtime
from omniagents.core.runtime import run_agent_once

from omni_code.utility_runs import (
    _build_model_provider,
    ephemeral_workspace_root,
)


async def _run_recap_agent(
    *,
    model: str | None,
    runtime: dict[str, Any] | None,
    prompt: str,
    session: Session | None,
) -> str:
    agent_yaml = (
        Path(__file__).resolve().parent.parent / "omni_agents" / "recapper" / "agent.yml"
    )
    model_provider, env_overrides = _build_model_provider(runtime or {})
    return await run_agent_once(
        agent_yaml,
        prompt,
        model=model,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        workspace_root=ephemeral_workspace_root(session),
    )


def _normalize_model_name(model_ref: str | None, runtime: dict[str, Any]) -> str | None:
    """Mirror compact's model-name normalization: strip a non-openai/litellm
    provider prefix so the bare model id reaches the provider."""
    model_name = runtime.get("model") if isinstance(runtime, dict) else None
    if (not isinstance(model_name, str) or not model_name.strip()) and isinstance(
        model_ref, str
    ):
        model_name = model_ref.strip()
    if (
        isinstance(model_name, str)
        and "/" in model_name
        and runtime.get("provider") != "litellm"
    ):
        prefix = model_name.split("/", 1)[0]
        if prefix not in {"openai", "litellm"}:
            model_name = model_name.split("/", 1)[1]
    return model_name


async def generate_recap(service: Any, session: Session) -> str:
    """Build a transcript and run the recapper agent over it. Pure: returns
    the recap text, no history mutation, no UI side effects. The programmatic
    core — callers that just want the text use this directly."""
    history = list(getattr(session, "history", []) or [])
    if not history:
        return "Nothing to recap yet — this session hasn't started."

    transcript = extract_transcript(history)
    user_messages = extract_user_messages(history)
    user_messages_block = format_user_messages_block(user_messages)
    prompt = (
        "All user messages (verbatim; last is highlighted):\n"
        f"{user_messages_block}\n\n"
        "Conversation transcript (raw items; includes tool calls/outputs):\n"
        f"{transcript}"
    )

    model_ref, runtime = _resolve_compaction_runtime(service, session)
    model_name = _normalize_model_name(model_ref, runtime)

    text = await _run_recap_agent(
        model=model_name, runtime=runtime, prompt=prompt, session=session
    )
    return (text or "").strip() or "(no recap produced)"


async def _broadcast_recap(service: Any, session: Session, text: str) -> None:
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


async def generate_and_broadcast_recap(service: Any, session: Session) -> str:
    """Programmatic entry point: produce the recap AND push it to the client
    panel. Use this from non-interactive triggers (idle hooks, ticket
    completion, etc.) where there's no slash-command return value to ride."""
    text = await generate_recap(service, session)
    await _broadcast_recap(service, session, text)
    return text


@server_function(
    description="Recap the session so far in <=400 words (shown in a panel)",
    strict=True,
    name_override="recap",
)
async def recap(service: Any, session: Session) -> dict:
    await service.set_client_status(
        "Recapping...",
        session=session,
        run_id=getattr(session, "active_run_id", None),
        show_spinner=True,
    )
    try:
        text = await generate_and_broadcast_recap(service, session)
    finally:
        await service.set_client_status(
            "",
            session=session,
            run_id=getattr(session, "active_run_id", None),
            show_spinner=False,
        )
    # Return the text too, so the interactive slash path can render it even
    # if the broadcast was dropped. The client routes recap's result into
    # the panel rather than dumping it in chat.
    return {"ok": True, "text": text}


RECAP_SERVER_FUNCTIONS = [recap]
