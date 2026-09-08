from __future__ import annotations

import copy
import json
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Two compaction layers coexist deliberately:
# - SDK-native (``core/session/compaction.py``): server-side trimming on
#   Responses-capable backends; opaque, per-turn, no history artifact.
# - Checkpoint compaction (this module, below): explicit model-visible
#   summary + resume protocol appended with the ``context_summary``
#   marker; backend-agnostic, and the recovery step the reactive
#   (on-context-error) hook drives — LiteLLM and other non-Responses
#   providers have no SDK-side compaction path.


def is_context_length_exceeded(err: Exception) -> bool:
    """Heuristic detector for context-window-exceeded errors.

    Covers OpenAI's ``code: context_length_exceeded`` shape, LiteLLM's
    ``ContextWindowExceededError`` class name, LiteLLM proxy errors that bury
    the real reason inside ``body.error.message``, and bare-message variants.
    """
    if err.__class__.__name__ == "ContextWindowExceededError":
        return True
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error")
        if isinstance(error_obj, dict):
            if error_obj.get("code") == "context_length_exceeded":
                return True
            error_message = str(error_obj.get("message", "")).lower()
            if "context_length_exceeded" in error_message:
                return True
            if "context window" in error_message and "exceed" in error_message:
                return True
    code = getattr(err, "code", None)
    if code == "context_length_exceeded":
        return True
    msg = str(err).lower()
    if "context window" in msg and "exceed" in msg:
        return True
    if "maximum context length" in msg:
        return True
    return False


def extract_transcript(history: list[dict]) -> str:
    parts: list[str] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        parts.append(json.dumps(item, ensure_ascii=False, default=str))
    return "\n".join(parts)


def extract_user_messages(history: list[dict]) -> list[str]:
    messages: list[str] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content:
            messages.append(content)
    return messages


def format_user_messages_block(messages: list[str]) -> str:
    if not messages:
        return "(none)"
    parts: list[str] = []
    last_idx = len(messages) - 1
    for idx, msg in enumerate(messages):
        label = f"#{idx + 1}"
        if idx == last_idx:
            label += " (LAST)"
        parts.append(f"- {label}:\n```\n{msg}\n```")
    return "\n".join(parts)


def extract_tool_events(history: list[dict]) -> list[dict]:
    events: list[dict] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role in {"tool", "function"}:
            events.append(item)
            continue
        tool_calls = item.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            events.append({"role": role, "tool_calls": tool_calls})
            continue
        omni = item.get("omniagents")
        if isinstance(omni, dict) and any(
            key in omni for key in ("tool_name", "tool_call", "tool_result")
        ):
            events.append(item)
    return events


