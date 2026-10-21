"""Workspace value types and errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class FileEntry:
    """One entry returned by ``Workspace.list_dir``.

    Every field means the same thing on every substrate — host, sandbox
    container, pyodide. Backends must build entries through
    :func:`omniagents.workspace._entries.make_entry`, which enforces the
    invariants below rather than leaving each backend to spell them the
    way its transport happens to.
    """

    name: str
    """The entry's bare basename — never a path, never a separator."""
    path: str
    """Workspace-relative POSIX path from ``Workspace.root``.

    No leading ``/``, no ``..``, no drive letter, ``/`` separators on
    every platform, so ``workspace.resolve(entry.path)`` round-trips back
    to this entry on any backend. The one exception is an *unconfined*
    ``HostWorkspace`` listing a directory outside its root, where the
    absolute POSIX path is reported instead (there is no relative
    spelling for it).
    """
    is_dir: bool
    """True only for real directories — ``lstat`` semantics.

    An entry is described by itself, never by a symlink target: a symlink
    pointing at a directory is ``is_dir=False, is_symlink=True``.
    """
    size: Optional[int] = None
    """Size in bytes for non-directories; ``None`` for directories."""
    mtime: Optional[datetime] = None
    """Local modification time when the substrate exposes one.

    ``None`` when it does not — notably ``SandboxWorkspace``, whose SDK
    directory listing carries no timestamp.
    """
    is_symlink: bool = False
    """True when the entry itself is a symlink."""


@dataclass
class ExecResult:
    """Result of a one-shot ``Workspace.exec``."""

    stdout: str
    stderr: str
    exit_code: int
    wall_time_seconds: float
    truncated: bool = False
    """True when stdout/stderr were trimmed to honor ``max_output_bytes``."""


@dataclass
class GrepMatch:
    """One line returned by ``Workspace.grep``."""

    path: str
    line_number: int
    line: str
    column: Optional[int] = None


class WorkspaceError(Exception):
    """Base class for workspace-layer errors."""


class WorkspaceNotBoundError(WorkspaceError):
    """Raised when a workspace operation runs without an environment lease."""

    def __init__(self) -> None:
        super().__init__(
            "No workspace is bound to this run. Select an execution environment "
            "before using filesystem or process tools."
        )


class WorkspaceEscapeError(WorkspaceError):
    """Raised when a path resolves outside the workspace root.

    Defense in depth: ensures host-mode and sandbox-mode behave identically
    when given absolute paths or ``..`` traversal that would step outside
    the workspace.
    """


class WorkspaceFeatureUnavailable(WorkspaceError):
    """Raised when the current workspace doesn't support a requested feature.

    Most common case: ``spawn(pty=True)`` against a sandbox client whose
    transport doesn't support PTY.
    """
