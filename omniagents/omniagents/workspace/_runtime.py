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
from pathlib import PurePosixPath
from typing import Optional

from .base import Workspace
from .host import HostWorkspace


_workspace_var: contextvars.ContextVar[Optional[Workspace]] = contextvars.ContextVar(
    "omniagents_workspace", default=None
)

# Process-wide default used when no per-run workspace is bound on the
# contextvar. ``set_workspace`` (per-run, e.g. SandboxAgent's
# WorkspaceBindingCapability) always wins over this. The "host" execution
# target sets it so plain-Agent runs — which have no per-run binding and
# may execute in uvicorn-spawned tasks that don't inherit a startup
# contextvar — still route through the configured HostWorkspace.
_default_workspace: Optional[Workspace] = None


def set_default_workspace(workspace: Optional[Workspace]) -> None:
    """Install the process-wide fallback workspace (or clear it with None)."""
    global _default_workspace
    _default_workspace = workspace


# The cwd requested for the current run (the agent's navigable working
# directory). ``stream_agent`` sets it from the run's ``workspace_root``;
# ``WorkspaceBindingCapability.bind`` reads it to root the SandboxWorkspace
# there. Per-run scope via contextvar so concurrent sessions don't collide.
_requested_cwd_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "omniagents_requested_cwd", default=None
)


def set_requested_cwd(path: Optional[str]) -> None:
    """Record the cwd requested for the current run (task-scoped)."""
    _requested_cwd_var.set(path)


def get_requested_cwd() -> Optional[str]:
    """The cwd requested for the current run, or None."""
    return _requested_cwd_var.get()


def clamp_within(path: Optional[str], root: str) -> str:
    """Return *path* iff it is *root* or a descendant of it, else *root*.

    The navigable cwd must stay inside the workspace boundary: a sandbox cwd
    can move among subdirectories of the manifest root but can never become a
    host path (which would be outside it). Pure string/path logic — no
    filesystem access — so it works against container paths from the host.
    """
    if isinstance(path, str) and path.strip():
        try:
            p = PurePosixPath(path)
            r = PurePosixPath(root)
            if p == r or r in p.parents:
                return str(p)
        except Exception:
            pass
    return root


def current_workspace() -> Workspace:
    """Return the workspace active in the current async context.

    Resolution: the per-run contextvar, then the process-wide default set
    via :func:`set_default_workspace`, then a fresh ``HostWorkspace(cwd)``.
    The cwd fallback is computed lazily so changing cwd is reflected
    (matters in tests that ``monkeypatch.chdir``).
    """
    ws = _workspace_var.get()
    if ws is not None:
        return ws
    if _default_workspace is not None:
        return _default_workspace
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
