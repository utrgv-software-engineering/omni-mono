from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
from pathlib import Path
from typing import Any

from agents import Runner
from omniagents import server_function
from omniagents.core.agents.builder import _default_build_agent
from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.runtime.compaction import (
    build_resume_protocol_block,
    extract_tool_events,
    extract_transcript,
    extract_user_messages,
    format_tool_events_block,
    format_user_messages_block,
    is_context_length_exceeded,
)
from omniagents.core.session.manager import Session
from openai import APIStatusError, BadRequestError


def history_since_last_summary(
    history: list[dict], *, include_summary: bool = False
) -> list[dict]:
    """Return history items after the most recent ``context_summary`` marker.

    Inlined from omniagents (removed in the SDK 0.16 migration); the
    ``omniagents.kind == "context_summary"`` marker is omni-code's own
    convention for indicating a summary checkpoint inside the session.
    """
    if not history:
        return []
    last_summary_idx: int | None = None
    for idx in range(len(history) - 1, -1, -1):
        item = history[idx]
        if (
            isinstance(item, dict)
            and item.get("role") == "assistant"
            and isinstance(item.get("omniagents"), dict)
            and item["omniagents"].get("kind") == "context_summary"
        ):
            last_summary_idx = idx
            break
    if last_summary_idx is None:
        return list(history)
    start = last_summary_idx if include_summary else last_summary_idx + 1
    return list(history[start:])


def _last_user_message(history: list[dict]) -> str | None:
    for item in reversed(history or []):
        if item.get("role") == "user" and isinstance(item.get("content"), str):
            content = item.get("content")
            if content:
                return content
    return None


def _is_context_length_exceeded(err: Exception) -> bool:
    if isinstance(err, (BadRequestError, APIStatusError)):
        return is_context_length_exceeded(err)
    return False


def _summarization_window(history: list[dict]) -> list[dict]:
    return history_since_last_summary(history, include_summary=False)


def _extract_transcript(history: list[dict]) -> str:
    return extract_transcript(history)


def _extract_user_messages(history: list[dict]) -> list[str]:
    return extract_user_messages(history)


def _format_user_messages_block(messages: list[str]) -> str:
    return format_user_messages_block(messages)


def _extract_tool_events(history: list[dict]) -> list[dict]:
    return extract_tool_events(history)


def _format_tool_events_block(events: list[dict], *, max_items: int = 5) -> str:
    return format_tool_events_block(events, max_items=max_items)


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
            k: _trim_large_strings(v, max_chars=max_chars) for k, v in value.items()
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
        for k, v in value.items():
            if isinstance(v, str) and k.lower() in {"stdout", "out"}:
                out[k] = _trim_text_tail(v, stdout_chars)
                continue
            if isinstance(v, str) and k.lower() in {"stderr", "err"}:
                out[k] = _trim_text_tail(v, stderr_chars)
                continue
            out[k] = _trim_exec_bash_fields(
                v, stdout_chars=stdout_chars, stderr_chars=stderr_chars
            )
        return out
    return value


def _sanitize_tool_event(event: dict) -> dict:
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


def _extract_file_paths_from_tool_event(event: dict) -> list[str]:
    tool_name = _extract_tool_name_from_event(event) or ""
    paths: list[str] = []

    def add_path(value: Any) -> None:
        if isinstance(value, str) and value:
            paths.append(value)

    if tool_name in {"read_file", "apply_patch", "write_file"}:
        for k in ("file_path", "path"):
            add_path(event.get(k))
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
                    for k in ("file_path", "path"):
                        add_path(args.get(k))
                elif isinstance(args, str):
                    try:
                        args_obj = json.loads(args)
                        if isinstance(args_obj, dict):
                            for k in ("file_path", "path"):
                                add_path(args_obj.get(k))
                    except Exception:
                        pass

    if tool_name == "list_directory":
        add_path(event.get("path"))

    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _resume_protocol_block(*, history: list[dict], tool_events: list[dict]) -> str:
    return build_resume_protocol_block(history=history, tool_events=tool_events)


