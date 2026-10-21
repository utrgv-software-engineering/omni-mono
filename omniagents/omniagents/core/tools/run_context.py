"""Accessors for values the framework stamps onto the tool run context.

omniagents propagates per-session values (``session_id``, ``workspace_root``,
``task_scope``) via ``session.context`` so they flow into every tool
invocation. The context object may be a plain dict (tests, direct Runner
use) or an attribute-style object, so tools should go through these
helpers instead of reaching into ``ctx.context`` themselves.
"""

from __future__ import annotations

from typing import Any, Optional

DEFAULT_TASK_SCOPE = "main"


def ctx_value(ctx: Any, key: str) -> Any:
    """Read ``key`` off ``ctx.context``, dict- or attribute-style.

    Returns None when the context is absent or doesn't carry the key.
    """
    c = getattr(ctx, "context", None)
    if c is None:
        return None
    if isinstance(c, dict):
        return c.get(key)
    return getattr(c, key, None)


def session_id_from_ctx(ctx: Any) -> Optional[str]:
    """The owning session's id, or None outside a framework-managed run.

    Tools that key state by session should treat None as "uncategorized"
    rather than failing — direct Runner use and tests pass contexts with
    no session.
    """
    v = ctx_value(ctx, "session_id")
    if isinstance(v, str) and v.strip():
        return v
    return None


def task_scope_from_ctx(ctx: Any, default: str = DEFAULT_TASK_SCOPE) -> str:
    """The task-list scope for this run.

    Sub-agent spawners (e.g. workers) set ``task_scope`` on the child's
    context so each worker gets its own task list; everything else shares
    ``default``.
    """
    v = ctx_value(ctx, "task_scope")
    if isinstance(v, str) and v.strip():
        return v
    return default
