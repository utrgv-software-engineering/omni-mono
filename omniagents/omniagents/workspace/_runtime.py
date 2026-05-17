"""Workspace contextvar runtime.

The current workspace is held in a contextvar so the SDK's sandbox runtime
can swap it for the duration of a SandboxAgent run, and so worker agents
spawned as asyncio tasks inherit it via ``contextvars.copy_context()``.

Default: a lazily-constructed :class:`HostWorkspace` rooted at the process
cwd, so non-sandbox runs Just Work without any setup.
"""

from __future__ import annotations

import contextvars
import os
from typing import Optional

from .base import Workspace
from .host import HostWorkspace


_workspace_var: contextvars.ContextVar[Optional[Workspace]] = contextvars.ContextVar(
    "omniagents_workspace", default=None
)


def current_workspace() -> Workspace:
    """Return the workspace active in the current async context.

    Falls back to a fresh ``HostWorkspace(cwd)`` when nothing has been set.
    The fallback is computed lazily so changing cwd is reflected (matters
    in tests that ``monkeypatch.chdir``).
    """
    ws = _workspace_var.get()
    if ws is None:
        ws = HostWorkspace(root=os.getcwd())
        _workspace_var.set(ws)
    return ws


def set_workspace(workspace: Workspace) -> contextvars.Token[Optional[Workspace]]:
    """Install *workspace* as the current workspace. Returns the token used
    to restore the previous value.

    Used by the SDK sandbox bridge to swap in a :class:`SandboxWorkspace`
    for the duration of a SandboxAgent run::

        token = set_workspace(SandboxWorkspace(session=...))
        try:
            await runner.run_streamed(...)
        finally:
            reset_workspace(token)

    Tests can also use this directly to verify routing without spinning up
    a real sandbox session.
    """
    return _workspace_var.set(workspace)


def reset_workspace(token: contextvars.Token[Optional[Workspace]]) -> None:
    """Restore the workspace that was active before ``set_workspace`` was
    called with this token."""
    _workspace_var.reset(token)
