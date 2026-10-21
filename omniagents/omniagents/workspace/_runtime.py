"""Workspace contextvar runtime.

The current workspace is held in a contextvar so the SDK's sandbox runtime
can swap it for the duration of a SandboxAgent run, and so worker agents
spawned as asyncio tasks inherit it via ``contextvars.copy_context()``.

There is deliberately no process-wide or cwd fallback. Every workspace access
must occur beneath an explicit Run environment lease.
"""

from __future__ import annotations

import contextvars
from pathlib import PurePosixPath
from typing import Optional, Protocol, runtime_checkable

from .base import Workspace
from .types import WorkspaceNotBoundError

_workspace_var: contextvars.ContextVar[Optional[Workspace]] = contextvars.ContextVar(
    "omniagents_workspace", default=None
)

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

    Missing binding is an error. Host execution is represented by an explicit
    HostEnvironment lease exactly like any sandbox or remote substrate.
    """
    ws = _workspace_var.get()
    if ws is None:
        raise WorkspaceNotBoundError()
    return ws


def set_workspace(
    workspace: Optional[Workspace],
) -> contextvars.Token[Optional[Workspace]]:
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


# ---------------------------------------------------------------------
# Mutation observation (tkt_FqUypyxF2tzC)
# ---------------------------------------------------------------------


@runtime_checkable
class WorkspaceMutationObserver(Protocol):
    """Notified around every content-mutating workspace call.

    Exists so the run-level diff can be built from ONE chokepoint instead of
    a hook in each of ``write_file`` / ``edit_file`` / ``apply_patch`` /
    ``edit_notebook`` / ``download_file``: every builtin FILE tool goes
    through the workspace content primitives, so observing here catches all
    of them — including file tools added later — and cannot drift out of
    sync with any one tool's ``ui_metadata``.

    It does NOT cover :func:`~omniagents.workspace.exec` /
    :func:`~omniagents.workspace.spawn`, which is how ``execute_bash`` runs.
    A command that rewrites a file (a formatter, ``git checkout``, ``mv``)
    is invisible here; the run-diff consumer compensates by re-reading every
    tracked path at the end of the run
    (:meth:`~omniagents.core.conversation.run_diff.RunDiffObserver.finalize`),
    which fixes stale content but cannot discover paths the run only ever
    touched through a shell.

    Both hooks are awaited inside the mutating call, so an implementation
    must be cheap and must not raise: the caller swallows and logs, because
    a diff-bookkeeping fault may never fail a user's edit.
    """

    async def before_mutation(self, path: str) -> None:
        """Called before the write/removal lands."""

    async def after_mutation(self, path: str) -> None:
        """Called after the write/removal lands."""


_mutation_observer_var: contextvars.ContextVar[Optional[WorkspaceMutationObserver]] = (
    contextvars.ContextVar("omniagents_workspace_mutation_observer", default=None)
)


def current_mutation_observer() -> Optional[WorkspaceMutationObserver]:
    """The observer bound to the current run, or ``None``.

    Per-run contextvar, deliberately not a process global: two concurrent
    runs must never accumulate into one another's diff — the same failure
    mode the singleton plan bridge had.
    """
    return _mutation_observer_var.get()


def set_mutation_observer(
    observer: Optional[WorkspaceMutationObserver],
) -> contextvars.Token[Optional[WorkspaceMutationObserver]]:
    """Bind *observer* for the current context. Returns the restore token."""
    return _mutation_observer_var.set(observer)


def reset_mutation_observer(
    token: contextvars.Token[Optional[WorkspaceMutationObserver]],
) -> None:
    _mutation_observer_var.reset(token)
