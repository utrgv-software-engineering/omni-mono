"""Builtin ``sandbox.*`` lifecycle server functions.

Container-level lifecycle RPCs for the sandbox a serve session runs in:
``sandbox.pause`` / ``sandbox.unpause`` (docker cgroup freezer, or a
graceful "unsupported" on backends without an equivalent primitive) and
``sandbox.discard_snapshot`` (terminal-shutdown flag: destroy the
environment's durable state on close instead of preserving it).
Activated by the builtin ``sandbox`` capability pack; lifted from
omni-code.

**Authority model — operator only.** Every function here is a
control-plane command invoked by the launcher / a slash command over the
server-function channel. Server functions are a separate registry from
the model's tool schema, so none of these are LLM-reachable. Profile changes
materialize and rebind another environment through the AgentHost control
plane. The agent-facing counterpart is the read-only
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
    name_override="sandbox.discard_snapshot",
    description=(
        "Flag this environment's shutdown as terminal: on close, destroy "
        "its durable state instead of preserving it — for a docker "
        "environment that means stopping and REMOVING the container (the "
        "workspace lives in its writable layer) and deleting the session-"
        "state record. Called by the launcher when the user closes or "
        "archives a session for good. Operator command, NOT an agent tool."
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


SANDBOX_SERVER_FUNCTIONS = [
    sandbox_pause,
    sandbox_unpause,
    sandbox_discard_snapshot,
]