def _extract_response_text(resp: Any) -> str:
    out = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        if getattr(item, "role", None) != "assistant":
            continue
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) in {"output_text", "text"}:
                text = getattr(c, "text", None)
                if isinstance(text, str) and text:
                    out.append(text)
    return "".join(out).strip()


@contextlib.contextmanager
def _temporary_environ(overrides: dict[str, str | None]):
    prior: dict[str, str | None] = {}
    for key, value in (overrides or {}).items():
        prior[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _litellm_env_overrides(
    *, model: str | None, api_key: str | None
) -> dict[str, str | None]:
    if not api_key:
        return {}

    model_name = (model or "").lower()
    if model_name.startswith("anthropic/") or model_name.startswith("claude"):
        return {"ANTHROPIC_API_KEY": api_key}
    if model_name.startswith("gemini/") or model_name.startswith("google/"):
        return {"GOOGLE_API_KEY": api_key}
    return {"OPENAI_API_KEY": api_key}


def _build_model_provider(
    runtime: dict[str, Any],
) -> tuple[Any | None, dict[str, str | None]]:
    provider = (runtime.get("provider") or "").strip().lower()
    model = runtime.get("model")
    api_key = runtime.get("api_key")
    base_url = runtime.get("base_url")

    if provider == "litellm":
        from agents.extensions.models.litellm_provider import LitellmProvider

        return LitellmProvider(), _litellm_env_overrides(model=model, api_key=api_key)

    if provider in {"openai", "openai-compatible", "azure"}:
        from openai import AsyncOpenAI
        from agents.models.openai_provider import OpenAIProvider

        resolved_base_url = base_url or "https://api.openai.com/v1"
        resolved_api_key = api_key
        if not resolved_api_key:
            if provider == "azure":
                resolved_api_key = os.getenv("AZURE_OPENAI_API_KEY")
            else:
                resolved_api_key = os.getenv("OPENAI_API_KEY")

        is_azure = resolved_base_url and (
            "openai.azure.com" in resolved_base_url
            or "services.ai.azure.com" in resolved_base_url
        )

        if is_azure:
            if not resolved_api_key:
                return None, {}
            client = AsyncOpenAI(
                api_key="dummy",
                base_url=resolved_base_url,
                default_headers={"api-key": resolved_api_key},
            )
            return OpenAIProvider(openai_client=client), {}

        if not resolved_api_key and resolved_base_url != "https://api.openai.com/v1":
            resolved_api_key = "dummy"

        if not resolved_api_key:
            return None, {}

        client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
        return OpenAIProvider(openai_client=client), {}

    return None, {}


def _resolve_compaction_runtime(
    service: Any, session: Session
) -> tuple[str | None, dict[str, Any]]:
    candidates: list[str] = []
    active_model = getattr(session, "active_model", None)
    if isinstance(active_model, str) and active_model.strip():
        candidates.append(active_model.strip())
    spec_model = getattr(getattr(service, "spec", None), "model_name", None)
    if isinstance(spec_model, str) and spec_model.strip():
        candidates.append(spec_model.strip())

    from omni_code.models import resolve_model_for_runtime

    for ref in candidates:
        runtime = resolve_model_for_runtime(ref)
        if runtime:
            return ref, runtime

    model_config = getattr(session, "model_config", None)
    if isinstance(model_config, dict) and model_config.get("model"):
        runtime = {
            "provider": (model_config.get("provider") or "").strip().lower(),
            "model": model_config.get("model"),
            "api_key": model_config.get("api_key"),
            "base_url": model_config.get("base_url"),
            "api_version": model_config.get("api_version"),
        }
        return candidates[0] if candidates else None, runtime

    return candidates[0] if candidates else None, {}


async def _run_compaction_agent(
    *,
    model: str | None,
    runtime: dict[str, Any] | None,
    prompt: str,
    session: Session | None,
) -> str:
    agent_yaml = (
        Path(__file__).resolve().parent.parent
        / "omni_agents"
        / "compactor"
        / "agent.yml"
    )
    spec = load_agent_spec_from_yaml(str(agent_yaml))
    if model:
        spec.model_name = model
    agent = await _default_build_agent(
        settings={}, mcp_servers=None, spec=spec, session=session
    )

    model_provider, env_overrides = _build_model_provider(runtime or {})

    from agents import RunConfig

    run_config = (
        RunConfig(model_provider=model_provider) if model_provider is not None else None
    )
    # Inherit workspace_root from the caller's session (the user's active
    # workspace) so that, if the compactor agent ever gains workspace
    # tools, they resolve paths against the right directory rather than
    # silently failing under strict mode. Today the compactor has tools=[]
    # so the value is unused; passing it is forward-compatible.
    workspace_root = None
    if session is not None:
        sess_ctx = getattr(session, "context", None)
        if isinstance(sess_ctx, dict):
            wr = sess_ctx.get("workspace_root")
            if isinstance(wr, str) and wr.strip():
                workspace_root = wr
    if not workspace_root:
        # Prefer the sandbox manifest root over omni serve's host cwd — they
        # differ for container backends, and this run bypasses the AgentService
        # resolver (direct Runner.run), so a host path here would not be
        # clamped. Matches fs_get_workspace_root's fallback chain.
        from omni_code.agent_workspace_state import get_manifest_root

        workspace_root = get_manifest_root() or os.getcwd()

    with _temporary_environ(env_overrides):
        result = await Runner.run(
            agent,
            prompt,
            context={"workspace_root": workspace_root},
            max_turns=1,
            run_config=run_config,
        )
        return result.final_output_as(str)


@server_function(
    description="Compact context by appending a summary marker",
    strict=True,
    name_override="compact",
)
async def compact(service: Any, session: Session) -> dict:
    await service.set_client_status(
        "Compacting...",
        session=session,
        run_id=getattr(session, "active_run_id", None),
        show_spinner=True,
    )
    try:
        full_history = list(getattr(session, "history", []) or [])
        window = _summarization_window(full_history)

        model_ref, runtime = _resolve_compaction_runtime(service, session)
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

        while True:
            transcript = _extract_transcript(window)
            user_messages = _extract_user_messages(full_history)
            user_messages_block = _format_user_messages_block(user_messages)
            prompt = (
                "All user messages (verbatim; last is highlighted):\n"
                f"{user_messages_block}\n\n"
                "Conversation transcript since last checkpoint (raw items; includes tool calls/outputs):\n"
                f"{transcript}"
            )

            try:
                summary_body = await _run_compaction_agent(
                    model=model_name,
                    runtime=runtime,
                    prompt=prompt,
                    session=session,
                )
                break
            except Exception as e:
                if _is_context_length_exceeded(e) and window:
                    window.pop(0)
                    continue
                raise

        summary_body = summary_body or "(no summary produced)"
        user_messages = _extract_user_messages(full_history)
        user_messages_block = _format_user_messages_block(user_messages)
        tool_events = _extract_tool_events(full_history)
        tool_events_block = _format_tool_events_block(tool_events, max_items=5)
        resume_protocol = _resume_protocol_block(
            history=full_history, tool_events=tool_events
        )
        summary_text = (
            "Another language model started to solve this problem and produced a summary of its thinking process. "
            "This message is a checkpoint to help you continue the work without duplicating effort. "
            f"{resume_protocol}\n"
            "Here are all user messages (verbatim; last is highlighted):\n"
            f"{user_messages_block}\n\n"
            "Here are the last 5 tool call/result events (verbatim):\n"
            f"{tool_events_block}\n\n"
            "Here is the summary produced by the other language model:\n\n"
            f"{summary_body}"
        )

        summary_item = {
            "role": "assistant",
            "content": summary_text,
            "omniagents": {"kind": "context_summary", "version": 1},
        }
        session.append_message(summary_item)
    finally:
        await service.set_client_status(
            "",
            session=session,
            run_id=getattr(session, "active_run_id", None),
            show_spinner=False,
        )

    return "Context compacted."
