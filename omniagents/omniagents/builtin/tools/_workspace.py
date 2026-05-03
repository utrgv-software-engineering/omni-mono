"""Shared workspace_root helpers for builtin tools.

The framework guarantees ``workspace_root`` is set on the run context by
the time a tool executes (see ``AgentService.start_run`` — it raises
``WorkspaceRootRequiredError`` rather than falling back to cwd). These
helpers exist so each tool reads the field consistently and produces
the same error if the invariant is somehow violated (e.g. a tool
invoked outside the AgentService flow without populating context).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from agents.run_context import RunContextWrapper

from omniagents.core.errors import WorkspaceRootRequiredError


def workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> Optional[str]:
    """Return the workspace_root from the run context, or ``None``.

    Accepts either a dict or an attribute-style object. Returns an
    absolute path (``os.path.abspath``) when found, ``None`` otherwise.
    """
    if ctx is None:
        return None
    c = getattr(ctx, "context", None)
    if c is None:
        return None
    if isinstance(c, dict):
        wr = c.get("workspace_root")
    else:
        wr = getattr(c, "workspace_root", None)
    if isinstance(wr, str) and wr.strip():
        return os.path.abspath(wr)
    return None


def require_workspace_root(
    ctx: RunContextWrapper[Any] | None, *, where: str
) -> str:
    """Return the workspace_root from the run context, or raise.

    Args:
        ctx: the tool's run context wrapper.
        where: short label used in the error message (e.g. tool name).
    """
    wr = workspace_root_from_ctx(ctx)
    if wr is None:
        raise WorkspaceRootRequiredError(where=where)
    return wr
