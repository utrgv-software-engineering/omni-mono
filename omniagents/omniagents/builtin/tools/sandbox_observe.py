"""Read-only sandbox observation tools for the agent.

The agent can SEE its sandbox state and WAIT for an operator-initiated switch
to finish — but it cannot change the sandbox. Switching profiles is a
control-plane operation (the ``sandbox.switch`` server function, invoked by the
launcher / a slash command), deliberately NOT exposed as an LLM tool: the model
must not be able to move itself to a less-isolated profile (e.g. out of a
network-restricted devbox onto the host). See
:mod:`omniagents.core.runtime.sandbox_lifecycle` for the authority model.

State is read from the process-global ``serve_state.SwitchContext``
that the serve host publishes; outside a serve process the tools degrade to a
benign "ready, no switching" answer.

Bundled by the builtin ``sandbox`` capability pack; lifted from omni-code.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agents.run_context import RunContextWrapper

from omniagents.core.tools import function_tool


def _state_dict() -> dict:
    from omniagents.core.runtime.serve_state import get_switch_context

    ctx = get_switch_context()
    if ctx is None:
        return {
            "available": False,
            "ready": True,
            "switching": False,
            "profile": None,
            "backend": None,
            "container_id": None,
            "last_error": None,
        }
    return {
        "available": True,
        "ready": ctx.ready_event.is_set() and not ctx.switching,
        "switching": ctx.switching,
        "profile": ctx.current_profile_name,
        "backend": ctx.backend,
        "container_id": ctx.container_id,
        "last_error": ctx.last_error,
    }


@function_tool(running_summary="Checking sandbox status...")
async def sandbox_get_state(ctx: RunContextWrapper[Any]) -> str:  # noqa: ARG001
    """Report the current sandbox you're running in.

    Returns the active sandbox profile name and backend, whether a profile
    switch is currently in progress, and the last switch error if any. Use this
    to confirm which environment your filesystem/shell tools will execute in
    (for example before relying on a tool only available in a desktop profile).

    You cannot change the sandbox — that's an operator action the user triggers
    in the UI. To react to one in progress, see ``sandbox_wait_until_ready``.
    """
    return json.dumps(_state_dict())


@function_tool(running_summary="Waiting for sandbox...")
async def sandbox_wait_until_ready(
    ctx: RunContextWrapper[Any],  # noqa: ARG001
    timeout_seconds: float = 120.0,
) -> str:
    """Block until an in-progress sandbox switch has finished.

    If the user asked you to do something "after switching to <profile>", call
    this first: your filesystem/shell tool calls would fail mid-switch (the old
    sandbox is being torn down and the new one brought up), so wait for the
    sandbox to become ready, then proceed. Returns immediately when no switch is
    in progress.

    Args:
        timeout_seconds: Max seconds to wait before giving up (default 120).
            On timeout, returns ``ready: false`` — the switch is taking unusually
            long; report that to the user rather than retrying blindly.
    """
    from omniagents.core.runtime.serve_state import get_switch_context

    sw = get_switch_context()
    if sw is None:
        return json.dumps(
            {"ready": True, "profile": None, "reason": "no live sandbox server"}
        )
    try:
        await asyncio.wait_for(sw.ready_event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return json.dumps(
            {"ready": False, "profile": sw.current_profile_name, "reason": "timeout"}
        )
    return json.dumps(
        {
            "ready": not sw.switching,
            "profile": sw.current_profile_name,
            "switching": sw.switching,
            "last_error": sw.last_error,
        }
    )
