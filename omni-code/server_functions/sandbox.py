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
from omniagents.core.session import Session


def _resolve_inner_session() -> Any:
    """Return the inner backend session, or None if no session is bound.

    ``serve_cli._serve`` publishes the wrapped ``SandboxSession`` via
    ``agent_workspace_state.set_sandbox_session`` after start. We reach
    through ``_inner`` to access backend-specific methods like
    ``pause`` / ``unpause`` — the public ``SandboxSession`` wrapper
    explicitly proxies a fixed method set and does not forward by
    ``__getattr__``.
    """
    from omni_code.agent_workspace_state import get_sandbox_session

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
async def sandbox_pause(service: Any, session: Session) -> dict:  # noqa: ARG001
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
async def sandbox_unpause(service: Any, session: Session) -> dict:  # noqa: ARG001
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
async def sandbox_notify_activity(service: Any, session: Session) -> dict:  # noqa: ARG001
    inner = _resolve_inner_session()
    if inner is None or not hasattr(inner, "notify_activity"):
        return {"ok": False}
    try:
        await inner.notify_activity()
    except Exception:
        return {"ok": False}
    return {"ok": True}


SANDBOX_SERVER_FUNCTIONS = [sandbox_pause, sandbox_unpause, sandbox_notify_activity]
