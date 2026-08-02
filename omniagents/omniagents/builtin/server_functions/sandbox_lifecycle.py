"""Builtin ``sandbox.*`` lifecycle server functions.

Container-level lifecycle RPCs for the sandbox a serve session runs in:
``sandbox.pause`` / ``sandbox.unpause`` (docker cgroup freezer, or a
graceful "unsupported" on backends without an equivalent primitive),
``sandbox.notify_activity`` (idle-timer presence ping),
``sandbox.discard_snapshot`` (terminal-shutdown flag), and
``sandbox.switch`` (in-place profile switch). Activated by the builtin
``sandbox`` capability pack; lifted from omni-code.

**Authority model — operator only.** Every function here is a
control-plane command invoked by the launcher / a slash command over the
server-function channel. Server functions are a separate registry from
the model's tool schema, so none of these are LLM-reachable — in
particular the model cannot call ``sandbox.switch`` to move itself to a
less-isolated profile. The agent-facing counterpart is the read-only
observation pair in the same pack (``sandbox_get_state`` /
``sandbox_wait_until_ready``,
``omniagents.builtin.tools.sandbox_observe``).

The operation router resolves an environment-scoped lifecycle controller into
an immutable lease. No handler consults process-global serve state.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function
from omniagents.execution import require_environment_lease
from omniagents.execution.errors import CapabilityUnavailableError
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


def _lifecycle_binding():
    lease = require_environment_lease()
    controller = lease.lifecycle_controller
    if controller is None:
        raise CapabilityUnavailableError(
            lease.environment_id,
            frozenset({"environment_lifecycle"}),
        )
    return lease, controller


_PAUSE_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_PAUSE_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "supported": {"type": "boolean"},
        "paused": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["ok"],
    "additionalProperties": False,
}


@server_function(
    name_override="sandbox.pause",
    description=(
        "Freeze all processes in the sandbox container (docker cgroup "
        "freezer). RAM is held; CPU drops to zero. Resume with sandbox.unpause."
    ),
    params_schema=_PAUSE_SCHEMA,
    result_schema=_PAUSE_RESULT_SCHEMA,
    strict=True,
    resource_scope=ResourceScope.ENVIRONMENT,
    execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
)
async def sandbox_pause(service: Any) -> dict:  # noqa: ARG001
    lease, controller = _lifecycle_binding()
    return await controller.pause(lease)


@server_function(
    name_override="sandbox.unpause",
    description=(
        "Thaw a previously-paused sandbox container. Sub-second resume; "
        "every process picks up where it was frozen."
    ),
    params_schema=_PAUSE_SCHEMA,
    result_schema=_PAUSE_RESULT_SCHEMA,
    strict=True,
    resource_scope=ResourceScope.ENVIRONMENT,
    execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
)
async def sandbox_unpause(service: Any) -> dict:  # noqa: ARG001
    lease, controller = _lifecycle_binding()
    return await controller.unpause(lease)


@server_function(
    name_override="sandbox.notify_activity",
    description=(
        "External presence ping — reset the idle timer so the sandbox "
        "doesn't auto-pause while the user is actively engaging with a "
        "client surface (chat UI focus, code-server keystrokes, browser "
        "tab pointed at a published port, etc.)."
    ),
    params_schema=_PAUSE_SCHEMA,
    result_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    },
    strict=True,
    resource_scope=ResourceScope.ENVIRONMENT,
    execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
)
async def sandbox_notify_activity(service: Any) -> dict:  # noqa: ARG001
    lease, controller = _lifecycle_binding()
    return await controller.notify_activity(lease)


@server_function(
    name_override="sandbox.discard_snapshot",
    description=(
        "Flag this session's shutdown as terminal: skip persisting the "
        "workspace snapshot during teardown. Called by the launcher right "
        "before SIGTERM when the user closes a session for good — the "
        "launcher deletes the snapshot tar afterwards, so the persist "
        "(fingerprint + full workspace tar export) would be wasted work. "
        "Operator command, NOT an agent tool."
    ),
    params_schema=_PAUSE_SCHEMA,
    result_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    },
    strict=True,
    resource_scope=ResourceScope.ENVIRONMENT,
    execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
)
async def sandbox_discard_snapshot(service: Any) -> dict:  # noqa: ARG001
    lease, controller = _lifecycle_binding()
    return await controller.discard_snapshot(lease)


_SWITCH_SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {
            "type": "string",
            "description": "Path to the target sandbox profile YAML to switch to.",
        }
    },
    "required": ["profile"],
    "additionalProperties": False,
}

_SWITCH_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "profile": {"type": "string"},
        "backend": {"type": "string"},
        "container_id": {"type": "string"},
        "services": {"type": "object", "additionalProperties": True},
        "reason": {"type": "string"},
    },
    "required": ["ok"],
    "additionalProperties": True,
}


@server_function(
    name_override="sandbox.switch",
    description=(
        "Switch the running sandbox to a different profile in place, without "
        "restarting the server: snapshots the current workspace, brings up the "
        "new backend, rehydrates the workspace, and re-attaches it to the live "
        "agent. Operator command (the launcher / a slash command) — NOT an "
        "agent tool: the model cannot move itself to a less-isolated sandbox. "
        "The conversation and WebSocket stay alive across the switch."
    ),
    params_schema=_SWITCH_SCHEMA,
    result_schema=_SWITCH_RESULT_SCHEMA,
    # NOT strict: this is an operator RPC (launcher / slash command), never an
    # LLM tool, so it doesn't need OpenAI strict-schema. Strict would also
    # reject the `services` map's `additionalProperties: true` and, because the
    # registrar validates the whole module, would drop sibling sandbox.* server
    # functions (pause/unpause) too.
    strict=False,
    resource_scope=ResourceScope.ENVIRONMENT,
    execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
)
async def sandbox_switch(service: Any, profile: str) -> dict:  # noqa: ARG001
    if not isinstance(profile, str) or not profile.strip():
        return {"ok": False, "reason": "profile must be a non-empty string"}
    lease, controller = _lifecycle_binding()
    return await controller.switch(lease, profile)


SANDBOX_SERVER_FUNCTIONS = [
    sandbox_pause,
    sandbox_unpause,
    sandbox_notify_activity,
    sandbox_discard_snapshot,
    sandbox_switch,
]
