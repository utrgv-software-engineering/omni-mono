from __future__ import annotations

import os
import asyncio
import logging
from typing import AsyncGenerator, Optional, Any

from agents import RunConfig, ItemHelpers
from openai.types.responses import ResponseCompletedEvent
from omniagents.core.debug import Debug
from omniagents.core.runtime.compaction import (
    history_since_last_summary,
    is_context_length_exceeded,
)
from omniagents.core.tools.artifacts import (
    reset_artifact_callback,
    set_artifact_callback,
)

# Setup logging
logger = logging.getLogger("omniagents.runtime")

# Configure file logging if requested (same as service.py)
LOG_FILE = os.getenv("OMNIAGENTS_SERVER_LOG_FILE")
if LOG_FILE:
    from pathlib import Path

    logger.setLevel(logging.DEBUG)
    log_path = Path(os.path.expanduser(LOG_FILE))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] [runtime] %(levelname)s: %(message)s")
    )
    logger.addHandler(file_handler)

# ---------------------------------------------------------------------
# Helper: best-effort notify that swallows disconnect errors so the
#         streaming task can keep running even when the WebSocket is
#         already closed.  Returns True if the payload was delivered.
# ---------------------------------------------------------------------


async def _safe_notify(channel, method: str, payload: dict) -> bool:  # noqa: D401
    """Try to send *payload* via *channel*; return False if socket is gone."""
    try:
        await channel.async_call(method, args=payload)
        return True
    except Exception:
        # Most commonly RuntimeError or WebSocketDisconnect from Starlette
        return False


# ---------------------------------------------------------------------
# Broadcast helper: send to every live channel attached to the Session.
# Returns True if at least one channel received the message.
# ---------------------------------------------------------------------


async def _broadcast(session, method: str, payload: dict) -> bool:  # noqa: D401
    if not session.channels:
        return False

    if method == "client_request":
        func = (payload or {}).get("function") if isinstance(payload, dict) else None
        if func == "ui.set_status":
            session.active_run_set_status_payload = dict(payload)

    delivered_any = False
    dead_channels = []
    for ch in list(session.channels):
        ok = await _safe_notify(ch, method, payload)
        if ok:
            delivered_any = True
        else:
            dead_channels.append(ch)

    # Remove channels that are no longer alive
    for ch in dead_channels:
        try:
            session.channels.remove(ch)
        except ValueError:
            pass

    return delivered_any


# Event mapping helper ----------------------------------------------------


def _stream_event_payload(item, run_id: str, *, session_id: str):
    """Map agent stream *item* into a notification name + payload.

    Returns (method_name, payload) or (None, None) if the item shouldn't
    be forwarded to the client.
    """
    if item.type == "tool_call_item":
        inp = item.to_input_item() or {}
        name = inp.get("name", "<tool>")
        call_id = inp.get("call_id")
        return "tool_called", {
            "run_id": run_id,
            "session_id": session_id,
            "tool": name,
            "input": inp.get("arguments", ""),
            **({"call_id": call_id} if call_id else {}),
        }
    elif item.type == "tool_call_output_item":
        # Extract call_id if present on the underlying raw_item / input item
        call_id = None
        try:
            out_dict = item.to_input_item() if hasattr(item, "to_input_item") else {}
            if isinstance(out_dict, dict):
                call_id = out_dict.get("call_id")
        except Exception:
            pass

        # Extract metadata from RichToolOutput if present
        output = getattr(item, "output", "")
        ui_metadata = {}

        # Check if output is a RichToolOutput instance
        from omniagents.core.tools import RichToolOutput

        if isinstance(output, RichToolOutput):
            ui_metadata = output.ui_metadata
            # String conversion will use __str__ which returns only llm_output

        payload = {
            "run_id": run_id,
            "session_id": session_id,
            "tool": getattr(item, "name", "<tool>"),
            "output": str(output),  # This calls __str__ for RichToolOutput
            **({"call_id": call_id} if call_id else {}),
        }

        # Only include metadata if it's not empty
        if ui_metadata:
            payload["metadata"] = ui_metadata

        return "tool_result", payload
    elif item.type == "message_output_item":
        text = ItemHelpers.text_message_output(item)
        return "message_output", {
            "run_id": run_id,
            "session_id": session_id,
            "content": text,
            "is_final": False,
        }
    return None, None


