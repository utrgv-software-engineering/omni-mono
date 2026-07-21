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

Pause/unpause/notify-activity resolve the live session from the
framework's process-global serve-session state
(:func:`omniagents.core.runtime.serve_state.get_sandbox_session`) and
probe its lifecycle surface through
:mod:`omniagents.core.sandbox.lifecycle_session` — session-agnostic by
design: pause freezes EVERY process in the container, which is why this
is a ``sandbox.*`` namespace rather than per-conversation scope.

``sandbox.switch`` validates the request and delegates to the executor
the serve host installed via :func:`omniagents.core.runtime.
sandbox_lifecycle.set_switch_executor`; the orchestration itself
(quiesce, snapshot, rebuild, re-wire, rollback) is serve-host policy.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function


def _resolve_backend_session() -> Any:
    """The live session's backend, or ``None`` when no session is bound.

    The serve host publishes the wrapped ``SandboxSession`` via
    ``serve_state.set_sandbox_session`` after start. Lifecycle methods
    live on the backend session, so we resolve through the framework's
    lifecycle helper (which unwraps the SDK proxy; bare sessions pass
    through unchanged).
    """
    from omniagents.core.runtime.serve_state import get_sandbox_session
    from omniagents.core.sandbox.lifecycle_session import resolve_backend_session

    session = get_sandbox_session()
    if session is None:
        return None
    return resolve_backend_session(session)


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
)
async def sandbox_pause(service: Any) -> dict:  # noqa: ARG001
    inner = _resolve_backend_session()
    if inner is None:
        return {"ok": False, "supported": False, "reason": "no live sandbox session"}
    if not hasattr(inner, "pause"):
        return {
            "ok": False,
            "supported": False,
            "reason": "backend does not implement pause",
        }
    try:
        await inner.pause()
    except Exception as exc:
        return {"ok": False, "supported": True, "reason": str(exc)}

    # Best-effort paused-state report. ``paused()`` is optional; if absent
    # we trust the pause() call returned cleanly.
    paused = True
    if hasattr(inner, "paused"):
        try:
            paused = bool(await inner.paused())
        except Exception:
            pass
    return {"ok": True, "supported": True, "paused": paused}


@server_function(
    name_override="sandbox.unpause",
    description=(
        "Thaw a previously-paused sandbox container. Sub-second resume; "
        "every process picks up where it was frozen."
    ),
    params_schema=_PAUSE_SCHEMA,
    result_schema=_PAUSE_RESULT_SCHEMA,
    strict=True,
)
async def sandbox_unpause(service: Any) -> dict:  # noqa: ARG001
    inner = _resolve_backend_session()
    if inner is None:
        return {"ok": False, "supported": False, "reason": "no live sandbox session"}
    if not hasattr(inner, "unpause"):
        return {
            "ok": False,
            "supported": False,
            "reason": "backend does not implement unpause",
        }
    try:
        await inner.unpause()
    except Exception as exc:
        return {"ok": False, "supported": True, "reason": str(exc)}

    paused = False
    if hasattr(inner, "paused"):
        try:
            paused = bool(await inner.paused())
        except Exception:
            pass
    return {"ok": True, "supported": True, "paused": paused}


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
)
async def sandbox_notify_activity(service: Any) -> dict:  # noqa: ARG001
    inner = _resolve_backend_session()
    if inner is None or not hasattr(inner, "notify_activity"):
        return {"ok": False}
    try:
        await inner.notify_activity()
    except Exception:
        return {"ok": False}
    return {"ok": True}


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
)
async def sandbox_discard_snapshot(service: Any) -> dict:  # noqa: ARG001
    from omniagents.core.runtime.serve_state import set_discard_snapshot_on_exit

    set_discard_snapshot_on_exit(True)
    return {"ok": True}


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
)
async def sandbox_switch(service: Any, profile: str) -> dict:  # noqa: ARG001
    # Delegate to the serve host's orchestrator, which holds the published
    # SwitchContext (app/spec/session_id/snapshot_dir/...) and owns the
    # quiesce/snapshot/rebuild/re-wire/rollback sequence.
    from omniagents.core.runtime.sandbox_lifecycle import switch_executor

    if not isinstance(profile, str) or not profile.strip():
        return {"ok": False, "reason": "profile must be a non-empty string"}
    executor = switch_executor()
    if executor is None:
        return {
            "ok": False,
            "reason": (
                "no sandbox switch executor installed — the serve host must "
                "call omniagents.core.runtime.sandbox_lifecycle."
                "set_switch_executor at startup"
            ),
        }
    return await executor(profile)


SANDBOX_SERVER_FUNCTIONS = [
    sandbox_pause,
    sandbox_unpause,
    sandbox_notify_activity,
    sandbox_discard_snapshot,
    sandbox_switch,
]
