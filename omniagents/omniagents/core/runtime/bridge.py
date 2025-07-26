from __future__ import annotations

import os
import asyncio
from typing import AsyncGenerator

from agents import RunConfig, ItemHelpers
from omniagents.core.session import append_message

# ---------------------------------------------------------------------
# Optional verbose logging controlled by env var
# ---------------------------------------------------------------------
DEBUG_SERVER = os.getenv("OMNIAGENTS_SERVER_DEBUG", "0") in ("1", "true", "True")

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
            **({"call_id": call_id} if call_id else {})
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

        return "tool_result", {
            "run_id": run_id,
            "tool": getattr(item, "name", "<tool>"),
            "output": str(getattr(item, "output", "")),
            **({"call_id": call_id} if call_id else {})
        }
    elif item.type == "message_output_item":
        text = ItemHelpers.text_message_output(item)
        return "message_output", {
            "run_id": run_id,
            "content": text,
            "is_final": False
        }
    return None, None


async def stream_agent(agent, prompt: str, session, rpc_channel, *, max_turns: int = 20) -> None:  # noqa: D401
    """Drive agent.run_streamed and forward events to *rpc_channel*.

    ``rpc_channel`` is the :class:`RpcMethodsBase` instance attached to the
    WebSocket connection and exposes ``.async_call(method, args)`` for
    server-to-client notifications.
    """

    # ------------------------------------------------------------------
    # Build input preserving session history; persist **user** message so
    # future runs see it as part of the context as well.
    # ------------------------------------------------------------------
    agent_input = session.history + [{"role": "user", "content": prompt}]

    # Persist the new user message immediately so it is available for
    # subsequent runs even if the assistant errors out mid-stream.
    user_msg = {"role": "user", "content": prompt}
    session.history.append(user_msg)
    append_message(session.id, user_msg)

    if DEBUG_SERVER:
        print(f"[srv] ▶ start_run  session={session.id[:8]} prompt={prompt[:80]!r} (hist={len(session.history)})")

    # Use Runner to run the agent
    from agents import RunConfig
    run_config = RunConfig()
    
    # Import Runner locally to avoid module-level import
    from agents import Runner as _Runner
    result = _Runner.run_streamed(agent, agent_input, max_turns=max_turns, run_config=run_config)

    async for event in result.stream_events():
        # Skip low-level / uninterested event types
        if event.type not in ("run_item_stream_event",):
            continue

        item = event.item
        method, payload = _stream_event_payload(item, run_id=asyncio.current_task().get_name())
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
                    session.history.append(inp)
                    append_message(session.id, inp)
            except Exception:
                # Ignore malformed items but keep streaming.
                pass

        if DEBUG_SERVER and method:
            print(f"[srv]   → {method} run={payload.get('run_id','')} details={payload}")

    if DEBUG_SERVER:
        print(f"[srv] ◀ end_run    session={session.id[:8]} hist={len(session.history)}") 