def _trim_text_head(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[TRUNCATED {len(text) - max_chars} chars]..."


def _trim_text_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"...[TRUNCATED {len(text) - max_chars} chars]...\n" + text[-max_chars:]


def _extract_tool_name_from_event(event: dict) -> str | None:
    name = event.get("name")
    if isinstance(name, str) and name:
        return name
    tool_name = event.get("tool_name")
    if isinstance(tool_name, str) and tool_name:
        return tool_name
    tool_calls = event.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        first = tool_calls[0]
        if isinstance(first, dict):
            fn = first.get("function")
            if isinstance(fn, dict):
                fn_name = fn.get("name")
                if isinstance(fn_name, str) and fn_name:
                    return fn_name
    omni = event.get("omniagents")
    if isinstance(omni, dict):
        omni_name = omni.get("tool_name") or omni.get("name")
        if isinstance(omni_name, str) and omni_name:
            return omni_name
    return None


def _trim_large_strings(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, str):
        return _trim_text_head(value, max_chars)
    if isinstance(value, list):
        return [_trim_large_strings(v, max_chars=max_chars) for v in value]
    if isinstance(value, dict):
        return {
            key: _trim_large_strings(value_item, max_chars=max_chars)
            for key, value_item in value.items()
        }
    return value


def _trim_exec_bash_fields(value: Any, *, stdout_chars: int, stderr_chars: int) -> Any:
    if isinstance(value, list):
        return [
            _trim_exec_bash_fields(
                v, stdout_chars=stdout_chars, stderr_chars=stderr_chars
            )
            for v in value
        ]
    if isinstance(value, dict):
        out: dict = {}
        for key, value_item in value.items():
            if isinstance(value_item, str) and key.lower() in {"stdout", "out"}:
                out[key] = _trim_text_tail(value_item, stdout_chars)
                continue
            if isinstance(value_item, str) and key.lower() in {"stderr", "err"}:
                out[key] = _trim_text_tail(value_item, stderr_chars)
                continue
            out[key] = _trim_exec_bash_fields(
                value_item, stdout_chars=stdout_chars, stderr_chars=stderr_chars
            )
        return out
    return value


def sanitize_tool_event(event: dict) -> dict:
    tool_name = _extract_tool_name_from_event(event)
    try:
        event_copy = copy.deepcopy(event)
    except Exception:
        event_copy = {"event": str(event)}

    if tool_name in {"read_file", "convert_to_markdown"}:
        safe = event_copy
    elif tool_name == "execute_bash":
        safe = _trim_exec_bash_fields(event_copy, stdout_chars=2000, stderr_chars=4000)
        safe = _trim_large_strings(safe, max_chars=2000)
    else:
        safe = _trim_large_strings(event_copy, max_chars=2000)

    try:
        rendered = json.dumps(safe, ensure_ascii=False, default=str)
    except Exception:
        safe = {"tool_name": tool_name or "UNKNOWN", "event": str(event_copy)}
        rendered = json.dumps(safe, ensure_ascii=False, default=str)

    max_event_chars = 8000
    if len(rendered) <= max_event_chars:
        return safe

    minimal = {
        "tool_name": tool_name or "UNKNOWN",
        "truncated": True,
        "original_chars": len(rendered),
    }
    if isinstance(safe, dict):
        for key in ("command", "cmd", "args", "exit_code", "returncode"):
            val = safe.get(key)
            if val is not None:
                minimal[key] = val
    minimal["snippet_tail"] = _trim_text_tail(rendered, max_event_chars)
    return minimal


def format_tool_events_block(events: list[dict], *, max_items: int = 5) -> str:
    if not events:
        return "(none)"
    tail = events[-max_items:]
    parts: list[str] = []
    for idx, event in enumerate(tail, start=max(1, len(events) - len(tail) + 1)):
        safe_event = sanitize_tool_event(event)
        parts.append(
            f"- #{idx}:\n```json\n{json.dumps(safe_event, ensure_ascii=False, default=str)}\n```"
        )
    return "\n".join(parts)


def extract_file_paths_from_tool_event(event: dict) -> list[str]:
    tool_name = _extract_tool_name_from_event(event) or ""
    paths: list[str] = []

    def add_path(value: Any) -> None:
        if isinstance(value, str) and value:
            paths.append(value)

    if tool_name in {"read_file", "apply_patch", "write_file"}:
        for key in ("file_path", "path"):
            add_path(event.get(key))
        tool_calls = event.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function")
                if not isinstance(fn, dict):
                    continue
                args = fn.get("arguments")
                if isinstance(args, dict):
                    for key in ("file_path", "path"):
                        add_path(args.get(key))
                elif isinstance(args, str):
                    try:
                        args_obj = json.loads(args)
                    except Exception:
                        args_obj = None
                    if isinstance(args_obj, dict):
                        for key in ("file_path", "path"):
                            add_path(args_obj.get(key))

    if tool_name == "list_directory":
        add_path(event.get("path"))

    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def build_resume_protocol_block(*, history: list[dict], tool_events: list[dict]) -> str:
    user_messages = extract_user_messages(history)
    has_user_intent = bool(user_messages and user_messages[-1].strip())
    has_tool_activity = bool(tool_events)
    active = has_user_intent and (has_tool_activity or len(user_messages) > 1)

    touched: list[str] = []
    for event in tool_events[-5:]:
        touched.extend(extract_file_paths_from_tool_event(event))

    touched_unique: list[str] = []
    seen: set[str] = set()
    for path in touched:
        if path in seen:
            continue
        seen.add(path)
        touched_unique.append(path)

    if not active:
        return (
            "Resume Protocol:\n"
            "- No active task detected (manual compaction or idle).\n"
            "- Ask the user what to do next.\n"
        )

    if touched_unique:
        files_block = "\n".join(f"- {path}" for path in touched_unique)
    else:
        files_block = "- UNKNOWN (no file paths detected; re-scan or ask user)"

    return (
        "Resume Protocol (do this first):\n"
        "1) Re-read the relevant files to confirm current state:\n"
        f"{files_block}\n"
        "2) Review the last 5 tool events and reproduce any failing command if applicable.\n"
        "3) Continue with the pending tasks / next steps from the summary.\n"
    )


# ---------------------------------------------------------------------------
# Checkpoint compaction
#
# The SDK-native path (``core/session/compaction.py``) trims context
# server-side on Responses-capable backends and leaves no artifact. This
# path is its complement: an explicit, model-visible checkpoint — an
# agent-written summary + resume protocol appended to session history
# with the ``context_summary`` marker — that works on any backend and is
# the recovery step reactive (on-context-error) compaction relies on.
#
# Like recap, the *summarizer* resolves by convention: a project that
# ships an agent named ``CONVENTIONAL_COMPACTION_AGENT`` (``compactor``)
# gets a summarizer that runs it one-shot under the agent's own
# instructions on the utility-run model resolution — no install call
# needed; the checkpoint-summary prompt is product content
# (``omniagents new`` scaffolds a starter ``agents/compactor/``).
# ``set_compaction_summarizer`` remains the override seam (an explicit
# install always wins). The builtin ``compact`` capability pack exposes
# ``/compact`` over this module; the orchestration below is generic.
# ---------------------------------------------------------------------------

CHECKPOINT_KIND = "context_summary"

#: Project-agent key the conventional compaction summarizer runs.
CONVENTIONAL_COMPACTION_AGENT = "compactor"

# The checkpoint ENVELOPE is orchestration framing (the text the framework
# wraps around the summarizer's output in the appended history item), not
# an agent's system prompt — it stays framework-owned.
DEFAULT_CHECKPOINT_ENVELOPE = (
    "Another language model started to solve this problem and produced a "
    "summary of its thinking process. This message is a checkpoint to help "
    "you continue the work without duplicating effort. "
)


# async (prompt: str, *, service, session) -> str
CompactionSummarizer = Callable[..., Awaitable[str]]

_SUMMARIZER: Optional[CompactionSummarizer] = None


def set_compaction_summarizer(summarizer: Optional[CompactionSummarizer]) -> None:
    """Install an explicit compaction summarizer (wins over the convention).

    The callable receives ``(prompt, *, service, session)`` and returns
    the summary body. Most products don't need this: shipping a
    ``compactor`` project agent activates the conventional summarizer
    (see :func:`compaction_summarizer`). Installing ``None`` restores
    the conventional fallback.
    """
    global _SUMMARIZER
    _SUMMARIZER = summarizer


def compaction_summarizer() -> Optional[CompactionSummarizer]:
    """The active checkpoint-compaction summarizer.

    An explicitly-installed summarizer wins; otherwise the conventional
    one — a one-shot run of the project's ``compactor`` agent under its
    own instructions
    (:func:`omniagents.core.runtime.conventional_service_summarizer`) —
    when the installed project ships that agent. ``None`` when neither
    exists.
    """
    if _SUMMARIZER is not None:
        return _SUMMARIZER
    from omniagents.core.runtime.ephemeral import conventional_service_summarizer

    return conventional_service_summarizer(CONVENTIONAL_COMPACTION_AGENT)


def is_checkpoint_item(item: Any) -> bool:
    """True when *item* is a checkpoint summary marker in session history."""
    return (
        isinstance(item, dict)
        and item.get("role") == "assistant"
        and isinstance(item.get("omniagents"), dict)
        and item["omniagents"].get("kind") == CHECKPOINT_KIND
    )


def history_since_last_checkpoint(
    history: list[dict], *, include_checkpoint: bool = False
) -> list[dict]:
    """Return history items after the most recent checkpoint marker.

    With ``include_checkpoint=True`` the checkpoint item itself is kept
    (the run path wants the model to still see the summary); without it,
    the window starts just after (the summarizer must not re-summarize
    the previous summary).
    """
    if not history:
        return []
    last_idx: int | None = None
    for idx in range(len(history) - 1, -1, -1):
        if is_checkpoint_item(history[idx]):
            last_idx = idx
            break
    if last_idx is None:
        return list(history)
    start = last_idx if include_checkpoint else last_idx + 1
    return list(history[start:])


async def summarize_with_window_shrink(
    window: list[dict],
    *,
    build_prompt: Any,
    summarize: Any,
) -> str:
    """Run ``summarize(build_prompt(window))``, shrinking on context errors.

    When the summarization call itself exceeds the model's context window,
    drop the oldest HALF of the window and retry — the recovery loop for
    checkpoint compaction, session summarization, and similar
    transcript-sized prompts. Halving (not one-at-a-time) matters: a
    transcript far over budget — a resident day session ~1900 items past
    the window — needed ~1900 doomed API calls under linear shrinking and
    its process was killed long before converging; halving converges in
    O(log n) attempts, and the newest tail is the context worth keeping
    anyway. Non-context errors propagate; an empty window propagates the
    last error too.
    """
    working = list(window)
    while True:
        prompt = build_prompt(working)
        try:
            return await summarize(prompt)
        except Exception as err:
            if is_context_length_exceeded(err) and working:
                working = working[max(1, len(working) // 2) :]
                continue
            raise


def build_checkpoint_prompt(full_history: list[dict], window: list[dict]) -> str:
    """The summarizer input: all user messages + the window transcript."""
    user_messages_block = format_user_messages_block(
        extract_user_messages(full_history)
    )
    transcript = extract_transcript(window)
    return (
        "All user messages (verbatim; last is highlighted):\n"
        f"{user_messages_block}\n\n"
        "Conversation transcript since last checkpoint (raw items; includes tool calls/outputs):\n"
        f"{transcript}"
    )


def build_checkpoint_summary_text(
    *,
    history: list[dict],
    summary_body: str,
    envelope: str | None = None,
) -> str:
    """Assemble the checkpoint item content: envelope + resume protocol +
    verbatim user messages + recent tool events + the agent summary."""
    user_messages_block = format_user_messages_block(extract_user_messages(history))
    tool_events = extract_tool_events(history)
    tool_events_block = format_tool_events_block(tool_events, max_items=5)
    resume_protocol = build_resume_protocol_block(
        history=history, tool_events=tool_events
    )
    return (
        f"{envelope if envelope is not None else DEFAULT_CHECKPOINT_ENVELOPE}"
        f"{resume_protocol}\n"
        "Here are all user messages (verbatim; last is highlighted):\n"
        f"{user_messages_block}\n\n"
        "Here are the last 5 tool call/result events (verbatim):\n"
        f"{tool_events_block}\n\n"
        "Here is the summary produced by the other language model:\n\n"
        f"{summary_body}"
    )


async def run_checkpoint_compaction(
    session: Any,
    summarize: Any,
    *,
    envelope: str | None = None,
) -> dict:
    """Compact *session* by appending a checkpoint summary marker.

    ``summarize`` is an async callable ``(prompt: str) -> str`` — typically
    a product's compactor agent run via ``run_agent_once`` with its own
    model resolution. Returns the appended history item.
    """
    full_history = list(getattr(session, "history", []) or [])
    window = history_since_last_checkpoint(full_history)

    summary_body = await summarize_with_window_shrink(
        window,
        build_prompt=lambda w: build_checkpoint_prompt(full_history, w),
        summarize=summarize,
    )
    summary_body = summary_body or "(no summary produced)"

    summary_item = {
        "role": "assistant",
        "content": build_checkpoint_summary_text(
            history=full_history, summary_body=summary_body, envelope=envelope
        ),
        "omniagents": {"kind": CHECKPOINT_KIND, "version": 1},
    }
    recorder = _conversation_recorder(session)
    session.append_message(summary_item)
    _record_checkpoint_item(session, recorder, summary_item)
    return summary_item


def _conversation_recorder(session: Any) -> Any:
    """Materialize the canonical thread BEFORE the checkpoint row is
    appended to history — otherwise the creation-time projection sees the
    checkpoint and records it, and the explicit call below records it
    again."""
    try:
        from omniagents.core.conversation.recorder import ensure_conversation_thread

        return ensure_conversation_thread(session)
    except Exception:
        logger.exception("conversation thread not materialized for compaction")
        return None


def _record_checkpoint_item(session: Any, recorder: Any, summary_item: dict) -> None:
    """Promote the checkpoint into a canonical compaction Item.

    Compaction writes straight to history without broadcasting, so it never
    reaches the notification chokepoint the domain recorder listens on. The
    recorded item also moves the thread's compaction floor, which is how a
    client knows which items are still in the model's live context.
    Best-effort: a domain-store fault must not fail a compaction.
    """
    if recorder is None:
        return
    try:
        recorder.record_compaction_checkpoint(
            session.id,
            summary=str(summary_item.get("content") or ""),
            kind=CHECKPOINT_KIND,
            version=1,
            run_id=getattr(session, "active_run_id", None),
        )
    except Exception:
        logger.exception("compaction checkpoint not recorded as a domain item")


def make_auto_compact_error_hook(*, state_key: str, command: str = "compact"):
    """Build a reactive-compaction ``error`` runtime hook.

    On a context-length-exceeded run error: record the pressure flags
    under ``session.variables[state_key]``, run the *command* server
    function (the product's checkpoint-compaction command), and ask the
    bridge to retry the run. Products register the result with
    ``@runtime_hook("error")`` (or assign it to a decorated name).

    Opt-outs honored via ``session.variables[state_key]``:
    ``auto_compact: False`` disables the compaction+retry (flags are
    still recorded); ``auto_compact_in_progress`` guards re-entry.
    """

    async def on_error(
        *,
        service: Any,
        session: Any,
        run_id: str,
        params: dict,
        event: str,
    ) -> dict | None:
        err = params.get("error")
        if err is None:
            return None

        if params.get("is_context_length_exceeded") is not True:
            return None

        vars_obj = getattr(session, "variables", None)
        if not isinstance(vars_obj, dict):
            vars_obj = {}
            try:
                session.variables = vars_obj
            except Exception:
                return None

        state = vars_obj.get(state_key)
        if not isinstance(state, dict):
            state = {}
            vars_obj[state_key] = state

        state["needs_context_compression"] = True
        state["context_error_run_id"] = run_id
        state["context_error"] = "context_length_exceeded"

        if state.get("auto_compact") is False:
            return None

        if state.get("auto_compact_in_progress"):
            return None

        state["auto_compact_in_progress"] = True
        try:
            await service.server_call(command, args={}, session_id=session.id)
        finally:
            state["auto_compact_in_progress"] = False

        return {"retry": True}

    return on_error


def make_auto_compact_token_hook(
    *, state_key: str, command: str = "compact", threshold: float = 0.9
):
    """Build a proactive-compaction ``token`` runtime hook.

    Token events only record pressure under ``session.variables[state_key]``.
    The bridge's model-input filter invokes this hook at a safe boundary,
    after tool outputs are saved, to run the *command* server function.
    Products register the result with ``@runtime_hook("token")`` (or assign it to a
    decorated name).

    Opt-outs honored via ``session.variables[state_key]``:
    ``auto_compact: False`` disables the compaction (flags are still
    recorded); ``auto_compact_in_progress`` guards re-entry.
    """

    async def on_token(
        *,
        service: Any,
        session: Any,
        run_id: str,
        params: dict,
        event: str,
    ) -> None:
        max_input_tokens = params.get("max_input_tokens")
        usage = params.get("totals") or params.get("delta") or {}
        total_tokens = usage.get("total_tokens") or 0
        if not max_input_tokens:
            return

        utilization = float(total_tokens) / float(max_input_tokens)
        if utilization < threshold:
            return

        vars_obj = getattr(session, "variables", None)
        if not isinstance(vars_obj, dict):
            vars_obj = {}
            try:
                session.variables = vars_obj
            except Exception:
                return

        state = vars_obj.get(state_key)
        if not isinstance(state, dict):
            state = {}
            vars_obj[state_key] = state

        state["needs_context_compression"] = True
        state["context_utilization"] = utilization
        state["context_utilization_run_id"] = run_id

        # Token notifications are consumed before the SDK has finished tools.
        # Only the model-input boundary may execute proactive compaction.
        if not params.get("compaction_safe_boundary"):
            return

        if state.get("auto_compact") is False:
            return

        if state.get("auto_compact_in_progress"):
            return

        state["auto_compact_in_progress"] = True
        try:
            await service.server_call(command, args={}, session_id=session.id)
        finally:
            state["auto_compact_in_progress"] = False

    setattr(on_token, "compaction_policy", (state_key, command, threshold))
    return on_token
