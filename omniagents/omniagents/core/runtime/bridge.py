from __future__ import annotations

import os
import asyncio
from typing import AsyncGenerator, Optional, Any

from agents import RunConfig, ItemHelpers
from openai.types.responses import ResponseCompletedEvent
from omniagents.core.debug import Debug

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
        # Include prompt at end and persist to session history on first attempt
        agent_input = session.history + [{"role": "user", "content": prompt}]
        user_msg = {"role": "user", "content": prompt}
        session.append_message(user_msg)
    else:
        # For retries, avoid re-adding the user message; keep context identical
        agent_input = list(session.history)

    if Debug.enabled():
        print(
            f"[srv] ▶ start_run  session={session.id[:8]} prompt={prompt[:80]!r} (hist={len(session.history)})"
        )

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
            if hasattr(item, "to_input_item"):
                try:
                    inp = item.to_input_item()
                    if inp:
                        session.append_message(inp)
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
        # Re-raise the original exception
        raise
    finally:
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
