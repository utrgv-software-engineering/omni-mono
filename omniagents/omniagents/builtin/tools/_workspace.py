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
from typing import Any, Dict, Optional, Tuple

from agents.run_context import RunContextWrapper

from omniagents.core.errors import WorkspaceRootRequiredError


def workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> Optional[str]:
    """Return the workspace_root from the run context, or ``None``.

    Accepts either a dict or an attribute-style object. Returns an
    absolute path in the active workspace's path syntax when found, ``None``
    otherwise: a sandbox root stays POSIX on a Windows host.
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
        from omniagents import workspace as _ws
        from omniagents.workspace.types import WorkspaceNotBoundError

        try:
            pm = _ws.current_workspace().path_module
        except WorkspaceNotBoundError:
            # Nothing bound yet (e.g. while spawning a worker): host syntax.
            pm = os.path
        # A root that is not absolute in the workspace's syntax is a host path.
        return pm.normpath(wr) if pm.isabs(wr) else os.path.abspath(wr)
    return None


def require_workspace_root(ctx: RunContextWrapper[Any] | None, *, where: str) -> str:
    """Return the workspace_root from the run context, or raise.

    Args:
        ctx: the tool's run context wrapper.
        where: short label used in the error message (e.g. tool name).
    """
    wr = workspace_root_from_ctx(ctx)
    if wr is None:
        raise WorkspaceRootRequiredError(where=where)
    return wr


def shell_unavailable_payload(*, tool: str, command: str) -> Tuple[str, Dict[str, Any]]:
    """Standard error shape for shell-needing tools against shell-less backends.

    Returns ``(llm_output, ui_metadata)`` ready to drop into a
    :class:`RichToolOutput`. Used by ``execute_bash`` (omniagents builtin and
    omni-code variants) when the active workspace returns
    ``supports_shell == False`` — e.g. :class:`PyodideWorkspace`.

    Kept here rather than on the workspace package so the wording can stay
    tool-specific (the model gets a clear "use run_python instead" hint).
    """
    msg = (
        f"{tool}: the active workspace has no shell — pyodide and similar "
        "no-shell backends can't run arbitrary commands. Use run_python(...) "
        "for Python code, or switch to a shell-capable backend (host, docker, "
        "unix_local) for git/npm/bash workflows."
    )
    ui_metadata = {
        "value": msg,
        "display_type": "error",
        "summary": "Shell not available",
        "preview": f"{tool} cannot run {command!r} — workspace has no shell.",
        "truncated": False,
        "metadata": {
            "error_type": "shell_unavailable",
            "command": command,
        },
    }
    return msg, ui_metadata
