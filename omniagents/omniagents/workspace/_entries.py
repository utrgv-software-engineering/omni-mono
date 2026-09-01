"""One :class:`~omniagents.workspace.types.FileEntry` contract for every backend.

Every ``Workspace.list_dir`` implementation builds its results through
:func:`make_entry` so the fields mean the *same thing* on the host, inside
a sandbox container, and in pyodide. Before this module existed each
backend spelled the fields however its substrate happened to report them —
``SandboxWorkspace`` passed the SDK's absolute container path through for
both ``name`` and ``path``, which made every consumer that treated them as
workspace-relative (the ``fs_*`` surface, the ``git_*`` surface) fail with
a path-confinement rejection on sandbox workspaces only.

The convention, enforced here rather than documented and hoped for:

``name``
    The entry's bare basename — never a separator, never a path. It is
    what a user would call the file.
``path``
    A **workspace-relative POSIX path** from ``Workspace.root``: no
    leading ``/``, no ``..`` segment, no drive letter, ``/`` separators
    on every platform. ``workspace.resolve(entry.path)`` therefore always
    round-trips back to the entry, on every substrate.

    The single exception is :class:`HostWorkspace` (the "no sandbox" host
    target), which may legitimately list directories outside its root;
    those entries report the absolute POSIX path and pass
    ``allow_absolute=True``. Workspaces that bound their own address space
    — every sandbox, every pyodide workspace, and the composite
    :class:`MountedHostWorkspace` — may never emit an absolute path.
``is_dir``
    True only for real directories, ``lstat`` semantics: an entry is
    described by *itself*, never by a symlink target. A symlink pointing
    at a directory is ``is_dir=False, is_symlink=True``. This is what
    ``pyodide`` (``FS.lstat``) and the ``fs_*`` host scanner
    (``os.scandir(..., follow_symlinks=False)``) already did; the host
    workspace was the outlier and now matches.
``size``
    Byte size of the entry itself for non-directories, ``None`` for
    directories.
``mtime``
    Local ``datetime`` when the substrate exposes one, else ``None``.
``is_symlink``
    True when the entry itself is a symlink.
"""

from __future__ import annotations

import logging
import posixpath
from datetime import datetime
from typing import Iterable, List, Literal, Optional

from .types import FileEntry

logger = logging.getLogger(__name__)

__all__ = [
    "SORT_KEYS",
    "entry_basename",
    "log_skipped_entry",
    "make_entry",
    "sort_entries",
]


def log_skipped_entry(backend: str, directory: str, error: Exception) -> None:
    """Record that a listing dropped an entry it could not describe.

    Every backend skips rather than raises so one bad entry cannot abort a
    whole listing — but a skipped entry must never be *silent*, or a
    truncated listing is indistinguishable from an accurate one. There is
    no way to signal this in the return value (``list_dir`` returns a bare
    list), so the log is the only channel; keep the call at every skip.
    """
    logger.warning(
        "%s.list_dir dropped an entry in %r that does not satisfy the "
        "FileEntry contract: %s",
        backend,
        directory,
        error,
    )


def entry_basename(raw: object) -> str:
    """Bare name of one listed entry, whatever spelling a substrate used.

    Accepts a bare name (``"a.txt"``), a workspace-relative path
    (``"sub/a.txt"``) or an absolute container path
    (``"/workspace/sub/a.txt"``) and returns ``"a.txt"`` for all three.

    ``/`` is the only separator recognized, deliberately: a backslash is a
    perfectly legal character *inside* a POSIX filename, so treating it as
    a separator would rename ``a\\b.txt`` to ``b.txt`` and hand callers a
    path that points at nothing. Windows-style spellings are refused by
    :func:`make_entry` instead of being guessed at.
    """
    trimmed = str(raw or "").rstrip("/")
    if not trimmed:
        return ""
    return posixpath.basename(trimmed)


def _validated_relpath(path: str, *, allow_absolute: bool) -> str:
    """Normalize one entry path and prove it satisfies the convention.

    Only *structural* invariants are checked — the properties that decide
    whether a path still addresses something inside the workspace: it must
    be non-empty, relative (unless the backend is an unconfined host
    workspace), and must not climb out with ``..`` or name the root
    itself.

    Windows-shaped *spellings* are deliberately **not** rejected here.
    ``C:`` and ``E:migration.sql`` are ordinary, legal POSIX filenames; a
    substrate reporting one is telling the truth, and refusing it would
    make the file silently vanish from listings on some substrates but not
    others — recreating the exact per-substrate split this module exists
    to eliminate. Drive letters, UNC paths and backslashes are refused at
    the *wire* boundaries that actually need them
    (``fs_*``'s ``canonical_relpath``, ``git_*``'s
    ``canonical_repo_path``), where a rejection is a typed error to one
    caller rather than a hole in everybody's directory listing.
    """
    text = str(path or "")
    if not text:
        raise ValueError("entry path is empty")
    if text.startswith("/"):
        if not allow_absolute:
            raise ValueError(f"entry path {path!r} is absolute")
        return posixpath.normpath(text)
    normalized = posixpath.normpath(text)
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"entry path {path!r} escapes the workspace root")
    if normalized == ".":
        raise ValueError("entry path resolves to the workspace root itself")
    return normalized


def make_entry(
    *,
    name: object,
    path: str,
    is_dir: bool,
    size: Optional[int] = None,
    mtime: Optional[datetime] = None,
    is_symlink: bool = False,
    allow_absolute: bool = False,
) -> FileEntry:
    """Build one :class:`FileEntry`, enforcing the cross-substrate contract.

    Raises :class:`ValueError` when *path* is not a legal entry path (a
    substrate reporting an absolute path, a ``..`` escape or a drive
    letter is a bug in that backend, not something to pass to callers).
    Backends catch it per entry and skip, so one malformed entry can never
    abort a whole listing.
    """
    basename = entry_basename(name)
    if not basename:
        raise ValueError(f"entry name {name!r} has no basename")
    relpath = _validated_relpath(path, allow_absolute=allow_absolute)
    if entry_basename(relpath) != basename:
        raise ValueError(f"entry name {basename!r} does not match path {relpath!r}")
    return FileEntry(
        name=basename,
        path=relpath,
        is_dir=bool(is_dir),
        # A directory's ``size`` is a substrate artifact (the size of its
        # own inode), never useful to a caller and different on every
        # backend — the contract says ``None``.
        size=None if is_dir else (None if size is None else int(size)),
        mtime=mtime,
        is_symlink=bool(is_symlink),
    )


#: ``sort_by`` key functions shared by every backend, so two substrates
#: listing the same tree return it in the same order.
SORT_KEYS = {
    "name": lambda e: e.name,
    "mtime": lambda e: (e.mtime or datetime.min, e.name),
    "size": lambda e: (-(e.size or 0), e.name),
}


def sort_entries(
    entries: Iterable[FileEntry],
    sort_by: Literal["name", "mtime", "size"] = "name",
) -> List[FileEntry]:
    out = list(entries)
    out.sort(key=SORT_KEYS[sort_by])
    return out
