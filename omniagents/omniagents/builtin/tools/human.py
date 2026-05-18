"""Human-in-the-loop tools: ``notify`` and ``escalate``.

These are *client functions* — their bodies dispatch to the connected
client (CLI, launcher, web UI) via ``request_client_function``. Each
consuming app implements its own handler:

- ``notify`` — fire-and-forget heads-up. The client shows a toast / log
  line / Slack message / desk-bell sound. The run does NOT pause.
- ``escalate`` — pause the run until the human responds, then return
  their reply to the agent. The tool call blocks while the LLM waits;
  the run resumes with the human's input as the tool result.

Clients that don't implement a handler simply receive no toast (notify
is fire-and-forget so the agent sees success). For ``escalate``, calls
to clients without a handler block until ``timeout_seconds`` then return
``{"ok": False, "reason": "no client response within timeout"}``.
"""

from __future__ import annotations

from typing import Any

from agents.run_context import RunContextWrapper

from omniagents.core.tools import client_function_tool


@client_function_tool
async def notify(ctx: RunContextWrapper[Any], message: str) -> dict:
    """Send a heads-up to the human operator without stopping the run.

    Use for time-sensitive but non-blocking information: long-running
    operation started, milestone reached, partial result ready, anomaly
    detected that the human should know about but that doesn't block
    progress.

    Args:
        message: The notification text shown to the human.
    """
    # Body replaced by @client_function_tool decorator.


@client_function_tool(expect_result=True, timeout_seconds=1800)
async def escalate(ctx: RunContextWrapper[Any], message: str) -> dict:
    """Pause and ask the human operator for input. Returns their reply.

    Use ONLY when truly blocked on something that requires human
    judgement: missing credentials, ambiguous requirements with
    conflicting interpretations, an external system that needs manual
    intervention. Prefer ``notify`` for heads-up information that
    doesn't block.

    The run pauses while the human composes a response. After they
    reply, the reply text comes back to you as the tool result and the
    run resumes.

    Args:
        message: The question or blocker description shown to the human.

    Returns:
        ``{"ok": True, "result": <reply>}`` on success, or
        ``{"ok": False, "reason": ...}`` if no human responded in time.
    """
    # Body replaced by @client_function_tool decorator.


__all__ = ["notify", "escalate"]
