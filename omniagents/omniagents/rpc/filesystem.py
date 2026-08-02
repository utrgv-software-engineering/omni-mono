"""Environment-workspace filesystem watch + chunked transfer manager.

Backs the experimental ``fs_*`` GUI protocol surface (tkt_B5fXOZHf08K1):
GUI clients keep a live view of an environment's workspace (watch/unwatch with
server-pushed change events) and move large files in bounded chunked
frames instead of one oversized JSON payload.

Substrate model
---------------
All file access goes through the :class:`~omniagents.workspace.base.Workspace`
abstraction — the same session/sandbox boundary the terminal uses — so the
workspace may live on the host filesystem, inside a Docker sandbox, or on
a remote backend without this module caring. Watching is an honest
server-side **polling scanner** with coalesced diffs:

* ``poll:host`` — the workspace is a :class:`HostWorkspace`; snapshots come
  from ``os.scandir`` walks in a worker thread (cheap, page-cache backed).
  inotify is deliberately not used: polling behaves identically across
  substrates, needs no per-platform backends, and its diff/coalesce
  semantics are exactly what the wire protocol promises.
* ``poll:workspace`` — any other workspace (sandbox, remote); snapshots
  come from recursive :meth:`Workspace.list_dir` calls through the
  environment's transport. Costlier per scan, so the default poll interval is
  longer.

Flood control (#292 posture)
----------------------------
File-change bursts must never overflow the transport or kill the
connection with ``TransportOverloadedError``:

* Diffs are coalesced per poll cycle — at most one event per path per
  cycle, batched into frames of at most ``max_events_per_frame`` events.
* A cycle whose diff exceeds ``max_events_per_cycle`` is not delivered as
  events at all: the client receives one explicit ``fs_rescan_required``
  notification and is expected to relist (``fs_list``) — never an
  unbounded buffer.
* A tree that exceeds ``max_watch_entries`` terminates the watch with
  ``fs_rescan_required`` (``reason="scan_limit_exceeded"``); the client
  re-subscribes to a narrower path.

Downloads are **client-pull** (each chunk is a request/response), so
transfer flow control is inherent — the server never queues unread data.

Replay / reconnect (#295 decision)
----------------------------------
``fs_events``/``fs_rescan_required``/``fs_transfer_progress`` are
deliberately NOT journaled in the session replay log: they are
high-volume, derivable state. Watches and transfers are per-connection —
on reconnect the client re-subscribes (``fs_watch``) and rescans
(``fs_list``), and resumes downloads by reopening with a byte offset.
Filling a watch gap from a journal would be dishonest (events may have
been coalesced away); rescan-on-reconnect is the truthful semantic.

Path confinement
----------------
Every wire path is a workspace-relative POSIX path canonicalized by
:func:`canonical_relpath` (absolute paths, drive letters, backslashes and
``..`` escapes are rejected before any I/O). Host workspaces additionally
enforce the boundary in :meth:`HostWorkspace.resolve`, which resolves
symlinks — a symlink pointing outside the root raises
``WorkspaceEscapeError`` and surfaces as ``-32060 fs_path_invalid``.

Sandbox workspaces enforce the same boundary in two layers:
``SandboxWorkspace.resolve`` refuses absolute and ``..`` paths lexically,
and the SDK session's path policy refuses symlinks whose target escapes
the manifest root — a rejection the workspace layer translates into
``WorkspaceEscapeError`` rather than leaking an untyped transport
failure.

The two substrates agree on *refusing*; they do not agree on the error
**code** for a symlink that points outside the root. ``HostWorkspace``
realpaths inside ``resolve``, so the escape is visible before any I/O and
surfaces as ``-32060 fs_path_invalid``. On a sandbox the same path simply
does not exist as far as the workspace can see — the confinement check in
``exists``/``is_file`` answers ``False`` — so ``fs_stat`` and
``fs_download_open`` answer ``-32061 fs_not_found`` instead
(``fs_upload_commit`` does surface ``fs_path_invalid``, because the commit
reaches the substrate write). Both refuse, neither serves a byte, and
nothing about the outside file leaks; a client that branches on the code
must treat both as "refused". Unifying them would cost a container round
trip on every predicate call.

Downloads resolve the path exactly once, at ``fs_download_open``, and
serve every chunk from the file object retained by that open. A path
replaced with an outward-pointing symlink *after* the transfer opened
therefore cannot redirect reads outside the root: there is no second
path resolution to subvert. Payload size is capped at
``max_download_bytes`` (mirroring ``max_upload_bytes``) and, on non-host
substrates, checked against the advertised size before the file is read
into memory.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import mimetypes
import os
import posixpath
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from omniagents.workspace.base import Workspace
from omniagents.workspace.host import HostWorkspace
from omniagents.workspace.types import WorkspaceEscapeError

logger = logging.getLogger(__name__)

__all__ = [
    "FilesystemManager",
    "FilesystemRpcError",
    "FsNotFoundError",
    "FsPathError",
    "FsReadOnlyError",
    "FsTransferError",
    "FsWatchError",
    "RPC_ERROR_FS_NOT_FOUND",
    "RPC_ERROR_FS_PATH_INVALID",
    "RPC_ERROR_FS_READ_ONLY",
    "RPC_ERROR_FS_TRANSFER_INVALID",
    "RPC_ERROR_FS_WATCH_INVALID",
    "canonical_relpath",
    "guess_mime",
]

#: JSON-RPC error codes reserved for the filesystem surface (-3206x).
RPC_ERROR_FS_PATH_INVALID = -32060
RPC_ERROR_FS_NOT_FOUND = -32061
RPC_ERROR_FS_TRANSFER_INVALID = -32062
RPC_ERROR_FS_WATCH_INVALID = -32063
RPC_ERROR_FS_READ_ONLY = -32064


class FilesystemRpcError(Exception):
    """Typed ``fs_*`` error surfaced to the wire verbatim.

    ``code``/``data`` follow the shapes reserved in the OpenRPC contract
    (``FsPathInvalid``/``FsNotFound``/``FsTransferInvalid``/
    ``FsWatchInvalid``/``FsReadOnly``).
    """

    code: int = RPC_ERROR_FS_PATH_INVALID
    kind: str = "fs_path_invalid"

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self._data = {k: v for k, v in data.items() if v is not None}

    @property
    def data(self) -> Dict[str, Any]:
        return {"kind": self.kind, **self._data}


class FsPathError(FilesystemRpcError):
    code = RPC_ERROR_FS_PATH_INVALID
    kind = "fs_path_invalid"


class FsNotFoundError(FilesystemRpcError):
    code = RPC_ERROR_FS_NOT_FOUND
    kind = "fs_not_found"


class FsTransferError(FilesystemRpcError):
    code = RPC_ERROR_FS_TRANSFER_INVALID
    kind = "fs_transfer_invalid"


class FsWatchError(FilesystemRpcError):
    code = RPC_ERROR_FS_WATCH_INVALID
    kind = "fs_watch_invalid"


class FsReadOnlyError(FilesystemRpcError):
    code = RPC_ERROR_FS_READ_ONLY
    kind = "fs_read_only"


def canonical_relpath(path: str) -> str:
    """Canonicalize one wire path into a workspace-relative POSIX path.

    Pure and strict: rejects non-strings, NUL bytes, backslashes,
    absolute paths (POSIX or drive-letter), and any ``..`` traversal that
    would step above the workspace root. ``.``/empty mean the root.
    Interior ``.`` and redundant separators are normalized away, so two
    spellings of the same location always canonicalize identically.
    """
    if not isinstance(path, str):
        raise FsPathError("path must be a string")
    trimmed = path.strip()
    if "\x00" in trimmed:
        raise FsPathError("path contains a NUL byte")
    if "\\" in trimmed:
        raise FsPathError("path must use POSIX separators", path=path)
    if trimmed in ("", "."):
        return "."
    if trimmed.startswith("/") or posixpath.isabs(trimmed):
        raise FsPathError("absolute paths are not allowed", path=path)
    if len(trimmed) >= 2 and trimmed[1] == ":" and trimmed[0].isalpha():
        raise FsPathError("drive-letter paths are not allowed", path=path)
    stack: List[str] = []
    for segment in trimmed.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not stack:
                raise FsPathError("path escapes the workspace root", path=path)
            stack.pop()
            continue
        stack.append(segment)
    return "/".join(stack) if stack else "."


def guess_mime(path: str) -> str:
    """Best-effort MIME type for *path* (extension-based, never sniffed)."""
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def _mtime_to_float(value: Optional[datetime]) -> float:
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OverflowError, OSError, ValueError):
            return 0.0
    return 0.0


# ---------------------------------------------------------------------------
#  Snapshot scanning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SnapshotEntry:
    is_dir: bool
    size: int
    mtime: float


_Snapshot = Dict[str, _SnapshotEntry]


def _wire_relpath(rel: str) -> Optional[str]:
    """The wire spelling of *rel*, or ``None`` when it has none.

    ``canonical_relpath`` both validates *and normalizes* — it strips
    surrounding whitespace, among other things — so using its return value
    as the path of a real directory entry would invent names. A file
    called ``" lead.txt"`` would be advertised as ``"lead.txt"``, which
    ``fs_stat`` then cannot find; worse, ``" lead.txt"`` and ``"lead.txt"``
    canonicalize to the same key, so one of two real files would vanish
    from a listing (and a watch would see the alias flap as spurious
    created/deleted churn).

    Only an exact round-trip is addressable over this wire. Anything else
    — a name that raises, or one that merely *changes* — is dropped, the
    same way on every substrate. Rewriting it would be the silent-wrong-
    answer failure this surface is supposed to avoid.
    """
    try:
        canonical = canonical_relpath(rel)
    except FsPathError:
        return None
    return canonical if canonical == rel else None


def _scan_host(
    base_abs: str, root_abs: str, recursive: bool, max_entries: int
) -> Tuple[_Snapshot, bool]:
    """Snapshot a host directory tree without following symlinks.

    Returns ``(snapshot, truncated)``; keys are POSIX paths relative to
    the workspace root. Symlinked directories are recorded but never
    descended into, so a link pointing outside the root cannot pull
    foreign trees into the view.
    """
    out: _Snapshot = {}
    truncated = False
    stack = [base_abs]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for entry in entries:
            try:
                stat = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            rel = os.path.relpath(entry.path, root_abs).replace(os.sep, "/")
            if _wire_relpath(rel) is None:
                # A name this wire cannot express — a backslash, a drive
                # letter, surrounding whitespace — is dropped here exactly
                # as it is on every other substrate. Listing it would offer
                # the client an entry it could never stat or download, and
                # doing so on the host only would make the same tree report
                # differently per substrate.
                logger.debug("fs scan skipped un-addressable host entry %r", rel)
                continue
            if len(out) >= max_entries:
                truncated = True
                stack.clear()
                break
            out[rel] = _SnapshotEntry(
                is_dir=is_dir,
                size=0 if is_dir else int(stat.st_size),
                mtime=float(stat.st_mtime),
            )
            if is_dir and recursive:
                stack.append(entry.path)
    return out, truncated


async def _scan_generic(
    workspace: Workspace, base_rel: str, recursive: bool, max_entries: int
) -> Tuple[_Snapshot, bool]:
    """Snapshot through the Workspace interface (sandbox/remote substrates).

    ``FileEntry.path`` is a workspace-relative POSIX path on every
    substrate (see :class:`~omniagents.workspace.types.FileEntry`), so
    ``canonical_relpath`` here is a *check*, not a conversion: an entry a
    backend still managed to spell wrongly is dropped from the snapshot
    with a debug log rather than aborting the caller's whole listing or
    killing a running watch.
    """
    out: _Snapshot = {}
    truncated = False
    queue = [base_rel]
    while queue:
        directory = queue.pop()
        try:
            entries = await workspace.list_dir(
                directory, include_hidden=True, respect_gitignore=False
            )
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            # A directory that vanished, turned into a file, or became
            # unreadable between scans is simply gone from this snapshot.
            continue
        for entry in entries:
            if len(out) >= max_entries:
                truncated = True
                queue.clear()
                break
            rel = _wire_relpath(entry.path)
            if rel is None:
                logger.debug(
                    "fs scan skipped un-addressable entry %r under %r",
                    entry.path,
                    directory,
                )
                continue
            out[rel] = _SnapshotEntry(
                is_dir=entry.is_dir,
                size=0 if entry.is_dir else int(entry.size or 0),
                mtime=_mtime_to_float(entry.mtime),
            )
            if entry.is_dir and recursive and not entry.is_symlink:
                queue.append(rel)
    return out, truncated


async def _scan(
    workspace: Workspace, base_rel: str, recursive: bool, max_entries: int
) -> Tuple[_Snapshot, bool]:
    if isinstance(workspace, HostWorkspace):
        base_abs = workspace.resolve(base_rel)
        return await asyncio.to_thread(
            _scan_host, base_abs, workspace.root, recursive, max_entries
        )
    return await _scan_generic(workspace, base_rel, recursive, max_entries)


def _diff_snapshots(old: _Snapshot, new: _Snapshot) -> List[Dict[str, Any]]:
    """One coalesced event per changed path, sorted for determinism."""
    events: List[Dict[str, Any]] = []
    for path in sorted(old.keys() - new.keys()):
        entry = old[path]
        events.append(
            {
                "type": "deleted",
                "path": path,
                "entry_type": "directory" if entry.is_dir else "file",
            }
        )
    for path in sorted(new.keys() - old.keys()):
        entry = new[path]
        events.append(
            {
                "type": "created",
                "path": path,
                "entry_type": "directory" if entry.is_dir else "file",
            }
        )
    for path in sorted(old.keys() & new.keys()):
        before, after = old[path], new[path]
        if before == after:
            continue
        if before.is_dir != after.is_dir:
            events.append(
                {
                    "type": "deleted",
                    "path": path,
                    "entry_type": "directory" if before.is_dir else "file",
                }
            )
            events.append(
                {
                    "type": "created",
                    "path": path,
                    "entry_type": "directory" if after.is_dir else "file",
                }
            )
        else:
            events.append(
                {
                    "type": "modified",
                    "path": path,
                    "entry_type": "directory" if after.is_dir else "file",
                }
            )
    return events


# ---------------------------------------------------------------------------
#  Per-connection state
# ---------------------------------------------------------------------------


@dataclass
class _WatchState:
    watch_id: str
    environment_id: str
    channel: Any
    workspace: Workspace
    path: str
    recursive: bool
    interval: float
    mechanism: str
    snapshot: _Snapshot
    task: Optional["asyncio.Task[None]"] = None
    closed: bool = False


@dataclass
class _DownloadState:
    transfer_id: str
    environment_id: str
    channel: Any
    path: str
    size: int
    sha256: str
    mime: str
    #: Host file object opened once at ``download_open`` and retained for
    #: the life of the transfer. Every chunk is served from this fd, so the
    #: path can never be re-resolved (and therefore never redirected
    #: outside the root by a symlink swapped in mid-transfer).
    handle: Optional[Any] = None
    spool: Optional[Any] = None
    position: int = 0
    last_progress: float = 0.0


@dataclass
class _UploadState:
    transfer_id: str
    environment_id: str
    channel: Any
    workspace: Workspace
    path: str
    declared_size: int
    declared_sha256: Optional[str]
    expected_sha256: Optional[str]
    overwrite: bool
    spool: Any
    hasher: Any = field(default_factory=hashlib.sha256)
    received: int = 0
    last_progress: float = 0.0


# ---------------------------------------------------------------------------
#  Manager
# ---------------------------------------------------------------------------


class FilesystemManager:
    """Per-connection workspace watches and chunked transfers.

    All entry points take the resource-owning ``environment_id`` plus the calling
    connection's channel; watches and transfers are bound to that channel
    and torn down by :meth:`cleanup_channel` when it disconnects.
    """

    def __init__(
        self,
        *,
        workspace_resolver: Callable[[str], Awaitable[Workspace]],
        source_writability_resolver: Optional[
            Callable[[str, Workspace, str], Awaitable[bool]]
        ] = None,
        poll_interval_host: float = 1.0,
        poll_interval_generic: float = 2.0,
        min_poll_interval: float = 0.2,
        max_poll_interval: float = 30.0,
        max_watch_entries: int = 25_000,
        max_events_per_cycle: int = 512,
        max_events_per_frame: int = 128,
        max_watches_per_connection: int = 32,
        max_transfers_per_connection: int = 16,
        chunk_size: int = 256 * 1024,
        max_upload_bytes: int = 512 * 1024 * 1024,
        max_download_bytes: int = 512 * 1024 * 1024,
        progress_interval: float = 0.4,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._source_writability_resolver = source_writability_resolver
        self._poll_interval_host = poll_interval_host
        self._poll_interval_generic = poll_interval_generic
        self._min_poll_interval = min_poll_interval
        self._max_poll_interval = max_poll_interval
        self._max_watch_entries = max_watch_entries
        self._max_events_per_cycle = max_events_per_cycle
        self._max_events_per_frame = max_events_per_frame
        self._max_watches_per_connection = max_watches_per_connection
        self._max_transfers_per_connection = max_transfers_per_connection
        self.chunk_size = chunk_size
        self._max_upload_bytes = max_upload_bytes
        self._max_download_bytes = max_download_bytes
        self._progress_interval = progress_interval
        self._watches: Dict[str, _WatchState] = {}
        self._downloads: Dict[str, _DownloadState] = {}
        self._uploads: Dict[str, _UploadState] = {}
        # Same-process RPC commits to one environment/workspace/path are serialized
        # so an expected-content precondition and its publish form one critical
        # section. This cannot prevent an out-of-process shell writer from
        # changing a host path between our final read and os.replace; that
        # boundary is documented in the wire contract.
        self._upload_commit_locks: Dict[Tuple[str, type, str, str], asyncio.Lock] = {}
        self._upload_commit_lock_users: Dict[Tuple[str, type, str, str], int] = {}

    # ------------------------------------------------------------------
    #  Shared helpers
    # ------------------------------------------------------------------

    async def _workspace(self, environment_id: str) -> Workspace:
        return await self._workspace_resolver(environment_id)

    async def _effective_writability(
        self, environment_id: str, workspace: Workspace, rel: str
    ) -> Tuple[bool, Optional[str]]:
        """Return the effective GUI mutation policy for one canonical path."""
        resolver = self._source_writability_resolver
        if resolver is not None and not await resolver(environment_id, workspace, rel):
            return False, "source_read_only"
        if not await self._backend_writable(workspace, rel):
            return False, "backend_not_writable"
        return True, None

    async def _assert_writable(
        self, environment_id: str, workspace: Workspace, rel: str
    ) -> None:
        writable, reason = await self._effective_writability(
            environment_id, workspace, rel
        )
        if not writable:
            raise FsReadOnlyError(
                "filesystem target is read-only", path=rel, reason=reason
            )

    async def _reported_writable(
        self, environment_id: str, workspace: Workspace, rel: str
    ) -> bool:
        """Report escaped/unprovable listing entries as non-writable.

        Recursive scans intentionally include a symlink entry without
        descending through it. An outward-pointing symlink must therefore not
        fail the caller's entire listing merely because permission probing
        cannot resolve it inside the workspace boundary.
        """
        try:
            return (await self._effective_writability(environment_id, workspace, rel))[
                0
            ]
        except WorkspaceEscapeError:
            return False

    @staticmethod
    async def _backend_writable(workspace: Workspace, rel: str) -> bool:
        """Best available backend permission check without mutating the target.

        Host workspaces expose real permission bits. Other substrates may
        provide an async ``path_writable(path, *, directory)`` capability; a
        shell-backed workspace is probed as its configured runtime user. A
        backend that can prove neither is reported read-only instead of
        advertising a write that may fail.
        """
        if not isinstance(workspace, HostWorkspace):
            probe = rel
            is_directory = await workspace.is_dir(probe)
            is_file = False if is_directory else await workspace.is_file(probe)
            if not is_directory and not is_file:
                probe = posixpath.dirname(probe) or "."
                while not await workspace.is_dir(probe):
                    parent = posixpath.dirname(probe) or "."
                    if parent == probe:
                        return False
                    probe = parent
                is_directory = True

            capability = getattr(workspace, "path_writable", None)
            if callable(capability):
                try:
                    return bool(await capability(probe, directory=is_directory))
                except Exception:
                    return False

            if not bool(getattr(workspace, "supports_shell", False)):
                return False
            try:
                absolute = workspace.resolve(probe)
                writable = await workspace.exec(["test", "-w", absolute], shell=False)
                if writable.exit_code != 0:
                    return False
                if is_directory:
                    searchable = await workspace.exec(
                        ["test", "-x", absolute], shell=False
                    )
                    return searchable.exit_code == 0
                return True
            except Exception:
                return False

        absolute = workspace.resolve(rel)

        def _check() -> bool:
            if os.path.exists(absolute):
                if os.path.isdir(absolute):
                    return os.access(absolute, os.W_OK | os.X_OK)
                parent = os.path.dirname(absolute) or workspace.root
                return os.access(absolute, os.W_OK) and os.access(
                    parent, os.W_OK | os.X_OK
                )

            parent = os.path.dirname(absolute) or workspace.root
            while not os.path.exists(parent):
                next_parent = os.path.dirname(parent)
                if next_parent == parent:
                    return False
                parent = next_parent
            return os.path.isdir(parent) and os.access(parent, os.W_OK | os.X_OK)

        return await asyncio.to_thread(_check)

    @staticmethod
    def _mechanism(workspace: Workspace) -> str:
        return "poll:host" if isinstance(workspace, HostWorkspace) else "poll:workspace"

    async def _emit(self, channel: Any, method: str, payload: Dict[str, Any]) -> bool:
        try:
            await channel.async_call(method, payload)
            return True
        except Exception:
            logger.debug("fs notification %s failed", method, exc_info=True)
            return False

    async def _maybe_progress(
        self,
        state: "_DownloadState | _UploadState",
        *,
        direction: str,
        transferred: int,
        total: int,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - state.last_progress < self._progress_interval:
            return
        state.last_progress = now
        await self._emit(
            state.channel,
            "fs_transfer_progress",
            {
                "environment_id": state.environment_id,
                "transfer_id": state.transfer_id,
                "direction": direction,
                "transferred": transferred,
                "total": total,
            },
        )

    @staticmethod
    async def _host_stat(workspace: HostWorkspace, rel: str) -> os.stat_result:
        absolute = workspace.resolve(rel)

        def _stat() -> os.stat_result:
            return os.stat(absolute)

        return await asyncio.to_thread(_stat)

    async def _stat_entry(self, workspace: Workspace, rel: str) -> Dict[str, Any]:
        """Type/size/mtime for one canonical path, or FsNotFoundError."""
        if isinstance(workspace, HostWorkspace):
            try:
                stat = await self._host_stat(workspace, rel)
            except FileNotFoundError:
                raise FsNotFoundError("no such file or directory", path=rel)
            if os.path.isdir(workspace.resolve(rel)):
                return {
                    "path": rel,
                    "type": "directory",
                    "size": None,
                    "mtime": stat.st_mtime,
                }
            return {
                "path": rel,
                "type": "file",
                "size": int(stat.st_size),
                "mtime": float(stat.st_mtime),
            }
        if await workspace.is_dir(rel):
            return {"path": rel, "type": "directory", "size": None, "mtime": None}
        if not await workspace.is_file(rel):
            raise FsNotFoundError("no such file or directory", path=rel)
        size: Optional[int] = None
        mtime: Optional[float] = None
        parent = posixpath.dirname(rel) or "."
        name = posixpath.basename(rel)
        # ``FileEntry.name`` is the bare basename on every substrate, so
        # this match works on sandbox/remote workspaces too. It used to be
        # the absolute container path on ``SandboxWorkspace``, which meant
        # the match never fired and every sandbox file was advertised with
        # ``size: null`` — including to ``download_open``'s pre-read size
        # cap, which was therefore never enforced there.
        try:
            for entry in await workspace.list_dir(
                parent, include_hidden=True, respect_gitignore=False
            ):
                if entry.name == name:
                    # ``FileEntry.size`` is an ``lstat`` size by contract —
                    # it describes the entry, not a symlink's target. For a
                    # symlink that is the length of the link text, which is
                    # emphatically not what a client asking "how big is
                    # this download?" means. Report ``null`` (a documented
                    # "unknown") rather than a confidently wrong number;
                    # the host branch above realpaths and so reports the
                    # true size.
                    if not entry.is_symlink:
                        size = entry.size
                    mtime = _mtime_to_float(entry.mtime) or None
                    break
        except OSError:
            pass
        return {"path": rel, "type": "file", "size": size, "mtime": mtime}

    # ------------------------------------------------------------------
    #  Listings
    # ------------------------------------------------------------------

    async def stat(self, environment_id: str, path: str) -> Dict[str, Any]:
        rel = canonical_relpath(path)
        workspace = await self._workspace(environment_id)
        try:
            info = await self._stat_entry(workspace, rel)
        except WorkspaceEscapeError as exc:
            raise FsPathError(str(exc), path=path) from exc
        if info["type"] == "file":
            info["mime"] = guess_mime(rel)
        info["writable"] = await self._reported_writable(environment_id, workspace, rel)
        return info

    async def list(
        self, environment_id: str, path: str, recursive: bool = False
    ) -> Dict[str, Any]:
        rel = canonical_relpath(path)
        workspace = await self._workspace(environment_id)
        try:
            if not await self._is_dir(workspace, rel):
                raise FsNotFoundError("no such directory", path=path)
            snapshot, truncated = await _scan(
                workspace, rel, recursive, self._max_watch_entries
            )
        except WorkspaceEscapeError as exc:
            raise FsPathError(str(exc), path=path) from exc
        entries = [
            {
                "path": entry_path,
                "type": "directory" if entry.is_dir else "file",
                "size": None if entry.is_dir else entry.size,
                "mtime": entry.mtime or None,
                "writable": await self._reported_writable(
                    environment_id, workspace, entry_path
                ),
            }
            for entry_path, entry in sorted(snapshot.items())
        ]
        writable = await self._reported_writable(environment_id, workspace, rel)
        return {
            "path": rel,
            "entries": entries,
            "truncated": truncated,
            "writable": writable,
        }

    @staticmethod
    async def _is_dir(workspace: Workspace, rel: str) -> bool:
        if isinstance(workspace, HostWorkspace):
            absolute = workspace.resolve(rel)
            return await asyncio.to_thread(os.path.isdir, absolute)
        return await workspace.is_dir(rel)

    # ------------------------------------------------------------------
    #  Watches
    # ------------------------------------------------------------------

    def _channel_watch_count(self, channel: Any) -> int:
        return sum(1 for state in self._watches.values() if state.channel is channel)

    async def watch(
        self,
        environment_id: str,
        path: str,
        *,
        recursive: bool = True,
        poll_interval_ms: Optional[int] = None,
        channel: Any,
    ) -> Dict[str, Any]:
        if channel is None:
            raise FsWatchError("fs_watch requires a connected channel")
        if self._channel_watch_count(channel) >= self._max_watches_per_connection:
            raise FsWatchError(
                "too many watches on this connection",
                reason="watch_limit_exceeded",
            )
        rel = canonical_relpath(path)
        workspace = await self._workspace(environment_id)
        try:
            if not await self._is_dir(workspace, rel):
                raise FsNotFoundError("no such directory", path=path)
            snapshot, truncated = await _scan(
                workspace, rel, recursive, self._max_watch_entries
            )
        except WorkspaceEscapeError as exc:
            raise FsPathError(str(exc), path=path) from exc
        if truncated:
            raise FsWatchError(
                "watched tree exceeds the scan limit; watch a narrower path",
                reason="scan_limit_exceeded",
            )
        default_interval = (
            self._poll_interval_host
            if isinstance(workspace, HostWorkspace)
            else self._poll_interval_generic
        )
        interval = default_interval
        if poll_interval_ms is not None:
            interval = min(
                self._max_poll_interval,
                max(self._min_poll_interval, poll_interval_ms / 1000.0),
            )
        state = _WatchState(
            watch_id=uuid.uuid4().hex,
            environment_id=environment_id,
            channel=channel,
            workspace=workspace,
            path=rel,
            recursive=recursive,
            interval=interval,
            mechanism=self._mechanism(workspace),
            snapshot=snapshot,
        )
        self._watches[state.watch_id] = state
        state.task = asyncio.create_task(self._watch_loop(state))
        return {
            "watch_id": state.watch_id,
            "environment_id": environment_id,
            "path": rel,
            "recursive": recursive,
            "mechanism": state.mechanism,
            "poll_interval_ms": int(interval * 1000),
        }

    async def _watch_loop(self, state: _WatchState) -> None:
        try:
            while not state.closed:
                await asyncio.sleep(state.interval)
                if state.closed:
                    return
                try:
                    snapshot, truncated = await _scan(
                        state.workspace,
                        state.path,
                        state.recursive,
                        self._max_watch_entries,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug(
                        "fs watch scan failed (watch %s)",
                        state.watch_id,
                        exc_info=True,
                    )
                    continue
                if truncated:
                    # The tree outgrew the watchable limit: tell the client
                    # explicitly and terminate the watch (re-subscribing to
                    # a narrower path is the recovery, not silent lag).
                    await self._emit(
                        state.channel,
                        "fs_rescan_required",
                        {
                            "environment_id": state.environment_id,
                            "watch_id": state.watch_id,
                            "reason": "scan_limit_exceeded",
                        },
                    )
                    return
                events = _diff_snapshots(state.snapshot, snapshot)
                state.snapshot = snapshot
                if not events:
                    continue
                if len(events) > self._max_events_per_cycle:
                    # Overflow: one bounded rescan signal instead of an
                    # event flood. The watch keeps running against the
                    # fresh snapshot.
                    delivered = await self._emit(
                        state.channel,
                        "fs_rescan_required",
                        {
                            "environment_id": state.environment_id,
                            "watch_id": state.watch_id,
                            "reason": "event_overflow",
                        },
                    )
                    if not delivered:
                        return
                    continue
                for start in range(0, len(events), self._max_events_per_frame):
                    delivered = await self._emit(
                        state.channel,
                        "fs_events",
                        {
                            "environment_id": state.environment_id,
                            "watch_id": state.watch_id,
                            "events": events[
                                start : start + self._max_events_per_frame
                            ],
                        },
                    )
                    if not delivered:
                        return
        finally:
            state.closed = True
            self._watches.pop(state.watch_id, None)

    async def unwatch(
        self, environment_id: str, watch_id: str, *, channel: Any
    ) -> bool:
        state = self._watches.get(watch_id)
        if (
            state is None
            or state.environment_id != environment_id
            or state.channel is not channel
        ):
            raise FsWatchError("unknown watch", watch_id=watch_id)
        self._drop_watch(state)
        return True

    def _drop_watch(self, state: _WatchState) -> None:
        state.closed = True
        self._watches.pop(state.watch_id, None)
        if state.task is not None and not state.task.done():
            state.task.cancel()

    # ------------------------------------------------------------------
    #  Downloads (client-pull chunked reads)
    # ------------------------------------------------------------------

    async def download_open(
        self, environment_id: str, path: str, *, channel: Any
    ) -> Dict[str, Any]:
        self._check_transfer_budget(channel)
        rel = canonical_relpath(path)
        if rel == ".":
            raise FsPathError("cannot download a directory", path=path)
        workspace = await self._workspace(environment_id)
        state = _DownloadState(
            transfer_id=uuid.uuid4().hex,
            environment_id=environment_id,
            channel=channel,
            path=rel,
            size=0,
            sha256="",
            mime=guess_mime(rel),
        )
        info: Optional[Dict[str, Any]] = None
        try:
            if isinstance(workspace, HostWorkspace):
                absolute = workspace.resolve(rel)

                def _open_host() -> Tuple[Any, int, str]:
                    if not os.path.isfile(absolute):
                        raise FsNotFoundError("no such file", path=rel)
                    self._check_download_size(os.path.getsize(absolute))
                    # Opened once, kept for the whole transfer: the boundary
                    # was enforced by ``resolve`` above, and from here on
                    # chunks come from this fd rather than from the path, so
                    # swapping the path for a symlink cannot redirect reads.
                    handle = open(absolute, "rb")
                    try:
                        hasher = hashlib.sha256()
                        total = 0
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            hasher.update(block)
                            total += len(block)
                        self._check_download_size(total)
                    except BaseException:
                        handle.close()
                        raise
                    return handle, total, hasher.hexdigest()

                state.handle, state.size, state.sha256 = await asyncio.to_thread(
                    _open_host
                )
            else:
                if not await workspace.is_file(rel):
                    raise FsNotFoundError("no such file", path=rel)
                # Substrate reads materialize the whole file, so the cap is
                # checked against the advertised size *before* reading
                # whenever the substrate can advertise one.
                #
                # This is a best-effort early exit, NOT the guarantee: the
                # advertised size is absent for a symlink (whose lstat size
                # describes the link, not its target) and on substrates
                # whose listing carries no size at all. The hard limit is
                # the post-read ``_check_download_size(len(data))`` below,
                # which always runs. An oversized file reached through an
                # in-workspace symlink is therefore still refused, but only
                # after it has been read once.
                info = await self._stat_entry(workspace, rel)
                advertised = info.get("size")
                if isinstance(advertised, int):
                    self._check_download_size(advertised)
                # One substrate read: stage into a spooled temp file so
                # ranged chunk serving is local and the checksum describes
                # a stable point-in-time view of the file.
                data = await workspace.read_bytes(rel)
                self._check_download_size(len(data))
                state.size = len(data)
                state.sha256 = hashlib.sha256(data).hexdigest()
                spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
                spool.write(data)
                state.spool = spool
            if info is None:
                info = await self._stat_entry(workspace, rel)
        except WorkspaceEscapeError as exc:
            self._close_download(state)
            raise FsPathError(str(exc), path=path) from exc
        except BaseException:
            # Never leak a retained fd or a staging spool when open fails
            # after the payload was already staged.
            self._close_download(state)
            raise
        self._downloads[state.transfer_id] = state
        return {
            "transfer_id": state.transfer_id,
            "environment_id": environment_id,
            "path": rel,
            "size": state.size,
            "mime": state.mime,
            "mtime": info.get("mtime"),
            "sha256": state.sha256,
            "chunk_size": self.chunk_size,
        }

    def _download_state(
        self, environment_id: str, transfer_id: str, channel: Any
    ) -> _DownloadState:
        state = self._downloads.get(transfer_id)
        if (
            state is None
            or state.environment_id != environment_id
            or state.channel is not channel
        ):
            raise FsTransferError("unknown transfer", transfer_id=transfer_id)
        return state

    async def download_read(
        self,
        environment_id: str,
        transfer_id: str,
        *,
        offset: Optional[int] = None,
        length: Optional[int] = None,
        channel: Any,
    ) -> Dict[str, Any]:
        state = self._download_state(environment_id, transfer_id, channel)
        start = state.position if offset is None else offset
        if start < 0 or start > state.size:
            raise FsTransferError(
                "offset out of range", transfer_id=transfer_id, reason="bad_offset"
            )
        want = self.chunk_size if length is None else length
        if want <= 0 or want > self.chunk_size:
            raise FsTransferError(
                "length out of range", transfer_id=transfer_id, reason="bad_length"
            )
        handle = state.handle
        if handle is not None:
            # Served from the fd retained at open — the path is never
            # re-resolved, so no symlink swapped in mid-transfer can point
            # these reads outside the workspace root. Requests on one
            # connection are dispatched sequentially and a transfer is bound
            # to its creating connection, so the handle position is
            # uncontended.
            def _read() -> bytes:
                handle.seek(start)
                return handle.read(want)

            chunk = await asyncio.to_thread(_read)
        else:
            spool = state.spool
            assert spool is not None
            spool.seek(start)
            chunk = spool.read(want)
        state.position = start + len(chunk)
        eof = state.position >= state.size or not chunk
        await self._maybe_progress(
            state,
            direction="download",
            transferred=state.position,
            total=state.size,
            force=eof,
        )
        return {
            "transfer_id": transfer_id,
            "offset": start,
            "data": base64.b64encode(chunk).decode("ascii"),
            "bytes": len(chunk),
            "eof": eof,
            "size": state.size,
        }

    async def download_close(
        self, environment_id: str, transfer_id: str, *, channel: Any
    ) -> bool:
        state = self._download_state(environment_id, transfer_id, channel)
        self._close_download(state)
        return True

    def _close_download(self, state: _DownloadState) -> None:
        self._downloads.pop(state.transfer_id, None)
        for resource in (state.handle, state.spool):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass
        state.handle = None
        state.spool = None

    def _check_download_size(self, size: int) -> None:
        """Refuse a download whose payload exceeds the transferable cap.

        Mirrors ``max_upload_bytes``: on non-host substrates the file is
        materialized in one read, so an unbounded file would balloon the
        server's memory. Checked before that read happens.
        """
        if size > self._max_download_bytes:
            raise FsTransferError(
                f"file is {size} bytes, above the {self._max_download_bytes}-byte "
                "transfer limit",
                reason="size_out_of_range",
            )

    # ------------------------------------------------------------------
    #  Uploads (sequential chunked frames, staged then committed)
    # ------------------------------------------------------------------

    def _check_transfer_budget(self, channel: Any) -> None:
        active = sum(
            1 for state in self._downloads.values() if state.channel is channel
        ) + sum(1 for state in self._uploads.values() if state.channel is channel)
        if active >= self._max_transfers_per_connection:
            raise FsTransferError(
                "too many transfers on this connection",
                reason="transfer_limit_exceeded",
            )

    async def upload_open(
        self,
        environment_id: str,
        path: str,
        size: int,
        *,
        sha256: Optional[str] = None,
        expected_sha256: Optional[str] = None,
        overwrite: bool = False,
        channel: Any,
    ) -> Dict[str, Any]:
        self._check_transfer_budget(channel)
        rel = canonical_relpath(path)
        if rel == ".":
            raise FsPathError("cannot upload to the workspace root itself", path=path)
        if size < 0 or size > self._max_upload_bytes:
            raise FsTransferError(
                "declared size out of range", reason="size_out_of_range"
            )
        for field_name, digest in (
            ("sha256", sha256),
            ("expected_sha256", expected_sha256),
        ):
            if digest is not None and (
                len(digest) != 64
                or any(
                    character not in "0123456789abcdefABCDEF" for character in digest
                )
            ):
                raise FsTransferError(
                    f"{field_name} must be a 64-character hexadecimal digest",
                    reason="invalid_checksum",
                )
        workspace = await self._workspace(environment_id)
        try:
            if isinstance(workspace, HostWorkspace):
                # Resolving the target validates the whole path (symlinked
                # parents included) against the workspace boundary.
                workspace.resolve(rel)
            await self._assert_writable(environment_id, workspace, rel)
            if await workspace.is_dir(rel):
                raise FsTransferError(
                    "target is a directory", reason="target_is_directory"
                )
            if (
                not overwrite
                and expected_sha256 is None
                and await workspace.exists(rel)
            ):
                raise FsTransferError("target already exists", reason="target_exists")
        except WorkspaceEscapeError as exc:
            raise FsPathError(str(exc), path=path) from exc
        state = _UploadState(
            transfer_id=uuid.uuid4().hex,
            environment_id=environment_id,
            channel=channel,
            workspace=workspace,
            path=rel,
            declared_size=size,
            declared_sha256=sha256.lower() if sha256 else None,
            expected_sha256=(expected_sha256.lower() if expected_sha256 else None),
            overwrite=overwrite,
            spool=tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024),
        )
        self._uploads[state.transfer_id] = state
        return {
            "transfer_id": state.transfer_id,
            "environment_id": environment_id,
            "path": rel,
            "chunk_size": self.chunk_size,
        }

    def _upload_state(
        self, environment_id: str, transfer_id: str, channel: Any
    ) -> _UploadState:
        state = self._uploads.get(transfer_id)
        if (
            state is None
            or state.environment_id != environment_id
            or state.channel is not channel
        ):
            raise FsTransferError("unknown transfer", transfer_id=transfer_id)
        return state

    async def upload_chunk(
        self,
        environment_id: str,
        transfer_id: str,
        offset: int,
        data: str,
        *,
        channel: Any,
    ) -> Dict[str, Any]:
        state = self._upload_state(environment_id, transfer_id, channel)
        if offset != state.received:
            raise FsTransferError(
                f"chunk offset {offset} does not match received bytes {state.received}",
                transfer_id=transfer_id,
                reason="offset_mismatch",
            )
        try:
            chunk = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FsTransferError(
                "chunk data is not valid base64",
                transfer_id=transfer_id,
                reason="invalid_data",
            ) from exc
        if len(chunk) > self.chunk_size:
            raise FsTransferError(
                "chunk exceeds the negotiated chunk size",
                transfer_id=transfer_id,
                reason="chunk_too_large",
            )
        if state.received + len(chunk) > state.declared_size:
            raise FsTransferError(
                "upload exceeds the declared size",
                transfer_id=transfer_id,
                reason="size_exceeded",
            )
        state.spool.write(chunk)
        state.hasher.update(chunk)
        state.received += len(chunk)
        await self._maybe_progress(
            state,
            direction="upload",
            transferred=state.received,
            total=state.declared_size,
            force=state.received >= state.declared_size,
        )
        return {
            "transfer_id": transfer_id,
            "received": state.received,
            "size": state.declared_size,
        }

    async def upload_commit(
        self, environment_id: str, transfer_id: str, *, channel: Any
    ) -> Dict[str, Any]:
        state = self._upload_state(environment_id, transfer_id, channel)
        if state.received != state.declared_size:
            raise FsTransferError(
                f"received {state.received} bytes of the declared "
                f"{state.declared_size}",
                transfer_id=transfer_id,
                reason="size_mismatch",
            )
        computed = state.hasher.hexdigest()
        if state.declared_sha256 is not None and computed != state.declared_sha256:
            raise FsTransferError(
                "uploaded bytes do not match the declared sha256",
                transfer_id=transfer_id,
                reason="checksum_mismatch",
            )
        # Resolve the workspace again at commit: environment routing and substrate
        # confinement/policy may have changed while chunks were in flight.
        workspace = await self._workspace(environment_id)
        lock_key = (
            environment_id,
            type(workspace),
            str(workspace.root),
            canonical_relpath(state.path),
        )
        lock = self._upload_commit_locks.setdefault(lock_key, asyncio.Lock())
        self._upload_commit_lock_users[lock_key] = (
            self._upload_commit_lock_users.get(lock_key, 0) + 1
        )
        try:
            async with lock:
                try:
                    # Source/backend policy is authoritative and is checked
                    # before target existence or expected-content details are
                    # disclosed. It is re-evaluated under the publish lock in
                    # case policy or permissions changed during transfer.
                    await self._assert_writable(environment_id, workspace, state.path)
                    if isinstance(workspace, HostWorkspace):
                        # Re-resolve under the commit lock immediately before
                        # checking the target and publishing the staged bytes.
                        absolute = workspace.resolve(state.path)
                    else:
                        absolute = None

                    if state.expected_sha256 is not None:
                        current_sha256 = await self._target_sha256(
                            workspace, state.path, absolute=absolute
                        )
                        if current_sha256 != state.expected_sha256:
                            raise FsTransferError(
                                "target does not match expected_sha256",
                                transfer_id=transfer_id,
                                reason="precondition_failed",
                            )
                    elif not state.overwrite and await workspace.exists(state.path):
                        raise FsTransferError(
                            "target already exists",
                            transfer_id=transfer_id,
                            reason="target_exists",
                        )

                    if isinstance(workspace, HostWorkspace):
                        assert absolute is not None
                        spool = state.spool

                        def _write_host() -> None:
                            os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
                            spool.seek(0)
                            fd, staging = tempfile.mkstemp(
                                prefix=".omni-upload-",
                                dir=os.path.dirname(absolute) or ".",
                            )
                            try:
                                with os.fdopen(fd, "wb") as handle:
                                    for block in iter(
                                        lambda: spool.read(1024 * 1024), b""
                                    ):
                                        handle.write(block)
                                os.replace(staging, absolute)
                            except BaseException:
                                try:
                                    os.unlink(staging)
                                except OSError:
                                    pass
                                raise

                        await asyncio.to_thread(_write_host)
                    else:
                        state.spool.seek(0)
                        await workspace.write_bytes(
                            state.path, state.spool.read(), create_dirs=True
                        )
                except WorkspaceEscapeError as exc:
                    raise FsPathError(str(exc), path=state.path) from exc
        except WorkspaceEscapeError as exc:
            raise FsPathError(str(exc), path=state.path) from exc
        finally:
            users = self._upload_commit_lock_users[lock_key] - 1
            if users:
                self._upload_commit_lock_users[lock_key] = users
            else:
                self._upload_commit_lock_users.pop(lock_key, None)
                self._upload_commit_locks.pop(lock_key, None)
        self._close_upload(state)
        await self._maybe_progress(
            state,
            direction="upload",
            transferred=state.received,
            total=state.declared_size,
            force=True,
        )
        return {"path": state.path, "size": state.received, "sha256": computed}

    async def _target_sha256(
        self,
        workspace: Workspace,
        path: str,
        *,
        absolute: Optional[str],
    ) -> Optional[str]:
        """Return the current target digest, or ``None`` when it is absent.

        The caller holds the same-server commit lock. Host files stream into
        the hasher; generic substrates use their necessarily materialized read.
        Direct filesystem writers remain outside this RPC lock.
        """
        if isinstance(workspace, HostWorkspace):
            assert absolute is not None

            def _hash_host() -> Optional[str]:
                if not os.path.isfile(absolute):
                    return None
                hasher = hashlib.sha256()
                with open(absolute, "rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(block)
                return hasher.hexdigest()

            return await asyncio.to_thread(_hash_host)
        if not await workspace.is_file(path):
            return None
        return hashlib.sha256(await workspace.read_bytes(path)).hexdigest()

    async def upload_abort(
        self, environment_id: str, transfer_id: str, *, channel: Any
    ) -> bool:
        state = self._upload_state(environment_id, transfer_id, channel)
        self._close_upload(state)
        return True

    def _close_upload(self, state: _UploadState) -> None:
        self._uploads.pop(state.transfer_id, None)
        try:
            state.spool.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

    def cleanup_channel(self, channel: Any) -> None:
        """Tear down every watch/transfer owned by a disconnected channel."""
        for state in [w for w in self._watches.values() if w.channel is channel]:
            self._drop_watch(state)
        for download in [d for d in self._downloads.values() if d.channel is channel]:
            self._close_download(download)
        for upload in [u for u in self._uploads.values() if u.channel is channel]:
            self._close_upload(upload)

    def cleanup_environment(self, environment_id: str) -> None:
        """Tear down every watch/transfer bound to a deleted environment."""
        for state in [
            w for w in self._watches.values() if w.environment_id == environment_id
        ]:
            self._drop_watch(state)
        for download in [
            d for d in self._downloads.values() if d.environment_id == environment_id
        ]:
            self._close_download(download)
        for upload in [
            u for u in self._uploads.values() if u.environment_id == environment_id
        ]:
            self._close_upload(upload)

    def watch_count(self) -> int:
        return len(self._watches)

    def transfer_count(self) -> int:
        return len(self._downloads) + len(self._uploads)
