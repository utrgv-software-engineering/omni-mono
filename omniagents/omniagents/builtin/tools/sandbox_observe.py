"""Read-only sandbox observation tools for the agent.

The agent can SEE its sandbox state and WAIT for an operator-initiated switch
to finish — but it cannot change the sandbox. Switching profiles is a
control-plane operation (the ``sandbox.switch`` server function, invoked by the
launcher / a slash command), deliberately NOT exposed as an LLM tool: the model
must not be able to move itself to a less-isolated profile (e.g. out of a
network-restricted devbox onto the host).

State is read from the current Run's environment lifecycle controller. A Run
without an environment (or whose environment has no lifecycle controller)
degrades to a benign "ready, unavailable" answer.

Bundled by the builtin ``sandbox`` capability pack; lifted from omni-code.
"""

from __future__ import annotations

import json
from typing import Any

from agents.run_context import RunContextWrapper

from omniagents.core.tools import function_tool


def _lifecycle_binding() -> tuple[Any, Any] | None:
    from omniagents.execution import current_request_context

    context = current_request_context()
    lease = context.environment_lease if context is not None else None
    controller = lease.lifecycle_controller if lease is not None else None
    if lease is None or controller is None:
        return None
    return lease, controller


def _state_dict() -> dict:
    binding = _lifecycle_binding()
    if binding is None:
        return {
            "available": False,
            "ready": True,
            "switching": False,
            "profile": None,
            "backend": None,
            "container_id": None,
            "last_error": None,
        }
    lease, controller = binding
    return controller.state(lease)


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
    binding = _lifecycle_binding()
    if binding is None:
        return json.dumps(
            {"ready": True, "profile": None, "reason": "no execution environment"}
        )
    lease, controller = binding
    return json.dumps(await controller.wait_until_ready(lease, timeout_seconds))
