from __future__ import annotations

import copy
import json
from typing import Any, Awaitable, Callable, Optional

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
# Like recap, the *summarizer* is product policy: products install one
# with ``set_compaction_summarizer`` (typically a one-shot run of their
# compactor agent on their resolved model).
# ``DEFAULT_CHECKPOINT_SUMMARY_INSTRUCTIONS`` is a ready-made system
# prompt for products that don't want to write their own. The builtin
# ``compact`` capability pack exposes ``/compact`` over this module; the
# orchestration below is generic.
# ---------------------------------------------------------------------------

CHECKPOINT_KIND = "context_summary"

DEFAULT_CHECKPOINT_ENVELOPE = (
    "Another language model started to solve this problem and produced a "
    "summary of its thinking process. This message is a checkpoint to help "
    "you continue the work without duplicating effort. "
)

# Lifted from omni-code's compactor agent.
DEFAULT_CHECKPOINT_SUMMARY_INSTRUCTIONS = """\
The conversation has reached its context length limit and will be handed off to a new agent that will have NO knowledge of the prior conversation.

Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.

This summary must be detailed enough that the new agent can continue autonomously from exactly where work left off, without duplicating efforts. It should capture technical details, patterns, architectural decisions, what was tried so far, what worked, and what did not work.

Before providing your final summary, include an <analysis> section.

The <analysis> section is a short coverage checklist only. It must NOT restate the summary in narrative form.

In <analysis>:
- Confirm you captured the user's explicit requests and constraints.
- Confirm you captured what was tried, what worked, and what failed.
- Confirm you captured exact file paths and key identifiers (function/class/section names) when present.
- Confirm you captured the last 5 tool calls and results (or explicitly state none were available).
- List any uncertainties or missing facts that the next agent must verify by re-reading files.

Your summary should include the following sections:
1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Key Sections: Enumerate specific files and sections examined, modified, or created. Pay special attention to the most recent messages and include full snippets where applicable and include a summary of why this file read or edit is important.
4. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
5. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
6. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and snippets where applicable.
7. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests without confirming with the user first.
8. If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:
<example>
<analysis>
[Coverage checklist only; no narrative duplication of <summary>.]
</analysis>
<summary>
1. Primary Request and Intent:
   [Detailed description]
2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]
3. Files and Key Sections:
   - [File Name 1]
     - [Summary of why this file is important]
     - [Summary of the changes made to this file, if any]
     - [Important Snippet]
   - [File Name 2]
     - [Important Snippet]
   - [...]
4. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]
5. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]
6. Current Work:
   [Precise description of current work]
7. Optional Next Step:
   [Optional Next step to take]
</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.
"""


# async (prompt: str, *, service, session) -> str
CompactionSummarizer = Callable[..., Awaitable[str]]

_SUMMARIZER: Optional[CompactionSummarizer] = None


def set_compaction_summarizer(summarizer: Optional[CompactionSummarizer]) -> None:
    """Install the product's checkpoint-compaction summarizer.

    The callable receives ``(prompt, *, service, session)`` and returns
    the summary body — typically a one-shot run of the product's
    compactor agent on its resolved model (see
    :data:`DEFAULT_CHECKPOINT_SUMMARY_INSTRUCTIONS` for a ready-made
    prompt).
    """
    global _SUMMARIZER
    _SUMMARIZER = summarizer


def compaction_summarizer() -> Optional[CompactionSummarizer]:
    return _SUMMARIZER


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
    drop the oldest window item and retry — the standard recovery loop for
    checkpoint compaction, session summarization, and similar
    transcript-sized prompts. Non-context errors propagate; an empty
    window propagates the last error too.
    """
    working = list(window)
    while True:
        prompt = build_prompt(working)
        try:
            return await summarize(prompt)
        except Exception as err:
            if is_context_length_exceeded(err) and working:
                working.pop(0)
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
    session.append_message(summary_item)
    return summary_item


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

    The proactive half of :func:`make_auto_compact_error_hook`: when a
    run's reported input-token utilization crosses *threshold*, record
    the pressure flags under ``session.variables[state_key]`` and run
    the *command* server function (the product's checkpoint-compaction
    command) before the context actually overflows. Products register
    the result with ``@runtime_hook("token")`` (or assign it to a
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
        delta = params.get("delta") or {}
        total_tokens = delta.get("total_tokens") or 0
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

        if state.get("auto_compact") is False:
            return

        if state.get("auto_compact_in_progress"):
            return

        state["auto_compact_in_progress"] = True
        try:
            await service.server_call(command, args={}, session_id=session.id)
        finally:
            state["auto_compact_in_progress"] = False

    return on_token