def _apply_compaction_window(history: list[dict]) -> list[dict]:
    return history_since_last_summary(history, include_summary=True)


def _is_context_length_exceeded(err: Exception) -> bool:
    return is_context_length_exceeded(err)


def _filter_history_for_api(history: list[dict]) -> list[dict]:
    """Reorder history items to match OpenAI API requirements.

    During streaming, items arrive in this order:
      reasoning → function_call(s) → message → function_call_output(s)

    But OpenAI expects:
      reasoning → message → function_call(s) → function_call_output(s)

    This reordering ensures:
    - reasoning items are immediately followed by messages (not function_calls)
    - parallel function_calls are grouped together after the message
    - function_call items are properly paired with their outputs

    This prevents errors like:
    "Item 'rs_xxx' of type 'reasoning' was provided without its required following item."
    "No tool call found for function call output with call_id xxx"
    """
    reordered = []
    i = 0
    consumed_indices: set[int] = set()
    while i < len(history):
        if i in consumed_indices:
            i += 1
            continue
        item = history[i]
        item_type = item.get("type", "message")

        # Handle reasoning items - they need special treatment for parallel calls
        if item_type == "reasoning":
            reasoning = item
            j = i + 1

            # Collect all parallel function calls that follow the reasoning.
            # Calls and outputs may be interleaved (fc → fc_output → fc → fc_output)
            # or grouped (fc → fc → fc_output → fc_output).  Handle both by
            # skipping over outputs while scanning for calls.
            function_calls = []
            interleaved_outputs = []
            while j < len(history):
                next_item = history[j]
                next_type = next_item.get("type", "")
                if (
                    isinstance(next_type, str)
                    and next_type.endswith("_call")
                    and not next_type.endswith("_call_output")
                ):
                    function_calls.append(next_item)
                    j += 1
                elif isinstance(next_type, str) and next_type.endswith("_call_output"):
                    # Output interleaved between calls – collect and skip
                    interleaved_outputs.append((j, next_item))
                    j += 1
                else:
                    break

            # A second reasoning may sit between the last output and the
            # message (the model emitted reasoning before the preamble text).
            # Skip over it so we can still find the paired message.
            trailing_reasoning = None
            if (
                j < len(history)
                and history[j].get("type") == "reasoning"
                and j + 1 < len(history)
                and history[j + 1].get("type") == "message"
            ):
                trailing_reasoning = history[j]
                j += 1

            # Find the message that pairs with this reasoning
            message = None
            if j < len(history) and history[j].get("type") == "message":
                message = history[j]
                j += 1

            # Items may be persisted in message-before-calls order
            # (reasoning → message → function_call) instead of streaming order
            # (reasoning → function_call → message).  Scan for calls after the
            # message so both orderings are handled.
            if message and not function_calls:
                while j < len(history):
                    next_item = history[j]
                    next_type = next_item.get("type", "")
                    if (
                        isinstance(next_type, str)
                        and next_type.endswith("_call")
                        and not next_type.endswith("_call_output")
                    ):
                        function_calls.append(next_item)
                        j += 1
                    elif isinstance(next_type, str) and next_type.endswith(
                        "_call_output"
                    ):
                        interleaved_outputs.append((j, next_item))
                        j += 1
                    else:
                        break

            # Collect outputs for the function calls.
            # Start from interleaved outputs already gathered, then scan
            # forward for any remaining (the grouped layout).
            outputs = []
            call_ids = {fc.get("call_id") for fc in function_calls if fc.get("call_id")}

            unmatched_interleaved = []
            for idx, out_item in interleaved_outputs:
                out_cid = out_item.get("call_id")
                if out_cid in call_ids:
                    outputs.append(out_item)
                    consumed_indices.add(idx)
                    call_ids.discard(out_cid)
                else:
                    # Output doesn't belong to any call in this group —
                    # preserve it so it isn't silently dropped.
                    unmatched_interleaved.append(out_item)

            if call_ids:
                for k in range(j, len(history)):
                    next_item = history[k]
                    next_type = next_item.get("type", "")
                    if not (
                        isinstance(next_type, str)
                        and next_type.endswith("_call_output")
                    ):
                        continue
                    output_call_id = next_item.get("call_id")
                    if output_call_id in call_ids:
                        outputs.append(next_item)
                        consumed_indices.add(k)
                        call_ids.remove(output_call_id)
                        if not call_ids:
                            break

            # Emit any interleaved outputs that didn't match a call in this
            # group before the group itself, so they aren't silently lost.
            reordered.extend(unmatched_interleaved)

            if message:
                # Valid reasoning group: reorder to reasoning → message → function_calls → outputs
                reordered.append(reasoning)
                reordered.append(message)
                reordered.extend(function_calls)
                reordered.extend(outputs)
            elif function_calls:
                # Orphaned reasoning (no message): drop reasoning but preserve tool calls
                # Azure rejects reasoning without messages AND rejects synthetic messages,
                # but accepts function_calls without reasoning if they have new IDs.
                # Create synthetic function_calls with new IDs to preserve the tool call history.
                for fc in function_calls:
                    # Find corresponding output
                    fc_output = None
                    for output in outputs:
                        if output.get("call_id") == fc.get("call_id"):
                            fc_output = output
                            break

                    # Create synthetic IDs (not referencing dropped reasoning)
                    original_id = fc.get("id", "unknown")
                    synth_call_id = f"call_synth_{original_id[3:] if original_id.startswith('fc_') else original_id}"
                    synth_fc_id = f"fc_synth_{original_id[3:] if original_id.startswith('fc_') else original_id}"

                    # Synthetic function call
                    synth_fc = {
                        "type": "function_call",
                        "id": synth_fc_id,
                        "call_id": synth_call_id,
                        "name": fc.get("name"),
                        "arguments": fc.get("arguments"),
                        "status": fc.get("status", "completed"),
                    }
                    reordered.append(synth_fc)

                    # Synthetic output
                    if fc_output:
                        synth_output = {
                            "type": "function_call_output",
                            "call_id": synth_call_id,
                            "output": fc_output.get("output"),
                        }
                        reordered.append(synth_output)
            # else: Orphaned reasoning with no function calls - just drop it

            i = j  # Skip past all processed items
        # Check if this is a function_call NOT preceded by reasoning
        elif (
            isinstance(item_type, str)
            and item_type.endswith("_call")
            and not item_type.endswith("_call_output")
        ):
            # Only process if we didn't already handle it as part of a reasoning group
            # Check if previous item was reasoning (would have been handled above)
            if i > 0 and reordered and reordered[-1].get("type") != "reasoning":
                call_id = item.get("call_id")
                function_call = item

                # Look ahead for the message and output
                message = None
                output = None
                j = i + 1

                # Find the message that comes after this call
                if j < len(history) and history[j].get("type") == "message":
                    message = history[j]
                    j += 1

                # Find the output that matches this call_id
                if call_id:
                    for k in range(j, len(history)):
                        next_item = history[k]
                        if (
                            isinstance(next_item.get("type", ""), str)
                            and next_item.get("type", "").endswith("_call_output")
                            and next_item.get("call_id") == call_id
                        ):
                            output = next_item
                            consumed_indices.add(k)
                            break

                # Reorder: message → function_call → output
                if message:
                    reordered.append(message)
                reordered.append(function_call)
                if output:
                    reordered.append(output)

                i = j  # Skip past the items we just processed
            else:
                # Already handled as part of reasoning group, skip
                i += 1
        else:
            # Not a reasoning or function_call, keep as-is
            reordered.append(item)
            i += 1

    return reordered


