"""Server functions for sandbox-level lifecycle.

Today this exposes pause / unpause — backed by ``container.pause()`` /
``container.unpause()`` (cgroup freezer) on the docker backend, and a
graceful "unsupported" response on backends without an equivalent
primitive (unix_local, future cloud backends that can only stop+start).

The launcher's idle detector calls ``sandbox.pause`` when the agent has
been quiet for a while; the next user activity triggers ``sandbox.unpause``.
Both are session-agnostic: pause freezes EVERY process in the container,
which is why this lives in a ``sandbox.*`` namespace instead of being
scoped per conversation session.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function


def _resolve_inner_session() -> Any:
    """Return the inner backend session, or None if no session is bound.

    ``serve_cli._serve`` publishes the wrapped ``SandboxSession`` via
    ``serve_state.set_sandbox_session`` after start. We reach
    through ``_inner`` to access backend-specific methods like
    ``pause`` / ``unpause`` — the public ``SandboxSession`` wrapper
    explicitly proxies a fixed method set and does not forward by
    ``__getattr__``.
    """
    from omniagents.core.runtime.serve_state import get_sandbox_session

    session = get_sandbox_session()
    if session is None:
        return None
    return getattr(session, "_inner", session)


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
    inner = _resolve_inner_session()
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
    inner = _resolve_inner_session()
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
    inner = _resolve_inner_session()
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
    # Delegate to the serve-side orchestrator, which holds the published
    # SwitchContext (app/spec/session_id/snapshot_dir/...). Imported lazily so
    # this module stays importable outside a running ``omni serve``.
    from omni_code.serve_cli import perform_switch

    return await perform_switch(profile)


SANDBOX_SERVER_FUNCTIONS = [
    sandbox_pause,
    sandbox_unpause,
    sandbox_notify_activity,
    sandbox_discard_snapshot,
    sandbox_switch,
]
