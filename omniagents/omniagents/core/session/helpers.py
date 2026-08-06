"""Tool-side helpers for accessing per-session state from inside a tool.

Tools receive a ``RunContextWrapper`` from the SDK; the OmniAgents bridge
stamps ``session_id`` and ``workspace_root`` onto its inner context dict
(see ``AgentService._apply_session_metadata``). These helpers turn a ctx
into the things tools actually want: the session id, the live ``Session``
object, or the session's variables dict.
"""

from __future__ import annotations

from typing import Any, Optional


def session_id_from_ctx(ctx: Any) -> Optional[str]:
    """Read ``session_id`` off the run context, or ``None`` if missing.

    The bridge stamps this onto the context dict before the SDK hands ctx
    to tools. Tools invoked outside the framework (unit tests with hand-
    built contexts) may not have it — return None and let the caller
    degrade gracefully.
    """
    c = getattr(ctx, "context", None)
    if c is None:
        return None
    if isinstance(c, dict):
        v = c.get("session_id")
    else:
        v = getattr(c, "session_id", None)
    if isinstance(v, str) and v.strip():
        return v
    return None


def session_from_ctx(ctx: Any) -> Optional[Any]:
    """Resolve the live ``Session`` for this run, or ``None``.

    Uses the process-global ``current_service()`` accessor to find the
    session manager. Returns None if no service is running or the session
    id is missing.
    """
    sid = session_id_from_ctx(ctx)
    if not sid:
        return None
    try:
        from omniagents.core.agents.service import current_service

        svc = current_service()
    except Exception:
        return None
    if svc is None:
        return None
    sm = getattr(svc, "_session_manager", None)
    if sm is None:
        return None
    return getattr(sm, "_by_id", {}).get(sid)


def session_variables(ctx: Any) -> dict:
    """Return ``session.variables`` for the run, or an empty dict.

    Tools that defer to user-supplied per-session context (ticket id,
    project id, workspace path, custom config) call this to read it. The
    return value is the live dict — mutations are visible to other tools
    in the same run but won't auto-persist; use
    ``AgentService.update_session_variables`` to mutate persistently.
    """
    sess = session_from_ctx(ctx)
    if sess is None:
        return {}
    vars_dict = getattr(sess, "variables", None)
    if isinstance(vars_dict, dict):
        return vars_dict
    return {}


__all__ = [
    "session_id_from_ctx",
    "session_from_ctx",
    "session_variables",
]


# ---------------------------------------------------------------------------
# Run-scoped current session id
# ---------------------------------------------------------------------------
#
# Set by the run bridge for the duration of stream_agent, so code that runs
# inside the run's task tree but never receives a RunContextWrapper (e.g.
# the agent-tool on_stream observer, whose dispatch task inherits this via
# contextvars) can still attribute work to the owning session.

import contextvars as _contextvars

_current_session_id: _contextvars.ContextVar[Optional[str]] = (
    _contextvars.ContextVar("omniagents_current_session_id", default=None)
)


def set_current_session_id(
    session_id: Optional[str],
) -> _contextvars.Token:
    """Install *session_id* as the run-scoped current session."""
    return _current_session_id.set(session_id)


def reset_current_session_id(token: _contextvars.Token) -> None:
    """Restore the previous current-session value."""
    _current_session_id.reset(token)


def current_session_id() -> Optional[str]:
    """The session id of the run active in this async context, or None."""
    return _current_session_id.get()