_STATUS_STRIP_TYPES = frozenset(
    {
        "reasoning",
        "message",
        "function_call",
        "function_call_output",
    }
)


def _strip_internal_keys_for_api(items: list[dict]) -> list[dict]:
    # Build set of reasoning IDs present so we can detect orphaned messages
    reasoning_ids: set[str] = set()
    for item in items or []:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            rid = item.get("id")
            if rid:
                reasoning_ids.add(rid)

    cleaned: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied.pop("omniagents", None)
        # The API returns `phase` on assistant messages (gpt-5.3-codex+),
        # but non-OpenAI providers (Azure, litellm) reject it as unknown.
        # Safe to strip unconditionally — the SDK re-adds it when needed.
        copied.pop("phase", None)
        # The API returns `status` on output items (reasoning, message, etc.)
        # but may reject it when those items are sent back as input.
        # Strip it from types where it's output-only metadata.
        if copied.get("type") in _STATUS_STRIP_TYPES:
            copied.pop("status", None)
        # Keep reasoning items (the API requires them before their paired
        # message).  Preserve encrypted_content when present — the API
        # requires it to associate reasoning with subsequent function_calls.
        # Only strip status which is output-only metadata.
        if copied.get("type") == "reasoning":
            copied.pop("status", None)
            # Ensure summary exists (API needs at least an empty list)
            if "summary" not in copied:
                copied["summary"] = []
        # Strip id from orphaned message items whose reasoning was lost.
        # This happens when history was saved before the reasoning-strip
        # fix: the reasoning item was dropped but the message kept its id,
        # so the API rejects it with "message without required reasoning".
        # Removing the id makes the API treat it as a plain assistant turn.
        if copied.get("type") == "message":
            msg_id = copied.get("id", "")
            if isinstance(msg_id, str) and msg_id.startswith("msg_"):
                expected_reasoning_id = "rs_" + msg_id[4:]
                if expected_reasoning_id not in reasoning_ids:
                    copied.pop("id", None)
        cleaned.append(copied)
    return cleaned


