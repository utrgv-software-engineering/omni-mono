from __future__ import annotations

import os
import asyncio
import logging
from typing import AsyncGenerator, Optional, Any

from agents import RunConfig, ItemHelpers
from openai.types.responses import ResponseCompletedEvent
from omniagents.core.debug import Debug

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


def _stream_event_payload(item, run_id: str):
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
        return "message_output", {"run_id": run_id, "content": text, "is_final": False}
    return None, None


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
    while i < len(history):
        item = history[i]
        item_type = item.get("type", "message")

        # Handle reasoning items - they need special treatment for parallel calls
        if item_type == "reasoning":
            reasoning = item
            j = i + 1

            # Collect all parallel function calls that follow the reasoning
            function_calls = []
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
                else:
                    break

            # Find the message that pairs with this reasoning
            message = None
            if j < len(history) and history[j].get("type") == "message":
                message = history[j]
                j += 1

            # Collect outputs for the function calls
            outputs = []
            call_ids = {fc.get("call_id") for fc in function_calls if fc.get("call_id")}
            while j < len(history) and call_ids:
                next_item = history[j]
                next_type = next_item.get("type", "")
                if next_type.endswith("_call_output"):
                    output_call_id = next_item.get("call_id")
                    if output_call_id in call_ids:
                        outputs.append(next_item)
                        call_ids.remove(output_call_id)
                        j += 1
                    else:
                        # Output for a different call, stop collecting
                        break
                else:
                    # Not an output, stop collecting
                    break

            # Reorder: reasoning → message → function_calls → outputs
            reordered.append(reasoning)
            if message:
                reordered.append(message)
            reordered.extend(function_calls)
            reordered.extend(outputs)

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
                if j < len(history):
                    next_item = history[j]
                    if (
                        next_item.get("type", "").endswith("_call_output")
                        and next_item.get("call_id") == call_id
                    ):
                        output = next_item
                        j += 1

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


