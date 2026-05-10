from __future__ import annotations

import copy
import json
from typing import Any

# Note: ``history_since_last_summary`` was removed when SDK-native compaction
# (``OpenAIResponsesCompactionSession``) took over context-window management.
# ``is_context_length_exceeded`` was restored because the SDK has no
# equivalent classifier and the bridge still needs one to drive the
# hook-driven retry primitive (LiteLLM and other non-Responses providers
# have no SDK-side compaction recovery path).


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
