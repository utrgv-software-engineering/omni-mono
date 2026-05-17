"""Workspace value types and errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class FileEntry:
    """One entry returned by ``Workspace.list_dir``."""

    name: str
    path: str
    """Path relative to the workspace root."""
    is_dir: bool
    size: Optional[int] = None
    """Size in bytes for files; ``None`` for directories."""
    mtime: Optional[datetime] = None
    is_symlink: bool = False
    """True when this entry is a symlink. Set on host; sandbox backends
    populate it on a best-effort basis depending on transport support."""


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