async def stream_agent(
    agent,
    prompt: str,
    session,
    rpc_channel,
    *,
    max_turns: int = 20,
    context: Optional[Any] = None,
    context_window: Optional[int] = None,
    include_user_message: bool = True,
    workflow_name: Optional[str] = None,
) -> dict | None:  # noqa: D401
    """Drive agent.run_streamed and forward events to *rpc_channel*.

    ``rpc_channel`` is the :class:`RpcMethodsBase` instance attached to the
    WebSocket connection and exposes ``.async_call(method, args)`` for
    server-to-client notifications.
    """

    # ------------------------------------------------------------------
    # Build input preserving session history; persist **user** message so
    # future runs see it as part of the context as well.
    # ------------------------------------------------------------------
    if include_user_message:
        # Filter history to remove tool call items before passing to API
        clean_history = _filter_history_for_api(session.history)
        # Include prompt at end and persist to session history on first attempt
        agent_input = clean_history + [{"role": "user", "content": prompt}]
        user_msg = {"role": "user", "content": prompt}
        session.append_message(user_msg)
    else:
        # For retries, avoid re-adding the user message; keep context identical
        # Still need to filter history
        agent_input = _filter_history_for_api(list(session.history))

    if Debug.enabled():
        print(
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
    # Track reasoning items that need to be paired with their following message
    # Reasoning items MUST be followed by a message item per OpenAI API requirements
    pending_reasoning_item: dict | None = None

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
    last_model_info = {
        "model": getattr(agent, "model", None),
        "context_window": context_window,
        "max_output_tokens": getattr(
            getattr(agent, "model_settings", None), "max_tokens", None
        ),
        "truncation": getattr(
            getattr(agent, "model_settings", None), "truncation", None
        ),
    }

    try:
        # Use Runner to run the agent
        from agents import RunConfig

        run_config = RunConfig(
            group_id=f"session_{session.id}",
            workflow_name=workflow_name or "omniagents_conversation",
        )

        # Import Runner locally to avoid module-level import
        from agents import Runner as _Runner

        result = _Runner.run_streamed(
            agent,
            agent_input,
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
                                    raw_usage.input_tokens_details, "cached_tokens", 0
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
                            "reasoning_tokens": current_totals["output_tokens_details"][
                                "reasoning_tokens"
                            ]
                            - last_usage_totals["output_tokens_details"][
                                "reasoning_tokens"
                            ],
                        },
                    }
                    turn_counter += 1
                    usage_payload = {
                        "run_id": asyncio.current_task().get_name(),
                        "turn": turn_counter,
                        "delta": delta,
                        "totals": current_totals,
                        "response_id": getattr(data.response, "id", None),
                        # Optional model info so clients can adjust UI per-turn
                        "model": last_model_info.get("model"),
                        "context_window": last_model_info.get("context_window"),
                        "max_output_tokens": last_model_info.get("max_output_tokens"),
                        "truncation": last_model_info.get("truncation"),
                    }
                    # Drop None values to avoid sending JSON null for optional fields
                    for key in [
                        "model",
                        "context_window",
                        "max_output_tokens",
                        "truncation",
                    ]:
                        if usage_payload.get(key) is None:
                            usage_payload.pop(key, None)
                    await _broadcast(session, "token", usage_payload)
                    last_usage_totals = current_totals
                # Continue to next event after handling raw response
                continue

            # Skip low-level / uninterested event types
            if event.type not in ("run_item_stream_event",):
                continue

            item = event.item
            method, payload = _stream_event_payload(
                item, run_id=asyncio.current_task().get_name()
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
                                await _broadcast(
                                    session,
                                    "client_request",
                                    {
                                        "request_id": f"status_{call_id}",
                                        "function": "ui.set_status",
                                        "args": {"text": text, "show_spinner": True},
                                        "run_id": asyncio.current_task().get_name(),
                                        "session_id": session.id,
                                    },
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
                        await _broadcast(
                            session,
                            "client_request",
                            {
                                "request_id": f"status_clear_{call_id}",
                                "function": "ui.set_status",
                                "args": {"text": "", "show_spinner": False},
                                "run_id": asyncio.current_task().get_name(),
                                "session_id": session.id,
                            },
                        )
                    except Exception:
                        pass
                    finally:
                        _status_calls.pop(call_id, None)

            if method:
                # Push event to client; if the WebSocket is already closed we
                # remember the payload so it can be replayed when the user
                # reconnects.
                delivered = await _broadcast(session, method, payload)
                if not delivered:
                    session.pending_events.append((method, payload))

            # Persist item into session history when it reaches a finalised form
            # CRITICAL: Reasoning items MUST be paired with their following message
            # per OpenAI API requirements. If we persist a reasoning item without
            # its message, subsequent API calls will fail with:
            # "Item 'msg_X' was provided without its required 'reasoning' item: 'rs_Y'"
            if hasattr(item, "to_input_item"):
                try:
                    inp = item.to_input_item()
                    if inp:
                        # Handle reasoning items - hold them until we see their message or tool call
                        if isinstance(inp, dict) and inp.get("type") == "reasoning":
                            pending_reasoning_item = inp
                            # Don't persist yet - wait for the paired item (message or tool call)
                            if Debug.enabled():
                                print(
                                    f"[srv]   ⏸ Holding reasoning item {inp.get('id', '?')[:12]} until message/tool call arrives"
                                )
                        # Handle items that can follow reasoning: messages or tool calls
                        # Tool calls are OUTPUT items from the model (not input items like outputs/responses)
                        # They include: function_call, computer_call, file_search_call, code_interpreter_call, etc.
                        # But NOT: function_call_output, computer_call_output (these are tool results, not calls)
                        elif isinstance(inp, dict) and (
                            inp.get("type") == "message"
                            or (
                                isinstance(inp.get("type"), str)
                                and inp.get("type", "").endswith("_call")
                                and not inp.get("type", "").endswith("_call_output")
                            )
                        ):
                            if pending_reasoning_item:
                                # Persist reasoning item first (atomically with message/tool call)
                                session.append_message(pending_reasoning_item)
                                if Debug.enabled():
                                    print(
                                        f"[srv]   ✓ Persisted reasoning {pending_reasoning_item.get('id', '?')[:12]} with {inp.get('type', '?')} {inp.get('id', '?')[:12]}"
                                    )
                                pending_reasoning_item = None
                            # Then persist the message or tool call
                            session.append_message(inp)
                        # All other items (outputs, etc.) - persist normally and clear pending reasoning
                        else:
                            session.append_message(inp)
                            # If we had a pending reasoning item and see an output/other item,
                            # the pairing is broken - discard the orphaned reasoning
                            if pending_reasoning_item:
                                if Debug.enabled():
                                    print(
                                        f"[srv]   ⚠ Discarded orphaned reasoning {pending_reasoning_item.get('id', '?')[:12]} (followed by {inp.get('type', '?')})"
                                    )
                                pending_reasoning_item = None
                except Exception:
                    # Ignore malformed items but keep streaming.
                    pass

            if Debug.enabled() and method:
                print(
                    f"[srv]   → {method} run={payload.get('run_id','')} details={payload}"
                )

    except asyncio.CancelledError:
        # Run was cancelled - the finally block will handle pending tool calls
        if Debug.enabled():
            print(f"[srv] ✗ Run cancelled")
        # Re-raise to let the caller handle it
        raise
    except Exception as e:
        # Any other exception - the finally block will handle pending tool calls
        if Debug.enabled():
            print(f"[srv] ✗ Error during run: {e}")

        # Log detailed error context for debugging
        logger.error(f"API request failed: {e}")
        logger.error(
            f"Error context - Session: {session.id}, "
            f"Turn: {turn_counter}, "
            f"Total items sent: {len(agent_input)}, "
            f"From DB: {history_from_db}, "
            f"Added this turn: {items_this_turn}, "
            f"Pending reasoning item: {pending_reasoning_item.get('id') if pending_reasoning_item else None}"
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
            if Debug.enabled():
                print(
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
                    "tool": tool_name,
                    "output": "[Cancelled by user]",
                    "call_id": call_id,
                }
                await _broadcast(session, "tool_result", cancelled_payload)

                if Debug.enabled():
                    print(
                        f"[srv]   → tool_result (cancelled) call_id={call_id} tool={tool_name}"
                    )

        # Handle any pending reasoning item that never got its paired message
        # This can happen if the run was interrupted/cancelled after a reasoning
        # item but before the message arrived
        if pending_reasoning_item:
            if Debug.enabled():
                print(
                    f"[srv] ⚠ Run ended with orphaned reasoning {pending_reasoning_item.get('id', '?')[:12]} - discarded"
                )
            # Don't persist orphaned reasoning items - they would cause API errors

        if Debug.enabled():
            print(
                f"[srv] ◀ end_run    session={session.id[:8]} hist={len(session.history)})"
            )

    # Return final details for inclusion in run_end
    return {
        "usage": last_usage_totals,
        "model": last_model_info.get("model"),
        "context_window": last_model_info.get("context_window"),
        "max_output_tokens": last_model_info.get("max_output_tokens"),
        "truncation": last_model_info.get("truncation"),
    }