def _strip_phase_from_model_input(data):
    """RunConfig.call_model_input_filter that strips ``phase`` from all input items.

    The OpenAI Responses API returns ``phase`` on assistant messages (used by
    gpt-5.3-codex+).  When the agents SDK re-serialises output items for the
    next model turn (tool-call loop), ``phase`` leaks into the request body.
    Non-OpenAI providers (Azure via litellm proxy) reject it as an unknown
    parameter.  Stripping here catches both session-history replay *and*
    intra-run tool-call loops.
    """
    cleaned: list = []
    for item in data.model_data.input:
        if isinstance(item, dict) and "phase" in item:
            item = {k: v for k, v in item.items() if k != "phase"}
        cleaned.append(item)
    data.model_data.input = cleaned
    return data.model_data


async def stream_agent(
    agent,
    prompt: str,
    session,
    rpc_channel,
    *,
    service: Any | None = None,
    max_turns: int = 20,
    context: Optional[Any] = None,
    max_input_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    include_user_message: bool = True,
    context_error_retry: bool = False,
    workflow_name: Optional[str] = None,
    group_id: Optional[str] = None,
    user_content: Optional[Any] = None,
    model_provider: Optional[Any] = None,
    model_label: Optional[str] = None,
) -> dict | None:  # noqa: D401
    """Drive agent.run_streamed and forward events to *rpc_channel*.

    ``rpc_channel`` is the :class:`RpcMethodsBase` instance attached to the
    WebSocket connection and exposes ``.async_call(method, args)`` for
    server-to-client notifications.
    """

    token = None
    if service is not None and session is not None:
        loop = asyncio.get_running_loop()

        async def _send_artifact(payload: dict, *, run_id: str | None) -> None:
            try:
                await asyncio.wait_for(
                    service.request_client_function(
                        "ui.add_artifact",
                        payload,
                        session=session,
                        run_id=run_id,
                        expect_result=False,
                    ),
                    timeout=2.0,
                )
            except Exception:
                pass

        def _artifact_callback(
            title: str,
            content: str,
            mode: str = "markdown",
            artifact_id: str | None = None,
        ) -> None:
            # Determine run_id if we're inside an asyncio task.  This will
            # fail with RuntimeError when called from a worker thread (sync
            # tools run via asyncio.to_thread), so default to None.
            run_id = None
            try:
                task = asyncio.current_task()
                if task is not None:
                    run_id = task.get_name()
            except RuntimeError:
                pass

            try:
                # Use run_coroutine_threadsafe because the callback may be
                # invoked from a worker thread (sync tools run via
                # asyncio.to_thread in the SDK's function_tool wrapper).
                asyncio.run_coroutine_threadsafe(
                    _send_artifact(
                        {
                            "title": title,
                            "content": content,
                            "mode": mode,
                            "artifact_id": artifact_id,
                        },
                        run_id=run_id,
                    ),
                    loop,
                )
            except Exception:
                pass

        token = set_artifact_callback(_artifact_callback)

    # ------------------------------------------------------------------
    # Build input preserving session history; persist **user** message so
    # future runs see it as part of the context as well.
    # ------------------------------------------------------------------
    if include_user_message:
        windowed_history = _apply_compaction_window(session.history)
        clean_history = _strip_internal_keys_for_api(
            _filter_history_for_api(windowed_history)
        )
        if user_content is not None:
            agent_input = clean_history + [{"role": "user", "content": user_content}]
            user_msg = {"role": "user", "content": user_content}
        else:
            agent_input = clean_history + [{"role": "user", "content": prompt}]
            user_msg = {"role": "user", "content": prompt}
        session.append_message(user_msg)
    else:
        agent_input = _strip_internal_keys_for_api(
            _filter_history_for_api(list(session.history))
        )

    Debug.log(
        f"[srv] ▶ start_run  session={session.id[:8]} prompt={prompt[:80]!r} (hist={len(session.history)})"
    )

    # Log the input structure for debugging
    history_from_db = len(session.history)
    items_this_turn = len(agent_input) - history_from_db
    logger.debug(
        f"API request starting - Session: {session.id}, "
        f"Total items: {len(agent_input)}, "
        f"From DB: {history_from_db}, "
        f"Added this turn: {items_this_turn}, "
        f"Include user message: {include_user_message}"
    )

    # Log item sequence (all fields, truncate content for readability)
    if logger.isEnabledFor(logging.DEBUG):
        import json

        item_sequence = []
        for idx, item in enumerate(agent_input):
            # Truncate content fields to keep log readable
            item_copy = dict(item)
            if "content" in item_copy and isinstance(item_copy["content"], str):
                if len(item_copy["content"]) > 100:
                    item_copy["content"] = (
                        item_copy["content"][:100] + "... (truncated)"
                    )

            # Show all fields as JSON
            item_json = json.dumps(item_copy, default=str)
            item_sequence.append(f"  [{idx}]: {item_json}")

        logger.debug(f"Item sequence:\n" + "\n".join(item_sequence))

    # Track pending tool calls that need results
    pending_tool_calls = {}
    # Track tool call statuses we set so we can clear them on completion
    _status_calls: dict[str, str] = {}

    # Track usage across turns
    last_usage_totals = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    turn_counter = 0

    # Track latest model info seen during the run (can change across turns)
    resolved_max_output_tokens = max_output_tokens
    if resolved_max_output_tokens is None:
        resolved_max_output_tokens = getattr(
            getattr(agent, "model_settings", None), "max_tokens", None
        )

    last_model_info = {
        "model": model_label or getattr(agent, "model", None),
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": resolved_max_output_tokens,
        "truncation": getattr(
            getattr(agent, "model_settings", None), "truncation", None
        ),
    }

    try:
        # Use Runner to run the agent
        from agents import RunConfig

        run_config_kwargs = {
            "group_id": group_id or f"session_{session.id}",
            "workflow_name": workflow_name or "omniagents_conversation",
            "call_model_input_filter": _strip_phase_from_model_input,
        }
        if model_provider is not None:
            run_config_kwargs["model_provider"] = model_provider
        run_config = RunConfig(**run_config_kwargs)

        # Import Runner locally to avoid module-level import
        from agents import Runner as _Runner

        run_input: Any = agent_input
        result = None

        while True:
            result = _Runner.run_streamed(
                agent,
                run_input,
                context=context,
                max_turns=max_turns,
                run_config=run_config,
            )
            session.current_stream_result = result

            async for event in result.stream_events():
                # Handle raw response events to emit usage on turn completion
                if event.type == "raw_response_event":
                    data = getattr(event, "data", None)
                    if (
                        isinstance(data, ResponseCompletedEvent)
                        and data.response
                        and data.response.usage
                    ):
                        # Use the usage directly from OpenAI's response (already cumulative)
                        # This bypasses the agents library's incorrect double-accumulation
                        raw_usage = data.response.usage
                        # Try to capture the actual model used for this turn (if provided by provider)
                        model_used = getattr(data.response, "model", None)
                        if model_used:
                            last_model_info["model"] = model_used
                        current_totals = {
                            "requests": turn_counter
                            + 1,  # Increment request count ourselves
                            "input_tokens": raw_usage.input_tokens,
                            "output_tokens": raw_usage.output_tokens,
                            "total_tokens": raw_usage.total_tokens,
                            "input_tokens_details": {
                                "cached_tokens": (
                                    getattr(
                                        raw_usage.input_tokens_details,
                                        "cached_tokens",
                                        0,
                                    )
                                    if raw_usage.input_tokens_details
                                    else 0
                                )
                            },
                            "output_tokens_details": {
                                "reasoning_tokens": (
                                    getattr(
                                        raw_usage.output_tokens_details,
                                        "reasoning_tokens",
                                        0,
                                    )
                                    if raw_usage.output_tokens_details
                                    else 0
                                )
                            },
                        }
                        delta = {
                            "requests": current_totals["requests"]
                            - last_usage_totals["requests"],
                            "input_tokens": current_totals["input_tokens"]
                            - last_usage_totals["input_tokens"],
                            "output_tokens": current_totals["output_tokens"]
                            - last_usage_totals["output_tokens"],
                            "total_tokens": current_totals["total_tokens"]
                            - last_usage_totals["total_tokens"],
                            "input_tokens_details": {
                                "cached_tokens": current_totals["input_tokens_details"][
                                    "cached_tokens"
                                ]
                                - last_usage_totals["input_tokens_details"][
                                    "cached_tokens"
                                ],
                            },
                            "output_tokens_details": {
                                "reasoning_tokens": current_totals[
                                    "output_tokens_details"
                                ]["reasoning_tokens"]
                                - last_usage_totals["output_tokens_details"][
                                    "reasoning_tokens"
                                ],
                            },
                        }
                        turn_counter += 1
                        usage_payload = {
                            "run_id": asyncio.current_task().get_name(),
                            "session_id": session.id,
                            "turn": turn_counter,
                            "delta": delta,
                            "totals": current_totals,
                            "response_id": getattr(data.response, "id", None),
                            # Optional model info so clients can adjust UI per-turn
                            "model": last_model_info.get("model"),
                            "max_input_tokens": last_model_info.get("max_input_tokens"),
                            "max_output_tokens": last_model_info.get(
                                "max_output_tokens"
                            ),
                            "truncation": last_model_info.get("truncation"),
                        }
                        # Drop None values to avoid sending JSON null for optional fields
                        for key in [
                            "model",
                            "max_input_tokens",
                            "max_output_tokens",
                            "truncation",
                        ]:
                            if usage_payload.get(key) is None:
                                usage_payload.pop(key, None)
                        await _broadcast(session, "token", usage_payload)
                        if service is not None:
                            try:
                                emit = getattr(service, "emit_runtime_hook", None)
                                if emit is not None:
                                    await emit(
                                        "token",
                                        session=session,
                                        run_id=asyncio.current_task().get_name(),
                                        params=usage_payload,
                                    )
                            except Exception:
                                pass
                        last_usage_totals = current_totals
                    # Continue to next event after handling raw response
                    continue

                # Skip low-level / uninterested event types
                if event.type not in ("run_item_stream_event",):
                    continue

                item = event.item
                method, payload = _stream_event_payload(
                    item,
                    run_id=asyncio.current_task().get_name(),
                    session_id=session.id,
                )

                # Track tool calls that need results
                if method == "tool_called" and payload.get("call_id"):
                    call_id = payload["call_id"]
                    tool_name = payload.get("tool", "unknown")
                    pending_tool_calls[call_id] = tool_name
                    # If the tool has a client-status override, emit it as a client_request
                    try:
                        # Attempt to find a matching tool object on the agent
                        for t in getattr(agent, "tools", []) or []:
                            name = getattr(t, "name", None)
                            if name == tool_name and hasattr(t, "_client_status_text"):
                                text = str(getattr(t, "_client_status_text"))
                                if text:
                                    status_payload = {
                                        "request_id": f"status_{call_id}",
                                        "function": "ui.set_status",
                                        "args": {"text": text, "show_spinner": True},
                                        "run_id": asyncio.current_task().get_name(),
                                        "session_id": session.id,
                                    }
                                    await _broadcast(
                                        session,
                                        "client_request",
                                        status_payload,
                                    )
                                    _status_calls[call_id] = text
                                break
                    except Exception:
                        # Best-effort; ignore if lookup fails
                        pass
                elif method == "tool_result" and payload.get("call_id"):
                    # Tool got a result, remove from pending
                    call_id = payload["call_id"]
                    pending_tool_calls.pop(call_id, None)
                    # Clear any status override we set for this call
                    if call_id in _status_calls:
                        try:
                            clear_payload = {
                                "request_id": f"status_clear_{call_id}",
                                "function": "ui.set_status",
                                "args": {"text": "", "show_spinner": False},
                                "run_id": asyncio.current_task().get_name(),
                                "session_id": session.id,
                            }
                            await _broadcast(
                                session,
                                "client_request",
                                clear_payload,
                            )
                        except Exception:
                            pass
                        finally:
                            _status_calls.pop(call_id, None)

                if method:
                    delivered = await _broadcast(session, method, payload)
                    if not delivered:
                        session.pending_events.append((method, payload))

                    # Emit audit events for tool_called / tool_result
                    if service is not None and method in ("tool_called", "tool_result"):
                        try:
                            from omniagents.core.audit.events import (
                                make_audit_event,
                                truncate_for_audit,
                                TOOL_CALLED,
                                TOOL_RESULT,
                            )

                            _et = (
                                TOOL_CALLED if method == "tool_called" else TOOL_RESULT
                            )
                            _max = 4096
                            if method == "tool_called":
                                _details = {
                                    "tool": payload.get("tool"),
                                    "call_id": payload.get("call_id"),
                                    "input": truncate_for_audit(
                                        payload.get("input", ""), _max
                                    ),
                                }
                            else:
                                _details = {
                                    "tool": payload.get("tool"),
                                    "call_id": payload.get("call_id"),
                                    "output": truncate_for_audit(
                                        payload.get("output", ""), _max
                                    ),
                                }
                            await service.emit_runtime_hook(
                                "audit",
                                **make_audit_event(
                                    _et,
                                    session_id=getattr(session, "id", None),
                                    user_id=getattr(session, "user_id", None),
                                    run_id=payload.get("run_id"),
                                    details=_details,
                                ),
                            )
                        except Exception:
                            pass

                # Persist item into session history
                if hasattr(item, "to_input_item"):
                    try:
                        inp = item.to_input_item()
                        if inp:
                            session.append_message(inp)
                    except Exception:
                        pass

                if method:
                    Debug.log(
                        f"[srv]   → {method} run={payload.get('run_id','')} details={payload}"
                    )

            # ----- Handle tool-approval interruptions (SDK 0.16+) -----
            interruptions = list(getattr(result, "interruptions", []) or [])
            if not interruptions:
                break
            if service is None:
                # No service to mediate approval — reject everything to keep
                # the run from hanging.
                state = result.to_state()
                for item in interruptions:
                    state.reject(
                        item,
                        rejection_message="Tool approval unavailable in this context",
                    )
                run_input = state
                continue

            current_run_id = (
                asyncio.current_task().get_name() if asyncio.current_task() else None
            )
            state = result.to_state()
            for item in interruptions:
                raw = getattr(item, "raw_item", None)
                tool_call_id = getattr(raw, "call_id", None) or getattr(raw, "id", None)
                if not tool_call_id:
                    # Without a stable call_id we can't correlate a response.
                    state.reject(
                        item,
                        rejection_message="Tool call missing call_id; auto-rejected",
                    )
                    continue
                tool_name = item.tool_name or getattr(raw, "name", None) or "tool"
                arguments = getattr(raw, "arguments", None)
                response = await service.request_tool_approval(
                    session=session,
                    call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    run_id=current_run_id,
                )
                if response.get("decision") == "approve":
                    state.approve(item, always_approve=False)
                else:
                    state.reject(
                        item,
                        rejection_message=response.get("rejection_message"),
                    )

            run_input = state
            session.current_stream_result = None
            # Loop and resume with the approval-applied state.

    except asyncio.CancelledError:
        # Run was cancelled - the finally block will handle pending tool calls
        Debug.log("[srv] ✗ Run cancelled")
        # Re-raise to let the caller handle it
        raise
    except Exception as e:
        if (
            not context_error_retry
            and service is not None
            and session is not None
            and include_user_message
        ):
            try:
                invoke = getattr(service, "invoke_runtime_hooks", None)
                if invoke is not None:
                    hook_result = await invoke(
                        "error",
                        session=session,
                        run_id=(
                            asyncio.current_task().get_name()
                            if asyncio.current_task()
                            else ""
                        ),
                        params={
                            "prompt": prompt,
                            "error": e,
                            "is_context_length_exceeded": _is_context_length_exceeded(
                                e
                            ),
                        },
                    )
                    if (
                        isinstance(hook_result, dict)
                        and hook_result.get("retry") is True
                    ):
                        return await stream_agent(
                            agent,
                            prompt,
                            session,
                            rpc_channel,
                            service=service,
                            max_turns=max_turns,
                            context=context,
                            max_input_tokens=max_input_tokens,
                            max_output_tokens=max_output_tokens,
                            include_user_message=False,
                            context_error_retry=True,
                            workflow_name=workflow_name,
                            group_id=group_id,
                            user_content=user_content,
                            model_provider=model_provider,
                            model_label=model_label,
                        )
            except Exception:
                pass

        # Any other exception - the finally block will handle pending tool calls
        Debug.log(f"[srv] ✗ Error during run: {e}")

        # Log detailed error context for debugging
        logger.error(f"API request failed: {e}", exc_info=True)

        # If it's an APIError, log the actual error body from OpenAI
        from openai import APIError

        if isinstance(e, APIError):
            logger.error(f"OpenAI API Error Body: {e.body}")
            logger.error(f"OpenAI API Error Type: {e.type}")
            logger.error(f"OpenAI API Error Code: {e.code}")
            Debug.log(f"[srv] ✗ API Error body={e.body} type={e.type} code={e.code}")

        import traceback

        Debug.log(f"[srv] ✗ Traceback:\n{traceback.format_exc()}")

        logger.error(
            f"Error context - Session: {session.id}, "
            f"Turn: {turn_counter}, "
            f"Total items sent: {len(agent_input)}, "
            f"From DB: {history_from_db}, "
            f"Added this turn: {items_this_turn}"
        )

        # Log full item sequence on error (all fields, not just known ones)
        if logger.isEnabledFor(logging.ERROR):
            import json

            error_sequence = []
            for idx, item in enumerate(agent_input):
                # Truncate content fields to keep log readable
                item_copy = dict(item)
                if "content" in item_copy and isinstance(item_copy["content"], str):
                    if len(item_copy["content"]) > 200:
                        item_copy["content"] = (
                            item_copy["content"][:200] + "... (truncated)"
                        )

                # Dump the entire item as JSON to see all fields
                item_json = json.dumps(item_copy, indent=4, default=str)
                error_sequence.append(f"  [{idx}]:\n{item_json}")

            logger.error(f"Full item sequence at error:\n" + "\n".join(error_sequence))

        # Re-raise the original exception
        raise
    finally:
        session.current_stream_result = None
        # Handle any pending tool calls that didn't get results
        # This covers both cancellation and unexpected termination
        if pending_tool_calls:
            Debug.log(
                f"[srv] ✗ Run ended with {len(pending_tool_calls)} pending tool calls"
            )

            for call_id, tool_name in pending_tool_calls.items():
                # Add a tool result indicating the run was interrupted
                cancelled_result = {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": "[Cancelled by user]",
                }
                session.append_message(cancelled_result)

                # Also send the result as an event to the client
                cancelled_payload = {
                    "run_id": (
                        asyncio.current_task().get_name()
                        if asyncio.current_task()
                        else ""
                    ),
                    "session_id": session.id,
                    "tool": tool_name,
                    "output": "[Cancelled by user]",
                    "call_id": call_id,
                }
                await _broadcast(session, "tool_result", cancelled_payload)

                Debug.log(
                    f"[srv]   → tool_result (cancelled) call_id={call_id} tool={tool_name}"
                )

        Debug.log(
            f"[srv] ◀ end_run    session={session.id[:8]} hist={len(session.history)})"
        )

        if token is not None:
            try:
                reset_artifact_callback(token)
            except Exception:
                pass

    return {
        "usage": last_usage_totals,
        "model": last_model_info.get("model"),
        "max_input_tokens": last_model_info.get("max_input_tokens"),
        "max_output_tokens": last_model_info.get("max_output_tokens"),
        "truncation": last_model_info.get("truncation"),
    }
