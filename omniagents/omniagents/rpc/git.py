"""Typed Git operations over an execution environment's workspace.

Backs the experimental ``git_*`` GUI protocol surface: repository
discovery, status, diff (with a **hunk-level** data model), log, branches,
worktrees, staging/unstaging/discarding by file *or* hunk, commit,
checkout, reset, fetch/pull/push and structured conflict inspection —
without any client ever parsing shell output.

Substrate model
---------------
Every git invocation goes through the
:class:`~omniagents.workspace.base.Workspace` abstraction — the same
execution-environment boundary the terminal and the ``fs_*`` surface use — so
the repository may live on the host filesystem, inside a Docker sandbox,
or on a remote backend without this module caring. Two consequences shape
the implementation:

* ``SandboxWorkspace.exec(cwd=..., shell=False)`` is unsupported, so we
  never pass ``cwd``: the repository is addressed with ``git -C <abs>``
  and every command is an argv list executed with ``shell=False``.
  **No code path in this module runs a shell.** Nothing is ever
  interpolated into a command string.
* Patch application needs a patch on stdin and :meth:`Workspace.exec` has
  no stdin, so the patch is written with :meth:`Workspace.write_text`
  into the repository's git directory — inside the workspace, and a
  place git never tracks — and handed to ``git apply`` as an argument.
  A substrate without a shell
  (:class:`~omniagents.workspace.pyodide.PyodideWorkspace`) answers
  ``-32095 git_capability_unavailable`` instead of failing obscurely.

Never parse localized text
--------------------------
Human-readable git output is *never* interpreted:

* status → ``--porcelain=v2 --branch -z`` (documented machine format),
* file-level diff metadata → ``--raw -z`` and ``--numstat -z``
  (``-``/``-`` is the structural binary marker),
* hunks → the unified-diff grammar (``@@`` headers and the fixed
  ``+``/``-``/`` ``/``\\`` line origins) with prefixes pinned to ``a/``
  and ``b/`` via ``--src-prefix``/``--dst-prefix``,
* worktrees → ``git worktree list --porcelain``,
* push results → ``git push --porcelain`` (the leading *flag character*
  carries the meaning; the trailing summary is passed through verbatim
  and labelled ``summary_raw`` because it is display text),
* unmerged stages → ``git ls-files -u -z``,
* repository state (merging / rebasing / …) → ``git rev-parse --verify``
  of the pseudo-refs, i.e. exit codes,
* conflict regions → the fixed ASCII conflict markers, which are part of
  git's file format, not of its message catalog.

Failures are exit codes plus verbatim stderr carried in typed error
``data`` — the message is for humans, the ``kind``/``exit_code`` are for
machines.

Scoping
-------
Operations are confined to repositories authorized by the environment
Workspace, on four independent facts checked in :meth:`_resolve_repo` —
including the **git directory**, not just the worktree, because a plain
file named ``.git`` can point a perfectly ordinary-looking checkout at
objects, refs and config outside the workspace. A composite host Workspace may
explicitly authorize the external metadata of a declared linked-worktree
source without exposing that metadata in the Files tree. ``git_list_repositories`` walks the workspace for ``.git``
entries; every other method takes the resulting ``repo`` id (the
workspace-relative POSIX path of the repository root) — there is no
implicit "the" repository, which is what makes multi-repository projects
unambiguous. A ``repo`` whose ``git rev-parse --show-toplevel`` resolves
outside the workspace root is refused: a workspace nested inside a larger
repository is not an operable repository. Worker worktrees created by
:class:`~omniagents.core.workers.isolation.WorktreeIsolation` live under
the framework scratch root, i.e. outside the workspace, so they are
*reported* by ``git_list_worktrees`` with ``accessible: false`` and are
not operable through this surface.

Destructive actions
-------------------
Discarding worktree changes, ``reset --hard``, amending a commit and
force-pushing never run on a bare request. They answer
``-32093 git_confirmation_required`` carrying a structured ``impact``
(what would be lost) and a single-use ``confirmation_token`` bound to
that exact impact. The client re-sends the token; if the repository moved
in between, the recomputed impact no longer matches and a *fresh*
confirmation is required. See :class:`ConfirmationBroker`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import logging
import posixpath
import re
import secrets
import time
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from omniagents.workspace.base import Workspace
from omniagents.workspace.types import (
    ExecResult,
    WorkspaceError,
    WorkspaceEscapeError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ConfirmationBroker",
    "DESTRUCTIVE_OPERATIONS",
    "DiffFile",
    "DiffHunk",
    "DiffLine",
    "GitCapabilityError",
    "GitConfirmationRequired",
    "GitManager",
    "GitOperationFailed",
    "GitPathError",
    "GitRepositoryNotFound",
    "GitRpcError",
    "GitStateInvalid",
    "RPC_ERROR_GIT_CAPABILITY_UNAVAILABLE",
    "RPC_ERROR_GIT_CONFIRMATION_REQUIRED",
    "RPC_ERROR_GIT_OPERATION_FAILED",
    "RPC_ERROR_GIT_PATH_INVALID",
    "RPC_ERROR_GIT_REPOSITORY_NOT_FOUND",
    "RPC_ERROR_GIT_STATE_INVALID",
    "build_patch",
    "canonical_repo_path",
    "local_remote_path",
    "parse_conflict_regions",
    "parse_numstat_z",
    "parse_patch_blocks",
    "parse_push_porcelain",
    "parse_raw_z",
    "parse_refspec_intent",
    "parse_status_v2",
    "parse_worktree_porcelain",
    "redact_remote_url",
]

#: JSON-RPC error codes reserved for the git surface (-3209x). -32010..76
#: are taken by earlier surfaces; -32080+ is reserved for the canonical
#: domain-object work landing in parallel.
RPC_ERROR_GIT_REPOSITORY_NOT_FOUND = -32090
RPC_ERROR_GIT_PATH_INVALID = -32091
RPC_ERROR_GIT_OPERATION_FAILED = -32092
RPC_ERROR_GIT_CONFIRMATION_REQUIRED = -32093
RPC_ERROR_GIT_STATE_INVALID = -32094
RPC_ERROR_GIT_CAPABILITY_UNAVAILABLE = -32095


@dataclass(frozen=True)
class _ResolvedRepo:
    """One validated, workspace-confined repository.

    ``git_dir_abs`` is the substrate-absolute per-worktree git directory —
    the thing that owns ``index.lock`` — and is what mutating operations
    serialize on.
    """

    workspace: Workspace
    repo_id: str
    root_abs: str
    git_dir_abs: str
    git_dir_rel: str

    def unpack(self) -> Tuple[Workspace, str, str, str]:
        return self.workspace, self.repo_id, self.root_abs, self.git_dir_rel


@dataclass
class _StatusWatchState:
    """One channel-bound repository-status watch (``git_status_watch``)."""

    watch_id: str
    environment_id: str
    repo: str
    channel: Any
    workspace: Workspace
    repo_abs: str
    interval: float
    digest: str
    task: Optional["asyncio.Task[None]"] = None
    closed: bool = False


class GitRpcError(Exception):
    """Typed ``git_*`` error surfaced to the wire verbatim."""

    code: int = RPC_ERROR_GIT_STATE_INVALID
    kind: str = "git_state_invalid"

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self._data = {key: value for key, value in data.items() if value is not None}

    @property
    def data(self) -> Dict[str, Any]:
        return {"kind": self.kind, **self._data}


class GitRepositoryNotFound(GitRpcError):
    code = RPC_ERROR_GIT_REPOSITORY_NOT_FOUND
    kind = "git_repository_not_found"


class GitPathError(GitRpcError):
    code = RPC_ERROR_GIT_PATH_INVALID
    kind = "git_path_invalid"


class GitOperationFailed(GitRpcError):
    code = RPC_ERROR_GIT_OPERATION_FAILED
    kind = "git_operation_failed"


class GitConfirmationRequired(GitRpcError):
    code = RPC_ERROR_GIT_CONFIRMATION_REQUIRED
    kind = "git_confirmation_required"


class GitStateInvalid(GitRpcError):
    code = RPC_ERROR_GIT_STATE_INVALID
    kind = "git_state_invalid"


class GitCapabilityError(GitRpcError):
    code = RPC_ERROR_GIT_CAPABILITY_UNAVAILABLE
    kind = "git_capability_unavailable"


#: Operations that can destroy work and therefore require an explicit,
#: impact-bound confirmation token before they run. Keyed by the wire
#: method name; the value is the confirmation "operation" label.
DESTRUCTIVE_OPERATIONS = {
    "git_discard": "discard_changes",
    "git_reset_hard": "reset_hard",
    "git_checkout_discard": "checkout_discard_changes",
    "git_push_force": "force_push",
    "git_push_delete": "delete_remote_ref",
    "git_commit_amend": "amend_commit",
}


# ---------------------------------------------------------------------------
#  Path handling
# ---------------------------------------------------------------------------


def canonical_repo_path(path: str, *, allow_root: bool = True) -> str:
    """Canonicalize one wire path into a relative POSIX path.

    Same strictness as the ``fs_*`` surface: no NUL bytes, no backslashes,
    no absolute or drive-letter paths, no ``..`` above the anchor. ``.``
    and the empty string mean the anchor itself when *allow_root*.
    """
    if not isinstance(path, str):
        raise GitPathError("path must be a string")
    trimmed = path.strip()
    if "\x00" in trimmed:
        raise GitPathError("path contains a NUL byte")
    if "\\" in trimmed:
        raise GitPathError("path must use POSIX separators", path=path)
    if trimmed in ("", "."):
        if not allow_root:
            raise GitPathError("a file path is required", path=path)
        return "."
    if trimmed.startswith("/") or posixpath.isabs(trimmed):
        raise GitPathError("absolute paths are not allowed", path=path)
    if len(trimmed) >= 2 and trimmed[1] == ":" and trimmed[0].isalpha():
        raise GitPathError("drive-letter paths are not allowed", path=path)
    stack: List[str] = []
    for segment in trimmed.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not stack:
                raise GitPathError("path escapes the repository root", path=path)
            stack.pop()
            continue
        stack.append(segment)
    if not stack:
        if not allow_root:
            raise GitPathError("a file path is required", path=path)
        return "."
    return "/".join(stack)


async def _no_sources(_environment_id: str) -> Sequence[Mapping[str, Any]]:
    """Default source policy for direct library use without an AgentHost."""

    return ()


def _redacted_source(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A published ``--source`` descriptor safe to put on the wire."""
    if source is None:
        return None
    out = dict(source)
    out.pop("gitDir", None)
    out.pop("gitCommonDir", None)
    out.pop("auth", None)
    if "repoUrl" in out:
        out["repoUrl"] = redact_remote_url(out.get("repoUrl"))
    return out


def _join_workspace(repo_rel: str, path_rel: str) -> str:
    """Workspace-relative path of *path_rel* inside repository *repo_rel*."""
    if repo_rel == ".":
        return path_rel
    if path_rel == ".":
        return repo_rel
    return f"{repo_rel}/{path_rel}"


# ---------------------------------------------------------------------------
#  Machine-format parsers (pure)
# ---------------------------------------------------------------------------

#: ``git diff --raw`` status letters → the wire ``change`` vocabulary.
_RAW_STATUS = {
    "A": "added",
    "C": "copied",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type_changed",
    "U": "unmerged",
    "X": "unknown",
}

#: ``git status --porcelain=v2`` XY letters → the wire vocabulary.
_XY_STATUS = {
    ".": "unmodified",
    "M": "modified",
    "T": "type_changed",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "unmerged",
}

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<heading>.*)$"
)

_CONFLICT_OURS = re.compile(r"^<{7,}(?: (?P<label>.*))?$")
_CONFLICT_BASE = re.compile(r"^\|{7,}(?: (?P<label>.*))?$")
_CONFLICT_SPLIT = re.compile(r"^={7,}$")
_CONFLICT_THEIRS = re.compile(r"^>{7,}(?: (?P<label>.*))?$")


def _split_nul(text: str) -> List[str]:
    records = text.split("\0")
    if records and records[-1] == "":
        records.pop()
    return records


@dataclass(frozen=True)
class RawRecord:
    """One ``git diff --raw -z`` record."""

    change: str
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str
    path: str
    old_path: Optional[str]
    similarity: Optional[int]


def parse_raw_z(text: str) -> List[RawRecord]:
    """Parse ``git diff --raw -z`` output.

    Record grammar: ``:<omode> <nmode> <ooid> <noid> <status><score>NUL
    <path>NUL[<newpath>NUL]``. Rename/copy statuses carry the extra path.
    """
    fields = _split_nul(text)
    records: List[RawRecord] = []
    index = 0
    while index < len(fields):
        info = fields[index]
        index += 1
        if not info.startswith(":"):
            raise ValueError(f"malformed --raw record: {info!r}")
        parts = info[1:].split(" ")
        if len(parts) != 5:
            raise ValueError(f"malformed --raw record: {info!r}")
        old_mode, new_mode, old_oid, new_oid, status = parts
        letter = status[0]
        score = status[1:]
        if index >= len(fields):
            raise ValueError("truncated --raw record: missing path")
        first_path = fields[index]
        index += 1
        old_path: Optional[str] = None
        path = first_path
        if letter in ("R", "C"):
            if index >= len(fields):
                raise ValueError("truncated --raw record: missing rename target")
            old_path = first_path
            path = fields[index]
            index += 1
        records.append(
            RawRecord(
                change=_RAW_STATUS.get(letter, "unknown"),
                old_mode=old_mode,
                new_mode=new_mode,
                old_oid=old_oid,
                new_oid=new_oid,
                path=path,
                old_path=old_path,
                similarity=int(score) if score.isdigit() else None,
            )
        )
    return records


def parse_numstat_z(text: str) -> Dict[str, Tuple[Optional[int], Optional[int]]]:
    """Parse ``git diff --numstat -z`` into ``path -> (added, deleted)``.

    ``(None, None)`` marks a binary file — git writes ``-`` for both
    counts, which is the *structural* binary signal (the ``Binary files …
    differ`` line in the patch is display text and is never read).
    """
    fields = _split_nul(text)
    out: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        parts = record.split("\t")
        if len(parts) < 3:
            raise ValueError(f"malformed --numstat record: {record!r}")
        added_raw, deleted_raw, inline_path = parts[0], parts[1], parts[2]
        added = None if added_raw == "-" else int(added_raw)
        deleted = None if deleted_raw == "-" else int(deleted_raw)
        if inline_path == "":
            # Rename/copy: the old and new paths follow as separate
            # NUL-terminated fields.
            if index + 1 >= len(fields):
                raise ValueError("truncated --numstat rename record")
            index += 1  # old path — the new path is the key
            path = fields[index]
            index += 1
        else:
            path = inline_path
        out[path] = (added, deleted)
    return out


@dataclass
class PatchBlock:
    """One ``diff --git`` / ``diff --cc`` block of a unified diff."""

    #: Header lines from ``diff --git`` up to (excluding) the first ``@@``.
    header_lines: List[str] = field(default_factory=list)
    #: Raw ``@@`` hunk bodies, header line first.
    hunks: List[List[str]] = field(default_factory=list)
    #: True for ``diff --cc``/``diff --combined`` blocks (unmerged paths).
    combined: bool = False


def parse_patch_blocks(patch: str) -> List[PatchBlock]:
    """Split unified-diff text into per-file blocks.

    A file boundary is a line starting with ``diff --git ``,
    ``diff --cc `` or ``diff --combined `` at column 0. Body lines always
    start with one of ``+``/``-``/`` ``/``\\``/``@`` so they can never be
    mistaken for a boundary.
    """
    blocks: List[PatchBlock] = []
    current: Optional[PatchBlock] = None
    in_hunk = False
    for line in patch.split("\n"):
        if (
            line.startswith("diff --git ")
            or line.startswith("diff --cc ")
            or line.startswith("diff --combined ")
        ):
            current = PatchBlock(combined=not line.startswith("diff --git "))
            current.header_lines.append(line)
            blocks.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            current.hunks.append([line])
            in_hunk = True
            continue
        if in_hunk:
            # ``diff.suppressBlankEmpty`` is pinned to false, so a blank
            # context line is always a single space; a truly empty line is
            # only ever the artifact of splitting a trailing newline.
            if line != "":
                current.hunks[-1].append(line)
        else:
            current.header_lines.append(line)
    return blocks


@dataclass
class DiffLine:
    origin: str  # context | add | delete | no_newline
    content: str
    old_lineno: Optional[int]
    new_lineno: Optional[int]

    def to_wire(self) -> Dict[str, Any]:
        return {
            "origin": self.origin,
            "content": self.content,
            "old_lineno": self.old_lineno,
            "new_lineno": self.new_lineno,
        }


@dataclass
class DiffHunk:
    index: int
    hunk_id: str
    header: str
    section_heading: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: List[DiffLine]
    #: Verbatim hunk body (header line first) — replayed into patches.
    raw: List[str]

    def to_wire(self) -> Dict[str, Any]:
        return {
            "hunk_id": self.hunk_id,
            "index": self.index,
            "header": self.header,
            "section_heading": self.section_heading,
            "old_start": self.old_start,
            "old_lines": self.old_lines,
            "new_start": self.new_start,
            "new_lines": self.new_lines,
            "lines": [line.to_wire() for line in self.lines],
        }


@dataclass
class DiffFile:
    path: str
    old_path: Optional[str]
    change: str
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str
    similarity: Optional[int]
    binary: bool
    added_lines: Optional[int]
    deleted_lines: Optional[int]
    unmerged: bool
    submodule: bool
    hunks: List[DiffHunk]
    header_lines: List[str]

    def to_wire(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "old_path": self.old_path,
            "change": self.change,
            "old_mode": self.old_mode,
            "new_mode": self.new_mode,
            "old_oid": self.old_oid,
            "new_oid": self.new_oid,
            "similarity": self.similarity,
            "binary": self.binary,
            "added_lines": self.added_lines,
            "deleted_lines": self.deleted_lines,
            "unmerged": self.unmerged,
            "submodule": self.submodule,
            "hunk_selectable": hunk_selection_supported(self),
            "hunks": [hunk.to_wire() for hunk in self.hunks],
        }


def hunk_selection_supported(diff_file: DiffFile) -> bool:
    """Whether individual hunks of *diff_file* may be staged/discarded.

    Text files with hunks qualify. Binary blobs, submodule pointers,
    unmerged paths and type changes have no single coherent line-level
    representation — a type change is two whole-file patches, not one —
    so they are file-level only. Clients see this as
    ``hunk_selectable: false`` rather than discovering it through a
    failure.
    """
    return bool(
        diff_file.hunks
        and not diff_file.binary
        and not diff_file.submodule
        and not diff_file.unmerged
        and diff_file.change != "type_changed"
    )


def _hunk_identity(scope: str, path: str, header: str, body: Sequence[str]) -> str:
    """Content-addressed hunk id.

    Includes the diff *scope* so a ``staged`` hunk id can never be
    replayed against the ``worktree`` diff, and the full body so an id
    stops resolving the moment the underlying change moves — which is
    what makes stale selections fail closed instead of applying
    somewhere unintended.
    """
    digest = hashlib.sha256()
    digest.update(scope.encode())
    digest.update(b"\0")
    digest.update(path.encode())
    digest.update(b"\0")
    digest.update(header.encode())
    for line in body:
        digest.update(b"\0")
        digest.update(line.encode())
    return digest.hexdigest()[:16]


def _parse_hunk(scope: str, path: str, index: int, raw: Sequence[str]) -> DiffHunk:
    match = _HUNK_HEADER.match(raw[0])
    if match is None:
        raise ValueError(f"malformed hunk header: {raw[0]!r}")
    old_start = int(match.group("old_start"))
    old_lines = int(match.group("old_lines") or 1)
    new_start = int(match.group("new_start"))
    new_lines = int(match.group("new_lines") or 1)
    heading = match.group("heading") or ""
    if heading.startswith(" "):
        heading = heading[1:]
    lines: List[DiffLine] = []
    old_cursor = old_start
    new_cursor = new_start
    for entry in raw[1:]:
        if entry == "":
            continue
        marker, content = entry[0], entry[1:]
        if marker == " ":
            lines.append(DiffLine("context", content, old_cursor, new_cursor))
            old_cursor += 1
            new_cursor += 1
        elif marker == "+":
            lines.append(DiffLine("add", content, None, new_cursor))
            new_cursor += 1
        elif marker == "-":
            lines.append(DiffLine("delete", content, old_cursor, None))
            old_cursor += 1
        elif marker == "\\":
            lines.append(DiffLine("no_newline", content.strip(), None, None))
        else:
            raise ValueError(f"unexpected diff line origin: {entry[:1]!r}")
    return DiffHunk(
        index=index,
        hunk_id=_hunk_identity(scope, path, raw[0], raw[1:]),
        header=raw[0],
        section_heading=heading,
        old_start=old_start,
        old_lines=old_lines,
        new_start=new_start,
        new_lines=new_lines,
        lines=lines,
        raw=list(raw),
    )


def assemble_diff(
    *,
    scope: str,
    raw_records: Sequence[RawRecord],
    numstat: Dict[str, Tuple[Optional[int], Optional[int]]],
    blocks: Sequence[PatchBlock],
) -> List[DiffFile]:
    """Join ``--raw``/``--numstat``/``-p`` output into typed file diffs.

    ``--raw`` is authoritative for paths, modes and change kind (it is
    NUL-delimited, so no quoting ambiguity); the patch supplies the
    hunks and is associated **by position**, which git guarantees is the
    same ordering.

    Positions are not one-to-one, though: a **type change** (``T`` — a
    file replaced by a symlink, or the reverse) is a single ``--raw``
    record but *two* ``diff --git`` blocks, a whole-file deletion
    followed by a whole-file creation. Consuming only one would shift
    every later file onto the wrong hunks, so the record claims both.
    The final alignment is checked, never assumed.
    """
    files: List[DiffFile] = []
    cursor = 0
    for record in raw_records:
        # A type change is rendered as delete-then-create; everything
        # else is exactly one block.
        wanted = 2 if record.change == "type_changed" else 1
        if cursor + wanted > len(blocks):
            raise ValueError(
                "diff association failed: "
                f"{len(raw_records)} raw records vs {len(blocks)} patch blocks"
            )
        owned = list(blocks[cursor : cursor + wanted])
        cursor += wanted
        added, deleted = numstat.get(record.path, (None, None))
        binary = added is None and deleted is None and record.path in numstat
        submodule = "160000" in (record.old_mode, record.new_mode)
        combined = any(block.combined for block in owned)
        header_lines: List[str] = []
        for block in owned:
            header_lines.extend(block.header_lines)
        hunks: List[DiffHunk] = []
        if not combined and not binary:
            position = 0
            for block in owned:
                for raw in block.hunks:
                    hunks.append(_parse_hunk(scope, record.path, position, raw))
                    position += 1
        files.append(
            DiffFile(
                path=record.path,
                old_path=record.old_path,
                change=record.change,
                old_mode=record.old_mode,
                new_mode=record.new_mode,
                old_oid=record.old_oid,
                new_oid=record.new_oid,
                similarity=record.similarity,
                binary=binary,
                added_lines=added,
                deleted_lines=deleted,
                unmerged=combined or record.change == "unmerged",
                submodule=submodule,
                hunks=hunks,
                header_lines=header_lines,
            )
        )
    if cursor != len(blocks):
        raise ValueError(
            "diff association failed: "
            f"{len(raw_records)} raw records vs {len(blocks)} patch blocks"
        )
    return files


# ---------------------------------------------------------------------------
#  Patch construction for hunk-level operations
# ---------------------------------------------------------------------------


def build_patch(
    selections: Sequence[Tuple[DiffFile, Sequence[DiffHunk]]],
    *,
    direction: str,
) -> str:
    """Render a unified diff containing only the selected hunks.

    The subtle part is line numbering. Git applies a patch against a
    *preimage* it must match exactly:

    * ``direction="forward"`` (staging worktree hunks into the index):
      the preimage is the diff's **old** side, which is untouched, so
      ``old_start`` is preserved and each included hunk's ``new_start``
      shifts by the cumulative size change of the hunks included before
      it.
    * ``direction="reverse"`` (``git apply -R`` — unstaging, discarding):
      the preimage is the diff's **new** side, so ``new_start`` is
      preserved and ``old_start`` shifts by the negated cumulative size
      change instead.

    Dropping a hunk therefore never leaves a patch whose remaining hunks
    claim line numbers that no longer exist.
    """
    if direction not in ("forward", "reverse"):
        raise ValueError(f"unknown patch direction: {direction!r}")
    out: List[str] = []
    for diff_file, hunks in selections:
        if not hunks:
            continue
        out.extend(diff_file.header_lines)
        delta = 0
        for hunk in hunks:
            if direction == "forward":
                old_start = hunk.old_start
                new_start = hunk.old_start + delta
            else:
                new_start = hunk.new_start
                old_start = hunk.new_start - delta
            out.append(
                _render_hunk_header(
                    old_start,
                    hunk.old_lines,
                    new_start,
                    hunk.new_lines,
                    hunk.section_heading,
                )
            )
            out.extend(hunk.raw[1:])
            delta += hunk.new_lines - hunk.old_lines
    if not out:
        return ""
    return "\n".join(out) + "\n"


def _render_hunk_header(
    old_start: int, old_lines: int, new_start: int, new_lines: int, heading: str
) -> str:
    old_span = f"{old_start}" if old_lines == 1 else f"{old_start},{old_lines}"
    new_span = f"{new_start}" if new_lines == 1 else f"{new_start},{new_lines}"
    suffix = f" {heading}" if heading else ""
    return f"@@ -{old_span} +{new_span} @@{suffix}"


# ---------------------------------------------------------------------------
#  Status / worktree / push parsers
# ---------------------------------------------------------------------------


def parse_status_v2(text: str) -> Dict[str, Any]:
    """Parse ``git status --porcelain=v2 --branch -z``.

    Every token here is part of git's documented machine format: the
    ``# branch.*`` headers, the ``1``/``2``/``u``/``?``/``!`` record
    kinds, and the two-letter XY code. ``(detached)`` is the documented
    constant ``branch.head`` takes when HEAD is not on a branch.
    """
    head: Dict[str, Any] = {
        "detached": False,
        "unborn": False,
        "branch": None,
        "oid": None,
    }
    upstream: Optional[Dict[str, Any]] = None
    entries: List[Dict[str, Any]] = []
    untracked: List[str] = []
    ignored: List[str] = []
    stash_count = 0

    fields = _split_nul(text)
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if record.startswith("# "):
            key, _, value = record[2:].partition(" ")
            if key == "branch.oid":
                head["oid"] = None if value == "(initial)" else value
                head["unborn"] = value == "(initial)"
            elif key == "branch.head":
                if value == "(detached)":
                    head["detached"] = True
                else:
                    head["branch"] = value
            elif key == "branch.upstream":
                upstream = {"name": value, "ahead": 0, "behind": 0}
            elif key == "branch.ab":
                ahead_token, _, behind_token = value.partition(" ")
                if upstream is not None:
                    upstream["ahead"] = int(ahead_token.lstrip("+") or 0)
                    upstream["behind"] = int(behind_token.lstrip("-") or 0)
            elif key == "stash":
                stash_count = int(value or 0)
            continue
        kind, _, rest = record.partition(" ")
        if kind == "?":
            untracked.append(rest)
        elif kind == "!":
            ignored.append(rest)
        elif kind in ("1", "2"):
            # ``1 XY sub mH mI mW hH hI path`` (8 fields) and
            # ``2 XY sub mH mI mW hH hI X<score> path`` (9 fields).
            parts = rest.split(" ", 8 if kind == "2" else 7)
            xy = parts[0]
            entry: Dict[str, Any] = {
                "path": parts[-1],
                "orig_path": None,
                "xy": xy,
                "index_status": _XY_STATUS.get(xy[0], "unknown"),
                "worktree_status": _XY_STATUS.get(xy[1], "unknown"),
                "staged": xy[0] != ".",
                "unstaged": xy[1] != ".",
                "submodule": parts[1] != "N...",
                "similarity": None,
                "unmerged": None,
            }
            if kind == "2":
                score = parts[-2]
                entry["similarity"] = (
                    int(score[1:]) if len(score) > 1 and score[1:].isdigit() else None
                )
                if index < len(fields):
                    entry["orig_path"] = fields[index]
                    index += 1
            entries.append(entry)
        elif kind == "u":
            # ``u XY sub m1 m2 m3 mW h1 h2 h3 path`` (10 fields).
            parts = rest.split(" ", 9)
            xy = parts[0]
            entries.append(
                {
                    "path": parts[-1],
                    "orig_path": None,
                    "xy": xy,
                    "index_status": _XY_STATUS.get(xy[0], "unknown"),
                    "worktree_status": _XY_STATUS.get(xy[1], "unknown"),
                    "staged": False,
                    "unstaged": True,
                    "submodule": parts[1] != "N...",
                    "similarity": None,
                    "unmerged": {
                        "base_mode": parts[2],
                        "ours_mode": parts[3],
                        "theirs_mode": parts[4],
                        "worktree_mode": parts[5],
                        "base_oid": parts[6],
                        "ours_oid": parts[7],
                        "theirs_oid": parts[8],
                    },
                }
            )
    conflicted = sorted(entry["path"] for entry in entries if entry["unmerged"])
    return {
        "head": head,
        "upstream": upstream,
        "entries": entries,
        "untracked": untracked,
        "ignored": ignored,
        "conflicted": conflicted,
        "stash_count": stash_count,
        "clean": not entries and not untracked,
    }


def parse_worktree_porcelain(text: str) -> List[Dict[str, Any]]:
    """Parse ``git worktree list --porcelain`` into typed records."""
    worktrees: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in text.split("\n"):
        if line == "":
            current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = {
                "path": value,
                "head": None,
                "branch": None,
                "detached": False,
                "bare": False,
                "locked": False,
                "lock_reason": None,
                "prunable": False,
            }
            worktrees.append(current)
            continue
        if current is None:
            continue
        if key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value
        elif key == "detached":
            current["detached"] = True
        elif key == "bare":
            current["bare"] = True
        elif key == "locked":
            current["locked"] = True
            current["lock_reason"] = value or None
        elif key == "prunable":
            current["prunable"] = True
    return worktrees


#: ``git push --porcelain`` leading flag characters (documented, and the
#: only part of the line whose meaning is machine-defined).
_PUSH_FLAGS = {
    " ": "fast_forward",
    "+": "forced_update",
    "-": "deleted",
    "*": "created",
    "!": "rejected",
    "=": "up_to_date",
}


def parse_push_porcelain(text: str) -> Dict[str, Any]:
    """Parse ``git push --porcelain`` output.

    Grammar: a ``To <url>`` line, then ``<flag>\\t<from>:<to>\\t<summary>``
    per ref, then ``Done``. Meaning comes from *flag*; ``summary`` is
    display text and is passed through as ``summary_raw`` without ever
    being interpreted.
    """
    remote_url: Optional[str] = None
    updates: List[Dict[str, Any]] = []
    for line in text.split("\n"):
        if line.startswith("To "):
            remote_url = line[3:].strip()
            continue
        if line in ("", "Done"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        flag = parts[0][:1]
        source, _, destination = parts[1].partition(":")
        updates.append(
            {
                "flag": flag,
                "status": _PUSH_FLAGS.get(flag, "unknown"),
                "rejected": flag == "!",
                "source_ref": source,
                "target_ref": destination,
                "summary_raw": parts[2] if len(parts) > 2 else "",
                "reason_raw": parts[3] if len(parts) > 3 else None,
            }
        )
    return {"remote_url": remote_url, "updates": updates}


def redact_remote_url(url: Optional[str]) -> Optional[str]:
    """Drop the userinfo from a remote URL before it goes back on the wire.

    ``https://user:token@host/x.git`` and ``https://ghp_xxx@host/x.git``
    both carry a credential in the userinfo, and a token is
    indistinguishable from a username there — so the whole userinfo goes.
    The scp-like ``git@host:path`` form is left alone: it has no place to
    put a password, and rewriting it would change what the remote *is*.
    """
    if not isinstance(url, str) or not url.strip():
        return url
    text = url.strip()
    scheme, separator, rest = text.partition("://")
    if not separator or "@" not in rest:
        return text
    _userinfo, _, remainder = rest.partition("@")
    return f"{scheme}://{remainder}"


def local_remote_path(url: str) -> Optional[str]:
    """The filesystem path a remote URL refers to, or ``None`` if remote.

    Git treats a bare path and a ``file://`` URL as local; everything
    with a transport (``https``, ``ssh``, ``git``, ``ext``, or the
    ``host:path`` scp-like form) reaches a different machine and crosses
    no workspace boundary.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    text = url.strip()
    if text.startswith("file://"):
        return text[len("file://") :] or "/"
    scheme, separator, rest = text.partition("://")
    if separator:
        return None
    if text.startswith("ext::") or text.startswith("transport::"):
        return None
    # scp-like ``[user@]host:path`` — a colon before any slash.
    colon = text.find(":")
    if colon != -1 and ("/" not in text[:colon]):
        return None
    return text


def parse_refspec_intent(refspec: Optional[str]) -> Dict[str, Any]:
    """Destructive intent encoded in a push refspec.

    ``<src>:<dst>`` with a leading ``+`` is a forced update; an **empty**
    source side (``:refs/heads/x``) deletes the remote ref. Both are as
    destructive as the ``force``/``--delete`` flags and must be gated the
    same way — reading only the boolean parameters let them through.
    """
    out: Dict[str, Any] = {"force": False, "delete": False, "target": None}
    if not isinstance(refspec, str) or not refspec.strip():
        return out
    spec = refspec.strip()
    if spec.startswith("+"):
        out["force"] = True
        spec = spec[1:]
    if ":" in spec:
        source, _, target = spec.partition(":")
        out["target"] = target or None
        if source.strip() == "":
            out["delete"] = True
    return out


def parse_conflict_regions(text: str) -> List[Dict[str, Any]]:
    """Parse conflict-marker regions out of a merged working-tree file.

    Markers (``<<<<<<<``, ``|||||||``, ``=======``, ``>>>>>>>``) are part
    of git's *file format*, not its message catalog: they are fixed ASCII
    and unaffected by locale. Both ``merge`` and ``diff3`` conflict styles
    are handled; the label after a marker is carried through verbatim.
    """
    regions: List[Dict[str, Any]] = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        opener = _CONFLICT_OURS.match(lines[index])
        if opener is None:
            index += 1
            continue
        start = index
        ours: List[str] = []
        base: Optional[List[str]] = None
        theirs: List[str] = []
        ours_label = opener.group("label") or ""
        base_label: Optional[str] = None
        theirs_label = ""
        section = "ours"
        index += 1
        closed = False
        while index < len(lines):
            line = lines[index]
            base_marker = _CONFLICT_BASE.match(line)
            theirs_marker = _CONFLICT_THEIRS.match(line)
            if base_marker is not None and section == "ours":
                base = []
                base_label = base_marker.group("label") or ""
                section = "base"
            elif _CONFLICT_SPLIT.match(line) and section in ("ours", "base"):
                section = "theirs"
            elif theirs_marker is not None and section == "theirs":
                theirs_label = theirs_marker.group("label") or ""
                closed = True
                index += 1
                break
            elif section == "ours":
                ours.append(line)
            elif section == "base" and base is not None:
                base.append(line)
            else:
                theirs.append(line)
            index += 1
        if not closed:
            break
        regions.append(
            {
                "start_line": start + 1,
                "end_line": index,
                "ours_label": ours_label,
                "base_label": base_label,
                "theirs_label": theirs_label,
                "ours": ours,
                "base": base,
                "theirs": theirs,
            }
        )
    return regions


# ---------------------------------------------------------------------------
#  Confirmation broker
# ---------------------------------------------------------------------------


def impact_digest(payload: Any) -> str:
    """Stable digest of a structured impact description."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class _PendingConfirmation:
    environment_id: str
    repo: str
    operation: str
    digest: str
    expires_at: float


class ConfirmationBroker:
    """Mints and redeems single-use, impact-bound confirmation tokens.

    A token is only redeemable for the exact ``(environment, repo, operation,
    impact)`` it was minted for. Because the impact is recomputed from
    live repository state at redemption time, a repository that moved
    between the two calls invalidates the token — the client is shown the
    new impact and must confirm again. Tokens are single-use and expire.
    """

    def __init__(self, *, ttl_seconds: float = 300.0, max_pending: int = 256) -> None:
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._pending: Dict[str, _PendingConfirmation] = {}

    def mint(self, environment_id: str, repo: str, operation: str, impact: Any) -> str:
        self._prune()
        if len(self._pending) >= self._max_pending:
            oldest = min(self._pending, key=lambda key: self._pending[key].expires_at)
            self._pending.pop(oldest, None)
        token = secrets.token_urlsafe(24)
        self._pending[token] = _PendingConfirmation(
            environment_id=environment_id,
            repo=repo,
            operation=operation,
            digest=impact_digest(impact),
            expires_at=time.monotonic() + self._ttl,
        )
        return token

    def redeem(
        self, token: str, environment_id: str, repo: str, operation: str, impact: Any
    ) -> bool:
        """Consume *token*. Returns False for unknown/expired/mismatched."""
        self._prune()
        pending = self._pending.pop(token, None)
        if pending is None:
            return False
        return (
            pending.environment_id == environment_id
            and pending.repo == repo
            and pending.operation == operation
            and pending.digest == impact_digest(impact)
        )

    def _prune(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._pending.items() if v.expires_at < now]:
            self._pending.pop(key, None)


# ---------------------------------------------------------------------------
#  Manager
# ---------------------------------------------------------------------------

#: Config overrides forced onto every invocation. They neutralize
#: user/repo configuration that would otherwise corrupt machine output
#: (path quoting, color, pagers, mnemonic diff prefixes).
_GIT_CONFIG_ARGS: Tuple[str, ...] = (
    "-c",
    "core.quotepath=false",
    "-c",
    "color.ui=false",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.noprefix=false",
    "-c",
    "diff.mnemonicprefix=false",
    "-c",
    "diff.external=",
    "-c",
    "diff.suppressblankempty=false",
    # ``ext::`` turns a remote URL into a shell command. Pinned off on the
    # command line, which outranks repository-local config, so a
    # ``protocol.ext.allow=always`` written into the workspace cannot
    # re-enable command execution on a surface that runs no shell.
    "-c",
    "protocol.ext.allow=never",
)

#: Diff arguments shared by every diff invocation. Prefixes are pinned so
#: captured header lines can be replayed into ``git apply`` verbatim.
_NULL_OID = "0" * 40

_DIFF_COMMON: Tuple[str, ...] = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--find-renames",
)

_DIFF_SCOPES = {
    "worktree": ("--diff-filter=u",),
    "staged": ("--cached", "--diff-filter=u"),
    "head": ("HEAD", "--diff-filter=u"),
}


class GitManager:
    """Typed git operations for environment-scoped workspaces.

    Entry points take the resource-owning ``environment_id`` (resolved and
    authorized by the caller) and a ``repo`` id from :meth:`list_repositories`.
    All state is derived from the repository on each call except confirmation
    tokens, which live in :class:`ConfirmationBroker`.
    """

    def __init__(
        self,
        *,
        workspace_resolver: Callable[[str], Awaitable[Workspace]],
        read_timeout: float = 30.0,
        write_timeout: float = 60.0,
        network_timeout: float = 300.0,
        max_output_bytes: int = 16 * 1024 * 1024,
        max_discovery_depth: int = 4,
        max_repositories: int = 64,
        default_context_lines: int = 3,
        max_context_lines: int = 64,
        default_log_count: int = 50,
        max_log_count: int = 1000,
        max_conflict_blob_bytes: int = 1024 * 1024,
        max_conflict_content_paths: int = 25,
        confirmation_ttl: float = 300.0,
        source_provider: Optional[
            Callable[[str], Awaitable[Sequence[Mapping[str, Any]]]]
        ] = None,
        status_watch_interval: float = 2.0,
        min_status_watch_interval: float = 0.2,
        max_status_watch_interval: float = 30.0,
        max_status_watches_per_connection: int = 8,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout
        self._network_timeout = network_timeout
        self._max_output_bytes = max_output_bytes
        self._max_discovery_depth = max_discovery_depth
        self._max_repositories = max_repositories
        self._default_context_lines = default_context_lines
        self._max_context_lines = max_context_lines
        self._default_log_count = default_log_count
        self._max_log_count = max_log_count
        self._max_conflict_blob_bytes = max_conflict_blob_bytes
        self._max_conflict_content_paths = max_conflict_content_paths
        self._source_provider = source_provider or _no_sources
        self._status_watch_interval = status_watch_interval
        self._min_status_watch_interval = min_status_watch_interval
        self._max_status_watch_interval = max_status_watch_interval
        self._max_status_watches_per_connection = max_status_watches_per_connection
        self._status_watches: Dict[str, _StatusWatchState] = {}
        self.confirmations = ConfirmationBroker(ttl_seconds=confirmation_ttl)
        # One lock per *physical* git directory — the thing that actually
        # owns ``index.lock``. Keying on (environment, repo) instead would give
        # two environments sharing one workspace independent locks and let them
        # race each other, and it would grow an entry per distinct string a
        # caller passed as ``repo``. The key is therefore taken from the
        # resolved, validated absolute git dir, never from wire input, and
        # entries are pruned once nobody is waiting on them.
        self._repo_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    #  Plumbing
    # ------------------------------------------------------------------

    async def _workspace(self, environment_id: str) -> Workspace:
        return await self._workspace_resolver(environment_id)

    @asynccontextmanager
    async def _serialized(
        self, environment_id: str, repo: str
    ) -> "AsyncIterator[_ResolvedRepo]":
        """Resolve *repo*, then hold the lock on its physical git directory.

        Resolution comes first and deliberately so: it is read-only, it is
        what proves the repository is inside the workspace, and its result
        is the only trustworthy lock key. Locking on the raw ``repo``
        parameter instead would (a) let a caller mint an unbounded number
        of lock objects out of unvalidated strings and (b) hand two
        environments that share one workspace two different locks for one
        index.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        lock = self._repo_locks.get(resolved.git_dir_abs)
        if lock is None:
            lock = asyncio.Lock()
            self._repo_locks[resolved.git_dir_abs] = lock
        try:
            async with lock:
                yield resolved
        finally:
            # Nobody is queued behind it, so the entry is dead weight.
            if not lock.locked() and not lock._waiters:  # type: ignore[attr-defined]
                self._repo_locks.pop(resolved.git_dir_abs, None)

    @staticmethod
    def _is_index_lock_failure(result: ExecResult) -> bool:
        """Whether a failed git command lost to ``index.lock``.

        The signal is the *filename* ``index.lock`` appearing in the path
        git prints back — a path is never translated, so this holds in
        every locale, unlike the sentence around it. Re-probing the
        filesystem for the lockfile instead would be racy: the winning
        process usually removes it before we could look.
        """
        return "index.lock" in (result.stderr or "")

    async def _run(
        self,
        workspace: Workspace,
        repo_abs: str,
        args: Sequence[str],
        *,
        timeout: Optional[float] = None,
        check: bool = True,
        operation: str = "git",
        config: Sequence[str] = (),
        git_dir_rel: Optional[str] = None,
        allow_truncated: bool = False,
        max_output_bytes: Optional[int] = None,
    ) -> ExecResult:
        """Run one git command through the workspace boundary.

        ``shell=False`` with an argv list and ``git -C`` instead of
        ``cwd=`` — the sandbox workspace refuses ``cwd`` with
        ``shell=False``, and this way nothing is ever interpolated into a
        shell string.
        """
        if not workspace.supports_shell:
            raise GitCapabilityError(
                "this workspace substrate cannot execute git",
                operation=operation,
                reason="no_shell",
            )
        argv = ["git", "-C", repo_abs, *_GIT_CONFIG_ARGS, *config, *args]
        try:
            result = await workspace.exec(
                argv,
                shell=False,
                timeout=timeout if timeout is not None else self._read_timeout,
                max_output_bytes=(
                    self._max_output_bytes
                    if max_output_bytes is None
                    else max_output_bytes
                ),
            )
        except FileNotFoundError as exc:  # pragma: no cover - substrate dependent
            raise GitCapabilityError(
                "git is not available in this workspace",
                operation=operation,
                reason="git_missing",
            ) from exc
        except OSError as exc:
            # Anything the OS refuses (argument limits, fd exhaustion, a
            # full disk) becomes a typed failure rather than an untyped
            # -32603 carrying a host path.
            raise GitOperationFailed(
                f"could not run git: {exc}",
                operation=operation,
                reason="exec_failed",
            ) from exc
        if result.truncated and not allow_truncated:
            raise GitOperationFailed(
                "git output exceeded the transfer limit",
                operation=operation,
                exit_code=result.exit_code,
                reason="output_truncated",
            )
        if check and result.exit_code != 0:
            reason = "index_locked" if self._is_index_lock_failure(result) else None
            raise GitOperationFailed(
                (result.stderr or result.stdout or "git command failed").strip(),
                operation=operation,
                exit_code=result.exit_code,
                command=" ".join(args),
                stderr=(result.stderr or "").strip() or None,
                reason=reason,
            )
        return result

    async def _apply_patch(
        self,
        workspace: Workspace,
        repo_abs: str,
        patch: str,
        *,
        args: Sequence[str],
        operation: str,
        git_dir_rel: str,
    ) -> None:
        """Feed *patch* to ``git apply`` through the workspace boundary.

        :meth:`Workspace.exec` has no stdin, so the patch is written to a
        scratch file and handed to ``git apply`` **as an argument**. The
        file lives inside the repository's git directory — always authorized
        by the Workspace boundary (:meth:`_resolve_repo` proves it), and a
        location git never tracks, so it can never show up as a workspace
        change.

        This is deliberately not a shell pipeline: an earlier heredoc
        implementation put the whole patch into one argv string and hit
        ``MAX_ARG_STRLEN`` (128 KiB on Linux) with an untyped ``OSError``
        for perfectly ordinary large diffs. Writing a file has no size
        cap and leaves the module with **no** ``shell=True`` call at all.
        """
        scratch = f"{git_dir_rel}/omniagents-apply-{secrets.token_hex(12)}.patch"
        body = patch if patch.endswith("\n") else patch + "\n"
        try:
            try:
                await workspace.write_text(scratch, body)
            except OSError as exc:
                raise GitOperationFailed(
                    f"could not stage the patch for {operation}: {exc}",
                    operation=operation,
                    reason="patch_write_failed",
                ) from exc
            scratch_abs = workspace.resolve_confined(scratch)
            result = await self._run(
                workspace,
                repo_abs,
                ["apply", *args, "--", scratch_abs],
                timeout=self._write_timeout,
                check=False,
                operation=operation,
            )
            if result.exit_code != 0 and self._is_index_lock_failure(result):
                raise GitOperationFailed(
                    (result.stderr or result.stdout or "git apply failed").strip(),
                    operation=operation,
                    exit_code=result.exit_code,
                    reason="index_locked",
                )
        finally:
            try:
                await workspace.remove(scratch)
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug(
                    "could not remove scratch patch %s", scratch, exc_info=True
                )
        if result.exit_code != 0:
            raise GitOperationFailed(
                (result.stderr or result.stdout or "git apply failed").strip(),
                operation=operation,
                exit_code=result.exit_code,
                command="apply " + " ".join(args),
                stderr=(result.stderr or "").strip() or None,
            )

    # ------------------------------------------------------------------
    #  Repository resolution
    # ------------------------------------------------------------------

    async def _resolve_repo(self, environment_id: str, repo: str) -> "_ResolvedRepo":
        """Resolve and confine *repo*.

        Confinement rests on four independent facts, all authorized by the
        selected environment's Workspace adapter rather than reconstructed
        from host paths in the RPC layer:

        1. the wire path canonicalizes to a relative POSIX path (no
           ``..``, absolute, drive-letter or backslash paths);
        2. the resolved directory belongs to the workspace boundary. For an
           ordinary workspace this is a root-prefix check; a composite host
           workspace maps an explicitly declared source back to its logical
           mount path;
        3. git's ``--show-toplevel`` is that same directory, so a
           workspace nested inside a larger repository is not operable as
           if it were the project;
        4. git's ``--absolute-git-dir`` **and** ``--git-common-dir`` are
           authorized by that same Workspace too. Without (4) a plain *file*
           named ``.git`` holding ``gitdir: /outside/.git`` satisfies (3)
           while every object, ref, index and config entry is read from —
           and written to — outside the workspace.
        """
        if not isinstance(repo, str) or not repo.strip():
            raise GitPathError("repo is required", reason="missing_repo")
        repo_id = canonical_repo_path(repo)
        workspace = await self._workspace(environment_id)
        try:
            repo_abs = workspace.resolve_confined(repo_id)
        except WorkspaceEscapeError as exc:
            raise GitPathError(str(exc), path=repo, reason="escapes_workspace") from exc
        if not workspace.contains_path(repo_abs):
            raise GitPathError(
                "the repository path resolves outside the environment workspace",
                path=repo,
                reason="escapes_workspace",
            )
        probe = await self._run(
            workspace,
            repo_abs,
            [
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
                "--absolute-git-dir",
                "--git-common-dir",
            ],
            check=False,
            operation="resolve_repository",
        )
        if probe.exit_code != 0:
            # Also the bare-repository answer: ``--show-toplevel`` needs a
            # work tree, and a repository without one has nothing this
            # surface can operate on.
            raise GitRepositoryNotFound(
                "not a git repository with a work tree",
                repo=repo_id,
                path=repo_id,
                reason="no_work_tree",
            )
        lines = [line for line in probe.stdout.strip().split("\n") if line.strip()]
        if len(lines) != 3:
            # A bare repository has no worktree, so ``--show-toplevel``
            # produces nothing and the layout cannot be confined.
            raise GitRepositoryNotFound(
                "the repository has no work tree in this workspace",
                repo=repo_id,
                reason="layout_unresolved",
            )
        toplevel, git_dir, common_dir = lines
        if not _same_path(toplevel, repo_abs, _path_module(workspace)):
            raise GitRepositoryNotFound(
                "the repository root is outside the environment workspace",
                repo=repo_id,
                reason="outside_workspace",
                toplevel=toplevel,
            )
        git_dir_rel = workspace.io_path(git_dir)
        if (
            git_dir_rel is None
            or git_dir_rel == "."
            or not workspace.contains_path(common_dir)
        ):
            raise GitRepositoryNotFound(
                "the repository's git directory is outside the environment workspace",
                repo=repo_id,
                reason="outside_workspace",
                toplevel=git_dir,
            )
        return _ResolvedRepo(
            workspace=workspace,
            repo_id=repo_id,
            root_abs=repo_abs,
            git_dir_abs=git_dir,
            git_dir_rel=git_dir_rel,
        )

    # ------------------------------------------------------------------
    #  Discovery
    # ------------------------------------------------------------------

    async def list_repositories(
        self,
        environment_id: str,
        path: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Enumerate operable repositories inside the environment workspace.

        The returned ``repo`` ids are the only accepted repository
        addresses; a multi-repository project therefore never has to guess
        which repository an operation targets.
        """
        workspace = await self._workspace(environment_id)
        base = canonical_repo_path(path or ".")
        requested_depth = (
            self._max_discovery_depth if max_depth is None else int(max_depth)
        )
        depth = max(0, min(requested_depth, self._max_discovery_depth))
        depth_clamped = depth != requested_depth
        found: List[str] = []
        truncated = False
        queue: List[Tuple[str, int]] = [(base, 0)]
        while queue:
            current, level = queue.pop(0)
            try:
                entries = await workspace.list_dir(
                    current, include_hidden=True, respect_gitignore=False
                )
            except (OSError, WorkspaceError):
                # Discovery is best-effort by construction: one unreadable
                # or mis-typed directory must not fail the whole listing.
                # ``Workspace.list_dir`` guarantees an OSError family on
                # every substrate (a sandbox session's own ``ls`` failure
                # is translated at the workspace layer), so this no longer
                # has to swallow every exception to survive a container.
                logger.debug("git discovery skipped %r", current, exc_info=True)
                continue
            names = {entry.name for entry in entries}
            if ".git" in names:
                found.append(current)
                if len(found) >= self._max_repositories:
                    truncated = True
                    break
                # A repository's own subdirectories may hold submodules or
                # nested checkouts; keep descending but never into .git.
            if level >= depth:
                continue
            for entry in entries:
                if not entry.is_dir or entry.is_symlink or entry.name == ".git":
                    continue
                # ``FileEntry.path`` is workspace-relative on every
                # substrate, so it addresses the child directly — but the
                # two path policies are deliberately not the same: a
                # backslash is a legal character inside a POSIX filename
                # (so ``FileEntry`` keeps it) and is refused on this
                # surface's wire (``canonical_repo_path``). A directory an
                # agent can create, such as ``back\slash``, must therefore
                # be skipped rather than abort the whole enumeration —
                # including the legitimate repositories found alongside it.
                try:
                    child = canonical_repo_path(entry.path)
                except GitPathError:
                    logger.debug(
                        "git discovery skipped entry %r: not addressable on "
                        "this surface",
                        entry.path,
                    )
                    continue
                queue.append((child, level + 1))

        sources = [
            dict(source) for source in await self._source_provider(environment_id)
        ]
        by_mount = {
            str(source.get("mountName") or ""): source
            for source in sources
            if source.get("mountName")
        }
        pm = _path_module(workspace)

        def path_key(value: str) -> str:
            return pm.normcase(pm.normpath(value))

        by_path = {
            path_key(str(source.get("path"))): source
            for source in sources
            if source.get("path")
        }
        repositories: List[Dict[str, Any]] = []
        for candidate in sorted(found):
            try:
                candidate_repo = await self._resolve_repo(environment_id, candidate)
                repo_id = candidate_repo.repo_id
                repo_abs = candidate_repo.root_abs
            except GitRpcError:
                continue
            summary = await self._repository_summary(workspace, repo_id, repo_abs)
            # A repository that *is* one of the launcher's published
            # ``--source`` mounts is labelled as such, so a client can
            # tell a project source apart from an incidental checkout.
            source = by_mount.get(repo_id) or by_path.get(path_key(repo_abs))
            if source is None:
                source = next(
                    (
                        candidate
                        for mount, candidate in by_mount.items()
                        if repo_id.startswith(f"{mount}/")
                    ),
                    None,
                )
            if source is None:
                source = next(
                    (
                        candidate
                        for source_path, candidate in by_path.items()
                        if repo_abs.startswith(f"{source_path}/")
                    ),
                    None,
                )
            summary["source"] = _redacted_source(source)
            repositories.append(summary)
        # Source materialization and repository discovery are different facts.
        # A plain ``local`` source is allowed to contain no repository at all,
        # and a repository-capable source may be present even when its Git
        # metadata is invalid. Only report a source as outside when neither its
        # environment mount nor its declared host path belongs to this
        # Workspace. Never infer reachability from whether discovery matched a
        # repository.
        unreachable: List[Dict[str, Any]] = []
        for source in sources:
            if source.get("kind") not in {"local-git", "git-remote"}:
                continue
            mount_name = source.get("mountName")
            declared_path = source.get("path")
            mounted = False
            if isinstance(mount_name, str) and mount_name:
                try:
                    mounted = await workspace.is_dir(mount_name)
                except (OSError, WorkspaceError):
                    mounted = False
            if not mounted and isinstance(declared_path, str) and declared_path:
                mounted = workspace.contains_path(declared_path)
            if mounted:
                continue
            unreachable.append(
                {
                    "mount_name": mount_name,
                    "kind": source.get("kind"),
                    "path": declared_path,
                    "repo_url": redact_remote_url(source.get("repoUrl")),
                    "reason": "not_in_workspace",
                }
            )
        return {
            "environment_id": environment_id,
            "path": base,
            "repositories": repositories,
            "sources": [_redacted_source(source) for source in sources],
            "unreachable_sources": unreachable,
            "max_depth": depth,
            "max_depth_clamped": depth_clamped,
            # True when the answer is incomplete for *any* reason: the
            # repository cap was hit, or the requested depth was clamped.
            "truncated": truncated or depth_clamped,
        }

    async def _repository_summary(
        self, workspace: Workspace, repo_id: str, repo_abs: str
    ) -> Dict[str, Any]:
        head, bare, git_dir = await asyncio.gather(
            self._run(
                workspace,
                repo_abs,
                ["rev-parse", "--abbrev-ref", "HEAD"],
                check=False,
                operation="repository_summary",
            ),
            self._run(
                workspace,
                repo_abs,
                ["rev-parse", "--is-bare-repository"],
                check=False,
                operation="repository_summary",
            ),
            self._run(
                workspace,
                repo_abs,
                ["rev-parse", "--path-format=absolute", "--git-common-dir"],
                check=False,
                operation="repository_summary",
            ),
        )
        branch = head.stdout.strip() or None
        detached = branch == "HEAD"
        return {
            "repo": repo_id,
            "root": repo_id,
            "absolute_root": repo_abs,
            "branch": None if detached or branch is None else branch,
            "detached": detached,
            "bare": bare.stdout.strip() == "true",
            "git_common_dir": git_dir.stdout.strip() or None,
            "is_linked_worktree": (
                git_dir.stdout.strip() != ""
                and not _same_path(
                    git_dir.stdout.strip(),
                    _path_module(workspace).join(repo_abs, ".git"),
                    _path_module(workspace),
                )
            ),
        }

    # ------------------------------------------------------------------
    #  Read operations
    # ------------------------------------------------------------------

    async def status(
        self,
        environment_id: str,
        repo: str,
        include_untracked: Optional[bool] = None,
        include_ignored: Optional[bool] = None,
        paths: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        untracked = "all" if include_untracked in (None, True) else "no"
        args = [
            "status",
            "--porcelain=v2",
            "--branch",
            "--show-stash",
            "-z",
            f"--untracked-files={untracked}",
        ]
        if include_ignored:
            args.append("--ignored=matching")
        args.extend(self._pathspec(paths))
        result = await self._run(workspace, repo_abs, args, operation="status")
        try:
            parsed = parse_status_v2(result.stdout)
        except (ValueError, IndexError) as exc:
            raise GitStateInvalid(
                f"could not parse git status output: {exc}",
                repo=repo_id,
                reason="status_parse_failed",
            ) from exc
        parsed["repo"] = repo_id
        parsed["environment_id"] = environment_id
        parsed["state"] = await self._repository_state(workspace, repo_abs)
        return parsed

    async def _repository_state(self, workspace: Workspace, repo_abs: str) -> str:
        """In-progress operation, detected from pseudo-refs (exit codes)."""
        probes = await asyncio.gather(
            *[
                self._run(
                    workspace,
                    repo_abs,
                    ["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"],
                    check=False,
                    operation="repository_state",
                )
                for ref in (
                    "REBASE_HEAD",
                    "MERGE_HEAD",
                    "CHERRY_PICK_HEAD",
                    "REVERT_HEAD",
                )
            ]
        )
        for name, probe in zip(
            ("rebasing", "merging", "cherry_picking", "reverting"), probes
        ):
            if probe.exit_code == 0:
                return name
        return "clean"

    @staticmethod
    def _pathspec(paths: Optional[Sequence[str]]) -> List[str]:
        if not paths:
            return []
        canonical = [canonical_repo_path(item, allow_root=False) for item in paths]
        return ["--", *canonical]

    # ------------------------------------------------------------------
    #  Repository-status watches (``git_status_watch``, tkt follow-up to
    #  tkt_l_l0lUi4A5V2)
    #
    #  The server polls ``git status --porcelain=v2`` — git itself applies
    #  ignore rules, so this stays cheap and bounded where a recursive
    #  filesystem watch of the worktree would blow the scan limit on any
    #  repository with a dependency tree. Clients receive a coalesced
    #  ``git_status_changed`` notification whenever the digest moves and
    #  refetch ``git_status``/``git_diff`` themselves; the event carries no
    #  repository content. Watches are channel-bound exactly like the
    #  ``fs_watch`` precedent: a dropped socket tears them down and a
    #  reconnecting client re-subscribes.
    # ------------------------------------------------------------------

    _STATUS_DIGEST_ARGS = (
        "status",
        "--porcelain=v2",
        "--branch",
        "--show-stash",
        "-z",
        "--untracked-files=all",
    )

    async def _status_digest(self, workspace: Workspace, repo_abs: str) -> str:
        result = await self._run(
            workspace,
            repo_abs,
            list(self._STATUS_DIGEST_ARGS),
            operation="status_watch",
        )
        return hashlib.sha256(result.stdout.encode("utf-8", "replace")).hexdigest()

    def _status_watch_count(self, channel: Any) -> int:
        return sum(
            1 for state in self._status_watches.values() if state.channel is channel
        )

    async def status_watch(
        self,
        environment_id: str,
        repo: str,
        *,
        poll_interval_ms: Optional[int] = None,
        channel: Any,
    ) -> Dict[str, Any]:
        if channel is None:
            raise GitOperationFailed(
                "git_status_watch requires a connected channel",
                operation="status_watch",
                reason="no_channel",
            )
        if self._status_watch_count(channel) >= self._max_status_watches_per_connection:
            raise GitOperationFailed(
                "too many status watches on this connection",
                operation="status_watch",
                reason="watch_limit_exceeded",
            )
        resolved = await self._resolve_repo(environment_id, repo)
        interval = self._status_watch_interval
        if poll_interval_ms is not None:
            interval = min(
                self._max_status_watch_interval,
                max(self._min_status_watch_interval, poll_interval_ms / 1000.0),
            )
        digest = await self._status_digest(resolved.workspace, resolved.root_abs)
        state = _StatusWatchState(
            watch_id=secrets.token_hex(16),
            environment_id=environment_id,
            repo=resolved.repo_id,
            channel=channel,
            workspace=resolved.workspace,
            repo_abs=resolved.root_abs,
            interval=interval,
            digest=digest,
        )
        self._status_watches[state.watch_id] = state
        state.task = asyncio.create_task(self._status_watch_loop(state))
        return {
            "watch_id": state.watch_id,
            "environment_id": environment_id,
            "repo": state.repo,
            "poll_interval_ms": int(interval * 1000),
            "digest": digest,
        }

    async def _status_watch_loop(self, state: _StatusWatchState) -> None:
        try:
            while not state.closed:
                await asyncio.sleep(state.interval)
                if state.closed:
                    return
                try:
                    digest = await self._status_digest(state.workspace, state.repo_abs)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Transient (index.lock races, a repo mid-rewrite) or the
                    # repository is gone; either way the next cycle retries
                    # and a permanently broken repo just polls quietly.
                    logger.debug(
                        "git status watch poll failed (watch %s)",
                        state.watch_id,
                        exc_info=True,
                    )
                    continue
                if digest == state.digest:
                    continue
                state.digest = digest
                try:
                    await state.channel.async_call(
                        "git_status_changed",
                        {
                            "environment_id": state.environment_id,
                            "repo": state.repo,
                            "watch_id": state.watch_id,
                            "digest": digest,
                        },
                    )
                except Exception:
                    logger.debug(
                        "git_status_changed delivery failed (watch %s)",
                        state.watch_id,
                        exc_info=True,
                    )
                    return
        finally:
            state.closed = True
            self._status_watches.pop(state.watch_id, None)

    async def status_unwatch(
        self, environment_id: str, watch_id: str, *, channel: Any
    ) -> bool:
        state = self._status_watches.get(watch_id)
        if (
            state is None
            or state.environment_id != environment_id
            or state.channel is not channel
        ):
            raise GitOperationFailed(
                "unknown status watch",
                operation="status_unwatch",
                reason="unknown_watch",
            )
        self._drop_status_watch(state)
        return True

    def _drop_status_watch(self, state: _StatusWatchState) -> None:
        state.closed = True
        self._status_watches.pop(state.watch_id, None)
        if state.task is not None and not state.task.done():
            state.task.cancel()

    def cleanup_channel(self, channel: Any) -> None:
        """Tear down every status watch owned by a disconnected channel."""
        for state in [s for s in self._status_watches.values() if s.channel is channel]:
            self._drop_status_watch(state)

    def status_watch_count(self) -> int:
        return len(self._status_watches)

    async def diff(
        self,
        environment_id: str,
        repo: str,
        mode: Optional[str] = None,
        paths: Optional[Sequence[str]] = None,
        context_lines: Optional[int] = None,
        from_rev: Optional[str] = None,
        to_rev: Optional[str] = None,
        include_untracked: bool = False,
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        scope = mode or "worktree"
        if scope != "range" and (from_rev or to_rev):
            raise GitStateInvalid(
                "from_rev/to_rev only apply to mode='range'",
                reason="parameter_not_applicable",
                mode=scope,
                supported=["range"],
            )
        if include_untracked and scope not in ("worktree", "head"):
            raise GitStateInvalid(
                "include_untracked only applies to mode='worktree' or 'head'",
                reason="parameter_not_applicable",
                mode=scope,
                supported=["worktree", "head"],
            )
        files = await self._collect_diff(
            workspace,
            repo_id,
            repo_abs,
            scope=scope,
            paths=paths,
            context_lines=context_lines,
            from_rev=from_rev,
            to_rev=to_rev,
        )
        if include_untracked:
            files.extend(
                await self._collect_untracked_diffs(
                    workspace,
                    repo_abs,
                    scope=scope,
                    paths=paths,
                    context_lines=context_lines,
                )
            )
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "mode": scope,
            "context_lines": self._context(context_lines),
            "context_lines_clamped": self._context_clamped(context_lines),
            "files": [item.to_wire() for item in files],
        }

    async def _collect_untracked_diffs(
        self,
        workspace: Workspace,
        repo_abs: str,
        *,
        scope: str,
        paths: Optional[Sequence[str]] = None,
        context_lines: Optional[int] = None,
    ) -> List[DiffFile]:
        """Untracked files as synthesized whole-file additions.

        ``git diff`` never shows untracked content (there is no baseline
        for it), so each file gets a read-only ``--no-index`` diff
        against ``/dev/null`` — the lazygit idiom; the index is never
        touched (no ``add -N``). ``--no-index`` exits 1 when the files
        differ, which here is the success case."""
        listed = await self._run(
            workspace,
            repo_abs,
            [
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                *self._pathspec(paths),
            ],
            operation="diff",
        )
        context = self._context(context_lines)
        out: List[DiffFile] = []
        for path in sorted(p for p in listed.stdout.split("\0") if p):
            patch = await self._run(
                workspace,
                repo_abs,
                [
                    "diff",
                    *_DIFF_COMMON,
                    "--patch",
                    f"-U{context}",
                    "--no-index",
                    "--",
                    "/dev/null",
                    path,
                ],
                operation="diff",
                check=False,
            )
            blocks = parse_patch_blocks(patch.stdout)
            if not blocks:
                continue
            block = blocks[0]
            new_mode = "100644"
            binary = False
            for line in block.header_lines:
                if line.startswith("new file mode "):
                    new_mode = line.rsplit(" ", 1)[-1]
                elif line.startswith("Binary files "):
                    binary = True
            hunks: List[DiffHunk] = []
            added: Optional[int] = None if binary else 0
            if not binary:
                for position, raw in enumerate(block.hunks):
                    hunk = _parse_hunk(scope, path, position, raw)
                    hunks.append(hunk)
                    added = (added or 0) + sum(
                        1 for entry in hunk.lines if entry.origin == "add"
                    )
            out.append(
                DiffFile(
                    path=path,
                    old_path=None,
                    change="added",
                    old_mode="000000",
                    new_mode=new_mode,
                    old_oid=_NULL_OID,
                    new_oid=_NULL_OID,
                    similarity=None,
                    binary=binary,
                    added_lines=added,
                    deleted_lines=None if binary else 0,
                    unmerged=False,
                    submodule=False,
                    hunks=hunks,
                    header_lines=list(block.header_lines),
                )
            )
        return out

    def _context(self, context_lines: Optional[int]) -> int:
        if context_lines is None:
            return self._default_context_lines
        return max(0, min(int(context_lines), self._max_context_lines))

    def _context_clamped(self, context_lines: Optional[int]) -> bool:
        return context_lines is not None and self._context(context_lines) != int(
            context_lines
        )

    def _scope_args(
        self, scope: str, from_rev: Optional[str], to_rev: Optional[str]
    ) -> List[str]:
        if scope == "range":
            if not from_rev:
                raise GitStateInvalid(
                    "mode='range' requires from_rev", reason="missing_revision"
                )
            revisions = [_checked_revision(from_rev)]
            if to_rev:
                revisions.append(_checked_revision(to_rev))
            return [*revisions, "--diff-filter=u"]
        if scope not in _DIFF_SCOPES:
            raise GitStateInvalid(
                f"unknown diff mode: {scope!r}",
                reason="unknown_mode",
                supported=sorted([*_DIFF_SCOPES, "range"]),
            )
        return list(_DIFF_SCOPES[scope])

    async def _collect_diff(
        self,
        workspace: Workspace,
        repo_id: str,
        repo_abs: str,
        *,
        scope: str,
        paths: Optional[Sequence[str]] = None,
        context_lines: Optional[int] = None,
        from_rev: Optional[str] = None,
        to_rev: Optional[str] = None,
    ) -> List[DiffFile]:
        scope_args = self._scope_args(scope, from_rev, to_rev)
        pathspec = self._pathspec(paths)
        context = self._context(context_lines)
        raw_result, numstat_result, patch_result = await asyncio.gather(
            self._run(
                workspace,
                repo_abs,
                ["diff", *_DIFF_COMMON, "--raw", "-z", *scope_args, *pathspec],
                operation="diff",
            ),
            self._run(
                workspace,
                repo_abs,
                ["diff", *_DIFF_COMMON, "--numstat", "-z", *scope_args, *pathspec],
                operation="diff",
            ),
            self._run(
                workspace,
                repo_abs,
                [
                    "diff",
                    *_DIFF_COMMON,
                    "--patch",
                    f"-U{context}",
                    *scope_args,
                    *pathspec,
                ],
                operation="diff",
            ),
        )
        try:
            return assemble_diff(
                scope=scope,
                raw_records=parse_raw_z(raw_result.stdout),
                numstat=parse_numstat_z(numstat_result.stdout),
                blocks=parse_patch_blocks(patch_result.stdout),
            )
        except ValueError as exc:
            raise GitStateInvalid(
                f"could not interpret git diff output: {exc}",
                repo=repo_id,
                reason="diff_parse_failed",
            ) from exc

    async def log(
        self,
        environment_id: str,
        repo: str,
        rev: Optional[str] = None,
        max_count: Optional[int] = None,
        skip: Optional[int] = None,
        paths: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        requested_count = (
            self._default_log_count if max_count is None else int(max_count)
        )
        count = max(1, min(requested_count, self._max_log_count))
        count_clamped = count != requested_count
        # %x00 field separator + %x1e record separator: fully machine
        # delimited, so subjects and bodies may contain anything.
        fmt = "%H%x00%h%x00%P%x00%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%D%x00%s%x00%b%x1e"
        args = ["log", f"--format={fmt}", f"--max-count={count}"]
        if skip:
            args.append(f"--skip={int(skip)}")
        if rev:
            args.append(_checked_revision(rev))
        args.extend(self._pathspec(paths))
        result = await self._run(
            workspace, repo_abs, args, check=False, operation="log"
        )
        if result.exit_code != 0:
            stderr = (result.stderr or "").strip()
            raise GitOperationFailed(
                stderr or "git log failed",
                operation="log",
                exit_code=result.exit_code,
                repo=repo_id,
                stderr=stderr or None,
            )
        commits: List[Dict[str, Any]] = []
        for record in result.stdout.split("\x1e"):
            record = record.lstrip("\n")
            if not record.strip():
                continue
            fields = record.split("\x00")
            if len(fields) < 12:
                continue
            commits.append(
                {
                    "oid": fields[0],
                    "short_oid": fields[1],
                    "parents": fields[2].split(" ") if fields[2] else [],
                    "author_name": fields[3],
                    "author_email": fields[4],
                    "authored_at": fields[5],
                    "committer_name": fields[6],
                    "committer_email": fields[7],
                    "committed_at": fields[8],
                    "refs": [
                        item.strip() for item in fields[9].split(",") if item.strip()
                    ],
                    "subject": fields[10],
                    "body": fields[11],
                }
            )
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "commits": commits,
            "max_count": count,
            # True when the caller asked for more history than the server
            # will return, so "50 commits" is never mistaken for "all".
            "truncated": count_clamped or len(commits) == count,
        }

    async def list_branches(
        self, environment_id: str, repo: str, include_remote: Optional[bool] = None
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        refs = ["refs/heads"]
        if include_remote in (None, True):
            refs.append("refs/remotes")
        fmt = "%(refname)%00%(objectname)%00%(upstream)%00%(upstream:remotename)%00%(HEAD)%00%(worktreepath)"
        result = await self._run(
            workspace,
            repo_abs,
            ["for-each-ref", f"--format={fmt}", *refs],
            operation="list_branches",
        )
        branches: List[Dict[str, Any]] = []
        for line in result.stdout.split("\n"):
            if not line.strip():
                continue
            fields = line.split("\x00")
            if len(fields) < 6:
                continue
            refname = fields[0]
            remote = refname.startswith("refs/remotes/")
            short = (
                refname[len("refs/remotes/") :]
                if remote
                else refname[len("refs/heads/") :]
            )
            branches.append(
                {
                    "ref": refname,
                    "name": short,
                    "oid": fields[1],
                    "remote": remote,
                    "upstream": fields[2] or None,
                    "upstream_remote": fields[3] or None,
                    "current": fields[4] == "*",
                    "worktree_path": fields[5] or None,
                    # ``worker/`` is the branch namespace
                    # WorktreeIsolation creates for isolated workers; the
                    # category lets a client group them without inventing
                    # its own naming convention.
                    "category": "worker" if short.startswith("worker/") else "branch",
                }
            )
        branches.sort(key=lambda item: (item["remote"], item["ref"]))
        return {"environment_id": environment_id, "repo": repo_id, "branches": branches}

    async def list_worktrees(self, environment_id: str, repo: str) -> Dict[str, Any]:
        """Worktree metadata, including ones this surface cannot operate on.

        Worker worktrees created by ``WorktreeIsolation`` live under the
        framework scratch root, i.e. *outside* the environment workspace.
        They are reported (a client should be able to see that a branch is
        checked out elsewhere) but marked ``accessible: false`` with a
        reason — this surface refuses to act outside the workspace rather
        than quietly reaching around the sandbox boundary.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        result = await self._run(
            workspace,
            repo_abs,
            ["worktree", "list", "--porcelain"],
            operation="list_worktrees",
        )
        worktrees = parse_worktree_porcelain(result.stdout)
        for entry in worktrees:
            relative = workspace.relative_path(entry["path"])
            entry["repo"] = relative
            entry["accessible"] = relative is not None
            entry["inaccessible_reason"] = (
                None if relative is not None else "outside_workspace"
            )
            branch = entry.get("branch") or ""
            entry["category"] = (
                "worker" if branch.startswith("refs/heads/worker/") else "worktree"
            )
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "worktrees": worktrees,
        }

    async def conflicts(
        self, environment_id: str, repo: str, paths: Optional[Sequence[str]] = None
    ) -> Dict[str, Any]:
        """Structured view of every unmerged path.

        Three independent structural sources, all machine formats:

        * ``git ls-files -u -z`` for the stage-1/2/3 blob identities,
        * ``git cat-file blob <oid>`` for each stage's **content**, so a
          three-pane merge editor has base/ours/theirs without a separate
          blob-read RPC — and so the base side is available even under
          git's default ``merge`` conflict style, which never writes a
          base section into the working file,
        * the fixed ASCII conflict markers in the merged working file for
          the regions to highlight.

        Stage contents are decoded as text and capped
        (``max_conflict_blob_bytes``); a stage over the cap comes back
        with ``content: null`` and ``content_truncated: true`` rather
        than a partial blob. Binary conflicts should be resolved
        file-level — the decode is lossy for them by construction.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        result = await self._run(
            workspace,
            repo_abs,
            ["ls-files", "-u", "-z", *self._pathspec(paths)],
            operation="conflicts",
        )
        stages: Dict[str, Dict[str, Any]] = {}
        for record in _split_nul(result.stdout):
            meta, _, path = record.partition("\t")
            parts = meta.split(" ")
            if len(parts) != 3 or not path:
                continue
            mode, oid, stage = parts
            entry = stages.setdefault(
                path,
                {"path": path, "stages": {}, "regions": [], "regions_available": False},
            )
            entry["stages"][
                {"1": "base", "2": "ours", "3": "theirs"}.get(stage, stage)
            ] = {
                "mode": mode,
                "oid": oid,
                "content": None,
                "content_truncated": False,
            }
        ordered = sorted(stages)
        for path in ordered[: self._max_conflict_content_paths]:
            entry = stages[path]
            workspace_path = _join_workspace(repo_id, path)
            try:
                # ``resolve_confined``, not a bare read: the boundary is this
                # surface's promise to the client, so it is asked for here
                # rather than inherited from whatever the workspace enforces
                # (a host workspace enforces nothing). A conflicted path that
                # symlinks out of the workspace is refused before it is read.
                content = await workspace.read_text(
                    workspace.resolve_confined(workspace_path)
                )
            except (
                FileNotFoundError,
                IsADirectoryError,
                UnicodeDecodeError,
                OSError,
                # WorkspaceEscapeError is a WorkspaceError, *not* an
                # OSError: a conflicted path that symlinks out of the
                # workspace used to escape as an untyped -32603 echoing
                # the absolute workspace root back to the client.
                WorkspaceEscapeError,
            ):
                pass
            else:
                entry["regions"] = parse_conflict_regions(content)
                entry["regions_available"] = True
            for stage in entry["stages"].values():
                blob = await self._run(
                    workspace,
                    repo_abs,
                    ["cat-file", "blob", stage["oid"]],
                    check=False,
                    allow_truncated=True,
                    operation="conflict_blob",
                    timeout=self._read_timeout,
                    max_output_bytes=self._max_conflict_blob_bytes,
                )
                if blob.exit_code != 0:
                    continue
                if blob.truncated:
                    stage["content_truncated"] = True
                else:
                    stage["content"] = blob.stdout
        state = await self._repository_state(workspace, repo_abs)
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "state": state,
            "conflicts": [stages[key] for key in ordered],
            "content_truncated": len(ordered) > self._max_conflict_content_paths,
        }

    # ------------------------------------------------------------------
    #  Hunk selection
    # ------------------------------------------------------------------

    async def _select_hunks(
        self,
        workspace: Workspace,
        repo_id: str,
        repo_abs: str,
        *,
        scope: str,
        hunks: Sequence[Any],
        context_lines: Optional[int],
    ) -> List[Tuple[DiffFile, List[DiffHunk]]]:
        """Resolve wire hunk references against a freshly recomputed diff.

        Selections are validated *before* anything runs: an id that no
        longer resolves (the file moved on underneath the client) is a
        typed ``git_state_invalid`` naming the stale ids, never a partial
        application.
        """
        requested: Dict[str, List[str]] = {}
        order: List[Tuple[str, str]] = []
        for item in hunks:
            if not isinstance(item, dict):
                raise GitStateInvalid(
                    "each hunk selection must be an object with path and hunk_id",
                    reason="invalid_hunk_selection",
                )
            path = canonical_repo_path(str(item.get("path", "")), allow_root=False)
            hunk_id = item.get("hunk_id")
            if not isinstance(hunk_id, str) or not hunk_id:
                raise GitStateInvalid(
                    "hunk selection is missing hunk_id",
                    reason="invalid_hunk_selection",
                    path=path,
                )
            requested.setdefault(path, []).append(hunk_id)
            order.append((path, hunk_id))
        files = await self._collect_diff(
            workspace,
            repo_id,
            repo_abs,
            scope=scope,
            paths=sorted(requested),
            context_lines=context_lines,
        )
        by_path = {item.path: item for item in files}
        unsupported = [
            path
            for path in requested
            if path in by_path and not hunk_selection_supported(by_path[path])
        ]
        if unsupported:
            raise GitStateInvalid(
                "these paths have no line-level representation and must be "
                "staged or discarded whole",
                reason="hunk_selection_unsupported",
                paths=sorted(unsupported),
            )
        selections: List[Tuple[DiffFile, List[DiffHunk]]] = []
        stale: List[Dict[str, str]] = []
        for path in sorted(requested):
            diff_file = by_path.get(path)
            if diff_file is None:
                stale.extend({"path": path, "hunk_id": h} for h in requested[path])
                continue
            available = {hunk.hunk_id: hunk for hunk in diff_file.hunks}
            chosen: List[DiffHunk] = []
            for hunk_id in requested[path]:
                hunk = available.get(hunk_id)
                if hunk is None:
                    stale.append({"path": path, "hunk_id": hunk_id})
                    continue
                chosen.append(hunk)
            if chosen:
                partial = len(chosen) < len(diff_file.hunks)
                if partial and diff_file.change in ("added", "deleted"):
                    raise GitStateInvalid(
                        "a file addition or deletion must be selected whole",
                        reason="partial_selection_unsupported",
                        path=path,
                        change=diff_file.change,
                    )
                chosen.sort(key=lambda item: item.index)
                selections.append((diff_file, chosen))
        if stale:
            raise GitStateInvalid(
                "these hunks are not present in the "
                f"{scope!r} diff; re-read git_diff for that mode and retry "
                "(hunk ids are bound to the diff mode they came from)",
                reason="hunk_not_found",
                stale=stale,
                scope=scope,
            )
        return selections

    # ------------------------------------------------------------------
    #  Index snapshot / rollback
    # ------------------------------------------------------------------

    async def _index_snapshot(
        self, workspace: Workspace, repo_abs: str
    ) -> Optional[str]:
        """Tree id of the current index, or ``None`` when unrepresentable.

        ``git write-tree`` refuses an unmerged index; a conflict-time
        operation therefore has no snapshot and is only allowed when it is
        a single atomic step.
        """
        result = await self._run(
            workspace,
            repo_abs,
            ["write-tree"],
            check=False,
            operation="index_snapshot",
        )
        if result.exit_code != 0:
            return None
        return result.stdout.strip() or None

    async def _restore_index(
        self, workspace: Workspace, repo_abs: str, tree: str
    ) -> None:
        await self._run(
            workspace,
            repo_abs,
            ["read-tree", tree],
            timeout=self._write_timeout,
            operation="index_restore",
        )

    # ------------------------------------------------------------------
    #  Staging
    # ------------------------------------------------------------------

    async def _stage_unlocked(
        self,
        environment_id: str,
        repo: str,
        paths: Optional[Sequence[str]] = None,
        hunks: Optional[Sequence[Any]] = None,
        context_lines: Optional[int] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stage whole paths and/or individual hunks.

        ``mode`` names the diff the ``hunks`` came from — ``worktree``
        (the default, index → worktree) or ``head`` (HEAD → worktree, the
        "all my changes" view a UI usually opens on). Hunk ids are
        scope-bound, so a ``head`` diff's hunks are only resolvable when
        the caller says so; a ``head``-scope stage additionally requires
        the file to be unstaged (its index entry must still match HEAD),
        otherwise ``git apply`` refuses and the typed failure says so.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        path_list = [
            canonical_repo_path(item, allow_root=False) for item in (paths or [])
        ]
        hunk_list = list(hunks or [])
        if not path_list and not hunk_list:
            raise GitStateInvalid(
                "nothing to stage: provide paths, hunks, or both",
                reason="empty_selection",
                repo=repo_id,
            )
        scope = mode or "worktree"
        if scope not in ("worktree", "head"):
            raise GitStateInvalid(
                f"hunks cannot be staged from mode={scope!r}",
                reason="unknown_mode",
                repo=repo_id,
                mode=scope,
                supported=["worktree", "head"],
            )
        selections = (
            await self._select_hunks(
                workspace,
                repo_id,
                repo_abs,
                scope=scope,
                hunks=hunk_list,
                context_lines=context_lines,
            )
            if hunk_list
            else []
        )
        self._reject_overlap(path_list, selections)
        patch = build_patch(selections, direction="forward")

        async def add_paths() -> None:
            await self._run(
                workspace,
                repo_abs,
                ["add", "--", *path_list],
                timeout=self._write_timeout,
                operation="stage",
                git_dir_rel=git_dir_rel,
            )

        async def apply_hunks() -> None:
            await self._apply_patch(
                workspace,
                repo_abs,
                patch,
                args=["--cached", "--whitespace=nowarn"],
                operation="stage_hunks",
                git_dir_rel=git_dir_rel,
            )

        steps: List[Callable[[], Awaitable[None]]] = []
        if path_list:
            steps.append(add_paths)
        if patch:
            steps.append(apply_hunks)
        await self._atomic(workspace, repo_abs, steps, operation="stage")
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "staged_paths": path_list,
            "staged_hunks": [
                {"path": diff_file.path, "hunk_id": hunk.hunk_id}
                for diff_file, chosen in selections
                for hunk in chosen
            ],
        }

    async def _unstage_unlocked(
        self,
        environment_id: str,
        repo: str,
        paths: Optional[Sequence[str]] = None,
        hunks: Optional[Sequence[Any]] = None,
        context_lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        path_list = [
            canonical_repo_path(item, allow_root=False) for item in (paths or [])
        ]
        hunk_list = list(hunks or [])
        if not path_list and not hunk_list:
            raise GitStateInvalid(
                "nothing to unstage: provide paths, hunks, or both",
                reason="empty_selection",
                repo=repo_id,
            )
        selections = (
            await self._select_hunks(
                workspace,
                repo_id,
                repo_abs,
                scope="staged",
                hunks=hunk_list,
                context_lines=context_lines,
            )
            if hunk_list
            else []
        )
        self._reject_overlap(path_list, selections)
        patch = build_patch(selections, direction="reverse")
        unborn = await self._is_unborn(workspace, repo_abs)

        async def restore_paths() -> None:
            if unborn:
                # No HEAD to restore from: dropping the index entry is the
                # only meaning "unstage" can have on an unborn branch.
                await self._run(
                    workspace,
                    repo_abs,
                    ["rm", "--cached", "-r", "--quiet", "--", *path_list],
                    timeout=self._write_timeout,
                    operation="unstage",
                    git_dir_rel=git_dir_rel,
                )
            else:
                await self._run(
                    workspace,
                    repo_abs,
                    ["restore", "--staged", "--", *path_list],
                    timeout=self._write_timeout,
                    operation="unstage",
                    git_dir_rel=git_dir_rel,
                )

        async def apply_hunks() -> None:
            await self._apply_patch(
                workspace,
                repo_abs,
                patch,
                args=["--cached", "--reverse", "--whitespace=nowarn"],
                operation="unstage_hunks",
                git_dir_rel=git_dir_rel,
            )

        steps: List[Callable[[], Awaitable[None]]] = []
        if path_list:
            steps.append(restore_paths)
        if patch:
            steps.append(apply_hunks)
        await self._atomic(workspace, repo_abs, steps, operation="unstage")
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "unstaged_paths": path_list,
            "unstaged_hunks": [
                {"path": diff_file.path, "hunk_id": hunk.hunk_id}
                for diff_file, chosen in selections
                for hunk in chosen
            ],
        }

    async def _is_unborn(self, workspace: Workspace, repo_abs: str) -> bool:
        probe = await self._run(
            workspace,
            repo_abs,
            ["rev-parse", "-q", "--verify", "HEAD^{commit}"],
            check=False,
            operation="head_probe",
        )
        return probe.exit_code != 0

    @staticmethod
    def _reject_overlap(
        paths: Sequence[str],
        selections: Sequence[Tuple[DiffFile, Sequence[DiffHunk]]],
    ) -> None:
        if not paths:
            return
        overlap = sorted(set(paths) & {diff_file.path for diff_file, _ in selections})
        if overlap:
            raise GitStateInvalid(
                "a path cannot be selected whole and by hunk in the same call",
                reason="overlapping_selection",
                paths=overlap,
            )

    async def _atomic(
        self,
        workspace: Workspace,
        repo_abs: str,
        steps: Sequence[Callable[[], Awaitable[None]]],
        *,
        operation: str,
    ) -> None:
        """Run *steps*, restoring the index if a later step fails.

        A single step needs no snapshot — ``git add`` and ``git apply``
        are each all-or-nothing. Two or more steps can leave the index
        half-updated, so the index tree is captured first and read back on
        failure. An index that cannot be snapshotted (unmerged) refuses
        multi-step operations outright rather than risking a partial
        result.
        """
        if not steps:
            return
        if len(steps) == 1:
            await steps[0]()
            return
        snapshot = await self._index_snapshot(workspace, repo_abs)
        if snapshot is None:
            raise GitStateInvalid(
                "the index has unmerged entries, so a multi-step "
                f"{operation} cannot be rolled back; resolve conflicts first",
                reason="unmerged_index",
                operation=operation,
            )
        for step in steps:
            try:
                await step()
            except BaseException as exc:
                # Deliberately not ``except GitRpcError``: an OS-level
                # failure or a cancellation between two steps is exactly
                # the case that would otherwise strand a half-updated
                # index. The snapshot goes back first, whatever happened.
                await self._restore_index(workspace, repo_abs, snapshot)
                if isinstance(exc, GitRpcError):
                    exc._data["rolled_back"] = True
                    raise
                if isinstance(exc, Exception):
                    raise GitOperationFailed(
                        f"{operation} failed and was rolled back: {exc}",
                        operation=operation,
                        reason="step_failed",
                        rolled_back=True,
                    ) from exc
                raise
        return

    # ------------------------------------------------------------------
    #  Destructive operations
    # ------------------------------------------------------------------

    async def _worktree_digest(self, workspace: Workspace, repo_abs: str) -> str:
        """Digest of every uncommitted change, staged or not.

        ``git diff HEAD --binary`` is the complete description of what a
        hard reset or a discarding checkout would destroy, so hashing it
        binds a confirmation token to the exact content — not merely to
        the set of paths involved.
        """
        result = await self._run(
            workspace,
            repo_abs,
            ["diff", *_DIFF_COMMON, "--patch", "--binary", "HEAD"],
            check=False,
            operation="confirmation_impact",
        )
        return impact_digest(result.stdout)

    def _require_confirmation(
        self,
        environment_id: str,
        repo_id: str,
        operation: str,
        impact: Dict[str, Any],
        token: Optional[str],
    ) -> None:
        """Gate a destructive operation on an impact-bound token."""
        if token and self.confirmations.redeem(
            token, environment_id, repo_id, operation, impact
        ):
            return
        fresh = self.confirmations.mint(environment_id, repo_id, operation, impact)
        raise GitConfirmationRequired(
            f"{operation} discards work and must be confirmed explicitly",
            operation=operation,
            repo=repo_id,
            confirmation_token=fresh,
            impact=impact,
            reason="stale_confirmation" if token else "confirmation_required",
        )

    async def _discard_unlocked(
        self,
        environment_id: str,
        repo: str,
        paths: Optional[Sequence[str]] = None,
        hunks: Optional[Sequence[Any]] = None,
        context_lines: Optional[int] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Throw away worktree changes, per file or per hunk.

        Destructive: the discarded content exists nowhere else, so the
        whole selection is resolved and validated first, the impact is
        confirmed, and the change is then applied as **one** reverse patch
        — there is no window in which half the selection is gone.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        path_list = [
            canonical_repo_path(item, allow_root=False) for item in (paths or [])
        ]
        hunk_list = list(hunks or [])
        if not path_list and not hunk_list:
            raise GitStateInvalid(
                "nothing to discard: provide paths, hunks, or both",
                reason="empty_selection",
                repo=repo_id,
            )
        selections = (
            await self._select_hunks(
                workspace,
                repo_id,
                repo_abs,
                scope="worktree",
                hunks=hunk_list,
                context_lines=context_lines,
            )
            if hunk_list
            else []
        )
        self._reject_overlap(path_list, selections)

        whole_file_patch = ""
        if path_list:
            tracked = await self._collect_diff(
                workspace, repo_id, repo_abs, scope="worktree", paths=path_list
            )
            missing = sorted(set(path_list) - {item.path for item in tracked})
            if missing:
                raise GitStateInvalid(
                    "these paths have no tracked worktree changes to discard",
                    reason="nothing_to_discard",
                    paths=missing,
                )
            binary_result = await self._run(
                workspace,
                repo_abs,
                [
                    "diff",
                    *_DIFF_COMMON,
                    "--patch",
                    "--binary",
                    "--diff-filter=u",
                    "--",
                    *path_list,
                ],
                operation="discard",
            )
            whole_file_patch = binary_result.stdout

        patch = build_patch(selections, direction="reverse")
        impact = {
            "paths": sorted(path_list),
            "hunks": sorted(
                f"{diff_file.path}:{hunk.hunk_id}"
                for diff_file, chosen in selections
                for hunk in chosen
            ),
            # Digest of the exact content that would be destroyed: a
            # token minted for one revision of the worktree cannot be
            # redeemed after that content changes.
            "content_digest": impact_digest([whole_file_patch, patch]),
        }
        self._require_confirmation(
            environment_id, repo_id, "discard_changes", impact, confirmation_token
        )

        combined = whole_file_patch
        if patch:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += patch
        if combined:
            await self._apply_patch(
                workspace,
                repo_abs,
                combined,
                args=["--reverse", "--binary", "--whitespace=nowarn"],
                operation="discard",
                git_dir_rel=git_dir_rel,
            )
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "discarded_paths": sorted(path_list),
            "discarded_hunks": [
                {"path": diff_file.path, "hunk_id": hunk.hunk_id}
                for diff_file, chosen in selections
                for hunk in chosen
            ],
        }

    async def _commit_unlocked(
        self,
        environment_id: str,
        repo: str,
        message: str,
        amend: Optional[bool] = None,
        allow_empty: Optional[bool] = None,
        author: Optional[str] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        if not isinstance(message, str) or not message.strip():
            raise GitStateInvalid(
                "a commit message is required", reason="empty_message", repo=repo_id
            )
        if amend:
            head = await self._run(
                workspace,
                repo_abs,
                ["rev-parse", "-q", "--verify", "HEAD"],
                check=False,
                operation="commit",
            )
            oid = head.stdout.strip()
            if not oid:
                raise GitStateInvalid(
                    "there is no commit to amend",
                    reason="unborn_head",
                    repo=repo_id,
                )
            self._require_confirmation(
                environment_id,
                repo_id,
                "amend_commit",
                {"head": oid},
                confirmation_token,
            )
        args = ["commit", "--message", message, "--no-verify"]
        if amend:
            args.append("--amend")
        if allow_empty:
            args.append("--allow-empty")
        if author:
            args.extend(["--author", author])
        result = await self._run(
            workspace,
            repo_abs,
            args,
            timeout=self._write_timeout,
            check=False,
            operation="commit",
            config=("-c", "commit.gpgsign=false"),
        )
        if result.exit_code != 0:
            status = await self.status(environment_id, repo_id)
            if not status["entries"] and not allow_empty:
                raise GitStateInvalid(
                    "nothing staged to commit",
                    reason="nothing_staged",
                    repo=repo_id,
                )
            raise GitOperationFailed(
                (result.stderr or result.stdout or "git commit failed").strip(),
                operation="commit",
                exit_code=result.exit_code,
                repo=repo_id,
                stderr=(result.stderr or "").strip() or None,
            )
        head = await self._run(
            workspace, repo_abs, ["rev-parse", "HEAD"], operation="commit"
        )
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "oid": head.stdout.strip(),
            "amended": bool(amend),
        }

    async def _checkout_unlocked(
        self,
        environment_id: str,
        repo: str,
        branch: Optional[str] = None,
        create: Optional[bool] = None,
        start_point: Optional[str] = None,
        detach: Optional[bool] = None,
        discard_changes: Optional[bool] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        if not branch:
            raise GitStateInvalid(
                "checkout requires a branch (or revision with detach)",
                reason="missing_branch",
                repo=repo_id,
            )
        # Parameters that would otherwise be accepted and quietly dropped.
        if detach and create:
            raise GitStateInvalid(
                "create and detach are mutually exclusive",
                reason="parameter_conflict",
                repo=repo_id,
            )
        if start_point and not create:
            raise GitStateInvalid(
                "start_point only applies when create is set",
                reason="parameter_not_applicable",
                repo=repo_id,
            )
        target = _checked_revision(branch)
        if discard_changes:
            status = await self.status(environment_id, repo_id)
            impact = {
                "target": target,
                "dirty_paths": sorted(
                    entry["path"] for entry in status["entries"] if entry["unstaged"]
                ),
                "staged_paths": sorted(
                    entry["path"] for entry in status["entries"] if entry["staged"]
                ),
                # Path lists alone would let a token minted for one
                # revision of the worktree redeem after the *content*
                # changed underneath — a parallel agent column rewriting
                # a file the user already confirmed. The digest pins the
                # exact bytes that would be thrown away.
                "content_digest": await self._worktree_digest(workspace, repo_abs),
            }
            self._require_confirmation(
                environment_id,
                repo_id,
                "checkout_discard_changes",
                impact,
                confirmation_token,
            )
        if detach:
            args = ["checkout", "--detach"]
            if discard_changes:
                args.append("--force")
            args.append(target)
        else:
            args = ["switch"]
            if create:
                args.append("--create")
            if discard_changes:
                args.append("--discard-changes")
            args.append(target)
            if create and start_point:
                args.append(_checked_revision(start_point))
        await self._run(
            workspace,
            repo_abs,
            args,
            timeout=self._write_timeout,
            operation="checkout",
            git_dir_rel=git_dir_rel,
        )
        return await self.status(environment_id, repo_id)

    async def _reset_unlocked(
        self,
        environment_id: str,
        repo: str,
        mode: Optional[str] = None,
        rev: Optional[str] = None,
        paths: Optional[Sequence[str]] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        reset_mode = (mode or "mixed").lower()
        if reset_mode not in ("soft", "mixed", "hard"):
            raise GitStateInvalid(
                f"unknown reset mode: {reset_mode!r}",
                reason="unknown_mode",
                supported=["soft", "mixed", "hard"],
            )
        path_list = [
            canonical_repo_path(item, allow_root=False) for item in (paths or [])
        ]
        if path_list and reset_mode != "mixed":
            raise GitStateInvalid(
                "a pathspec reset is always mixed",
                reason="incompatible_mode",
                mode=reset_mode,
            )
        target = _checked_revision(rev) if rev else "HEAD"
        if reset_mode == "hard":
            status = await self.status(environment_id, repo_id)
            head = await self._run(
                workspace,
                repo_abs,
                ["rev-parse", "-q", "--verify", "HEAD"],
                check=False,
                operation="reset",
            )
            target_probe = await self._run(
                workspace,
                repo_abs,
                ["rev-parse", "-q", "--verify", f"{target}^{{commit}}"],
                check=False,
                operation="reset",
            )
            impact = {
                "target": target,
                "target_oid": target_probe.stdout.strip() or None,
                "head_oid": head.stdout.strip() or None,
                "dirty_paths": sorted(entry["path"] for entry in status["entries"]),
                # See checkout: the path list is not enough — pin the
                # bytes, so a token cannot outlive the content it
                # described.
                "content_digest": await self._worktree_digest(workspace, repo_abs),
            }
            self._require_confirmation(
                environment_id, repo_id, "reset_hard", impact, confirmation_token
            )
        args = ["reset", f"--{reset_mode}", target]
        if path_list:
            args = ["reset", "--quiet", target, "--", *path_list]
        await self._run(
            workspace,
            repo_abs,
            args,
            timeout=self._write_timeout,
            operation="reset",
            git_dir_rel=git_dir_rel,
        )
        return await self.status(environment_id, repo_id)

    # ------------------------------------------------------------------
    #  Network operations
    # ------------------------------------------------------------------

    async def _resolve_remote(
        self,
        workspace: Workspace,
        repo_abs: str,
        repo_id: str,
        remote: Optional[str],
        refspec: Optional[str],
    ) -> Optional[str]:
        """Validate *remote* — its name **and** where its URL points.

        ``remote`` is a name, never a URL: accepting a URL let a caller
        point git at any path or scheme reachable from the sandbox, and
        (with a repository-local ``protocol.ext.allow``) execute a
        command through an ``ext::`` URL on a surface that runs no shell.
        ``ext::`` is additionally pinned off in :data:`_GIT_CONFIG_ARGS`.

        Name validation alone is not enough, though: repository config
        lives *inside* the workspace, so code in the environment can add
        ``git remote add leak /abs/outside`` and then fetch it. A
        **local** remote — a filesystem path or a ``file://`` URL —
        must therefore also resolve inside the workspace root. Network
        remotes (``https``/``ssh``/``git``) are left alone: they cross no
        workspace boundary, and refusing them would make the surface
        useless for its actual job.

        Also rejects a bare ``refspec``: ``git fetch <x>`` reads its
        first positional as the *repository*, so a refspec without a
        remote would be silently reinterpreted as one.
        """
        if refspec and not remote:
            raise GitStateInvalid(
                "refspec requires an explicit remote",
                reason="missing_remote",
                repo=repo_id,
            )
        if remote is None:
            return None
        name = _checked_remote(remote)
        listing = await self._run(
            workspace, repo_abs, ["remote"], operation="resolve_remote"
        )
        configured = {
            line.strip() for line in listing.stdout.split("\n") if line.strip()
        }
        if name not in configured:
            raise GitStateInvalid(
                "remote must name a remote configured in this repository",
                reason="unknown_remote",
                repo=repo_id,
                value=name,
                supported=sorted(configured),
            )
        urls = await self._run(
            workspace,
            repo_abs,
            ["remote", "get-url", "--all", name],
            check=False,
            operation="resolve_remote",
        )
        for url in (line.strip() for line in urls.stdout.split("\n")):
            if not url:
                continue
            local = local_remote_path(url)
            if local is None:
                continue
            target = (
                local if posixpath.isabs(local) else posixpath.join(repo_abs, local)
            )
            if not workspace.contains_path(posixpath.normpath(target)):
                raise GitStateInvalid(
                    "this remote points at a location outside the environment "
                    "workspace, so fetching it would import objects the "
                    "workspace boundary excludes",
                    reason="remote_outside_workspace",
                    repo=repo_id,
                    value=name,
                )
        return name

    async def _remote_refs(self, workspace: Workspace, repo_abs: str) -> Dict[str, str]:
        result = await self._run(
            workspace,
            repo_abs,
            ["for-each-ref", "--format=%(refname)%00%(objectname)", "refs/remotes"],
            operation="remote_refs",
        )
        refs: Dict[str, str] = {}
        for line in result.stdout.split("\n"):
            if not line.strip():
                continue
            name, _, oid = line.partition("\x00")
            refs[name] = oid
        return refs

    async def _fetch_unlocked(
        self,
        environment_id: str,
        repo: str,
        remote: Optional[str] = None,
        refspec: Optional[str] = None,
        prune: Optional[bool] = None,
        progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Fetch, reporting ref movement structurally.

        Remote-tracking refs are snapshotted before and after, so the
        result describes exactly which refs moved without reading a single
        line of git's progress output.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        remote_name = await self._resolve_remote(
            workspace, repo_abs, repo_id, remote, refspec
        )
        before = await self._remote_refs(workspace, repo_abs)
        updates: List[Dict[str, Any]] = []
        args = ["fetch", "--no-progress"]
        if prune:
            args.append("--prune")
        if remote_name:
            args.append(remote_name)
        if refspec:
            args.append(_checked_revision(refspec))
        async with _ProgressScope(progress, "fetch", repo_id, environment_id) as report:
            await self._run(
                workspace,
                repo_abs,
                args,
                timeout=self._network_timeout,
                operation="fetch",
            )
            after = await self._remote_refs(workspace, repo_abs)
            updates.extend(_ref_delta(before, after))
            await report({"updated_refs": len(updates)})
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "remote": remote_name,
            "updated_refs": updates,
        }

    async def _pull_unlocked(
        self,
        environment_id: str,
        repo: str,
        remote: Optional[str] = None,
        refspec: Optional[str] = None,
        rebase: Optional[bool] = None,
        progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Pull, treating conflicts as a *state* rather than an error.

        A pull that stops on a conflict is a legitimate outcome the client
        must render, so it returns ``ok: false`` with the conflicted paths
        and the repository state; only a failure that leaves no conflict
        raises.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        remote_name = await self._resolve_remote(
            workspace, repo_abs, repo_id, remote, refspec
        )
        args = ["pull", "--no-progress", "--no-edit"]
        args.append("--rebase" if rebase else "--no-rebase")
        if remote_name:
            args.append(remote_name)
        if refspec:
            args.append(_checked_revision(refspec))
        async with _ProgressScope(progress, "pull", repo_id, environment_id) as report:
            result = await self._run(
                workspace,
                repo_abs,
                args,
                timeout=self._network_timeout,
                check=False,
                operation="pull",
            )
            status = await self.status(environment_id, repo_id)
            await report({"exit_code": result.exit_code})
        if result.exit_code != 0 and not status["conflicted"]:
            raise GitOperationFailed(
                (result.stderr or result.stdout or "git pull failed").strip(),
                operation="pull",
                exit_code=result.exit_code,
                repo=repo_id,
                stderr=(result.stderr or "").strip() or None,
            )
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "ok": result.exit_code == 0,
            "state": status["state"],
            "conflicted": status["conflicted"],
            "head": status["head"],
        }

    async def _push_unlocked(
        self,
        environment_id: str,
        repo: str,
        remote: Optional[str] = None,
        refspec: Optional[str] = None,
        force: Optional[bool] = None,
        force_with_lease: Optional[bool] = None,
        set_upstream: Optional[bool] = None,
        confirmation_token: Optional[str] = None,
        progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Push, gated on what git says it will actually do.

        Destructive intent does not only live in the parameters. It can
        come from the *refspec* (``+main:main`` forces, ``:refs/heads/x``
        deletes) and, crucially, from **repository configuration** code in the
        environment can write — ``remote.<name>.push = +refs/…`` or
        ``remote.<name>.mirror = true`` both produce forced updates and
        deletions from a request that names neither.

        So the gate does not try to predict git's decision from the
        inputs. The *identical* argv is run once with ``--dry-run``
        first, and the confirmation is driven by the porcelain flags git
        itself reports (``+`` forced, ``-`` deleted). Whatever configures
        the push — parameter, refspec or config — shows up there.
        """
        resolved = await self._resolve_repo(environment_id, repo)
        workspace, repo_id, repo_abs, git_dir_rel = resolved.unpack()
        remote_name = await self._resolve_remote(
            workspace, repo_abs, repo_id, remote, refspec
        )
        args = ["push", "--porcelain", "--no-progress"]
        if force_with_lease:
            args.append("--force-with-lease")
        elif force:
            args.append("--force")
        if set_upstream:
            args.append("--set-upstream")
        if remote_name:
            args.append(remote_name)
        if refspec:
            args.append(_checked_revision(refspec))

        planned = await self._run(
            workspace,
            repo_abs,
            ["push", "--dry-run", *args[1:]],
            timeout=self._network_timeout,
            check=False,
            operation="push_plan",
        )
        plan = parse_push_porcelain(planned.stdout)
        # Parameters and refspec are still read, so an intent git cannot
        # yet see (a delete of a ref the remote does not have, say) is
        # gated anyway. The plan is what adds config-driven intent.
        intent = parse_refspec_intent(refspec)
        planned_force = [
            item for item in plan["updates"] if item["status"] == "forced_update"
        ]
        planned_delete = [
            item for item in plan["updates"] if item["status"] == "deleted"
        ]
        forcing = bool(force or force_with_lease or intent["force"] or planned_force)
        deleting = bool(intent["delete"] or planned_delete)
        if forcing or deleting:
            head = await self._run(
                workspace,
                repo_abs,
                ["rev-parse", "-q", "--verify", "HEAD"],
                check=False,
                operation="push",
            )
            operation = "delete_remote_ref" if deleting else "force_push"
            impact = {
                "remote": remote_name,
                "refspec": refspec,
                "force": forcing,
                "force_with_lease": bool(force_with_lease),
                "delete": deleting,
                "target_ref": intent["target"],
                "head_oid": head.stdout.strip() or None,
                # The refs git said it would rewrite or remove, with the
                # oids they would move from and to: the confirmation
                # describes the real consequence, not the request.
                "planned_updates": [
                    {
                        "status": item["status"],
                        "source_ref": item["source_ref"],
                        "target_ref": item["target_ref"],
                    }
                    for item in plan["updates"]
                    if item["status"] in ("forced_update", "deleted")
                ],
            }
            self._require_confirmation(
                environment_id, repo_id, operation, impact, confirmation_token
            )
        async with _ProgressScope(progress, "push", repo_id, environment_id) as report:
            result = await self._run(
                workspace,
                repo_abs,
                args,
                timeout=self._network_timeout,
                check=False,
                operation="push",
            )
            parsed = parse_push_porcelain(result.stdout)
            await report({"exit_code": result.exit_code})
        rejected = [item for item in parsed["updates"] if item["rejected"]]
        if result.exit_code != 0 and not rejected:
            raise GitOperationFailed(
                (result.stderr or result.stdout or "git push failed").strip(),
                operation="push",
                exit_code=result.exit_code,
                repo=repo_id,
                stderr=(result.stderr or "").strip() or None,
            )
        return {
            "environment_id": environment_id,
            "repo": repo_id,
            "ok": result.exit_code == 0,
            "remote_url": redact_remote_url(parsed["remote_url"]),
            "updates": parsed["updates"],
            "rejected": [item["target_ref"] for item in rejected],
        }

    # ------------------------------------------------------------------
    #  Serialized public entry points
    #
    #  Every mutating operation runs several git commands and git guards
    #  the index with a lockfile, so concurrent calls on one repository —
    #  the normal condition for a deck of parallel agent columns, or a
    #  user clicking "stage hunk" quickly — would lose to ``index.lock``.
    #  Failing closed is correct but useless; these wrappers serialize per
    #  (environment, repository) so the work actually lands, and classify any
    #  remaining contention (an *external* git process) structurally as
    #  ``reason="index_locked"`` rather than by reading git's prose.
    # ------------------------------------------------------------------

    async def stage(
        self,
        environment_id: str,
        repo: str,
        paths: Optional[Sequence[str]] = None,
        hunks: Optional[Sequence[Any]] = None,
        context_lines: Optional[int] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._stage_unlocked(
                environment_id,
                repo,
                paths=paths,
                hunks=hunks,
                context_lines=context_lines,
                mode=mode,
            )

    async def unstage(
        self,
        environment_id: str,
        repo: str,
        paths: Optional[Sequence[str]] = None,
        hunks: Optional[Sequence[Any]] = None,
        context_lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._unstage_unlocked(
                environment_id,
                repo,
                paths=paths,
                hunks=hunks,
                context_lines=context_lines,
            )

    async def discard(
        self,
        environment_id: str,
        repo: str,
        paths: Optional[Sequence[str]] = None,
        hunks: Optional[Sequence[Any]] = None,
        context_lines: Optional[int] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._discard_unlocked(
                environment_id,
                repo,
                paths=paths,
                hunks=hunks,
                context_lines=context_lines,
                confirmation_token=confirmation_token,
            )

    async def commit(
        self,
        environment_id: str,
        repo: str,
        message: str,
        amend: Optional[bool] = None,
        allow_empty: Optional[bool] = None,
        author: Optional[str] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._commit_unlocked(
                environment_id,
                repo,
                message,
                amend=amend,
                allow_empty=allow_empty,
                author=author,
                confirmation_token=confirmation_token,
            )

    async def checkout(
        self,
        environment_id: str,
        repo: str,
        branch: Optional[str] = None,
        create: Optional[bool] = None,
        start_point: Optional[str] = None,
        detach: Optional[bool] = None,
        discard_changes: Optional[bool] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._checkout_unlocked(
                environment_id,
                repo,
                branch=branch,
                create=create,
                start_point=start_point,
                detach=detach,
                discard_changes=discard_changes,
                confirmation_token=confirmation_token,
            )

    async def reset(
        self,
        environment_id: str,
        repo: str,
        mode: Optional[str] = None,
        rev: Optional[str] = None,
        paths: Optional[Sequence[str]] = None,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._reset_unlocked(
                environment_id,
                repo,
                mode=mode,
                rev=rev,
                paths=paths,
                confirmation_token=confirmation_token,
            )

    async def fetch(
        self,
        environment_id: str,
        repo: str,
        remote: Optional[str] = None,
        refspec: Optional[str] = None,
        prune: Optional[bool] = None,
        progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._fetch_unlocked(
                environment_id,
                repo,
                remote=remote,
                refspec=refspec,
                prune=prune,
                progress=progress,
            )

    async def pull(
        self,
        environment_id: str,
        repo: str,
        remote: Optional[str] = None,
        refspec: Optional[str] = None,
        rebase: Optional[bool] = None,
        progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._pull_unlocked(
                environment_id,
                repo,
                remote=remote,
                refspec=refspec,
                rebase=rebase,
                progress=progress,
            )

    async def push(
        self,
        environment_id: str,
        repo: str,
        remote: Optional[str] = None,
        refspec: Optional[str] = None,
        force: Optional[bool] = None,
        force_with_lease: Optional[bool] = None,
        set_upstream: Optional[bool] = None,
        confirmation_token: Optional[str] = None,
        progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        async with self._serialized(environment_id, repo):
            return await self._push_unlocked(
                environment_id,
                repo,
                remote=remote,
                refspec=refspec,
                force=force,
                force_with_lease=force_with_lease,
                set_upstream=set_upstream,
                confirmation_token=confirmation_token,
                progress=progress,
            )


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _path_module(workspace: Any) -> Any:
    """The workspace's path syntax (``Workspace.path_module``); POSIX for
    duck-typed workspaces that don't declare one."""
    return getattr(workspace, "path_module", posixpath)


def _same_path(left: str, right: str, path_module: Any = posixpath) -> bool:
    """Whether two absolute paths name the same location, in the workspace's
    path syntax. git reports Windows paths with forward slashes
    (``C:/Users/...``); the workspace spells them ``C:\\Users\\...``."""
    pm = path_module
    return pm.normcase(pm.normpath(left)) == pm.normcase(pm.normpath(right))


def _relative_to(candidate: str, root: str) -> Optional[str]:
    """Workspace-relative POSIX path of *candidate*, or None when outside."""
    candidate = candidate.rstrip("/")
    root = root.rstrip("/")
    if candidate == root:
        return "."
    prefix = root + "/"
    if not candidate.startswith(prefix):
        return None
    return candidate[len(prefix) :]


def _checked_revision(value: str) -> str:
    """Reject revisions/refspecs that could be read as git options.

    Commands are argv lists, so there is no shell to escape from; the only
    injection left is a value that git itself would treat as an option.
    """
    if not isinstance(value, str) or not value.strip():
        raise GitStateInvalid("a revision is required", reason="missing_revision")
    text = value.strip()
    if text.startswith("-"):
        raise GitStateInvalid(
            "a revision may not start with '-'",
            reason="invalid_revision",
            value=text,
        )
    if "\x00" in text or "\n" in text:
        raise GitStateInvalid(
            "a revision may not contain control characters",
            reason="invalid_revision",
        )
    return text


def _checked_remote(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GitStateInvalid("a remote name is required", reason="missing_remote")
    text = value.strip()
    if text.startswith("-") or "\x00" in text or "\n" in text:
        raise GitStateInvalid(
            "invalid remote name", reason="invalid_remote", value=text
        )
    return text


def _ref_delta(before: Dict[str, str], after: Dict[str, str]) -> List[Dict[str, Any]]:
    """Structural description of how remote-tracking refs moved."""
    updates: List[Dict[str, Any]] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if old == new:
            continue
        updates.append(
            {
                "ref": name,
                "old_oid": old,
                "new_oid": new,
                "change": (
                    "created"
                    if old is None
                    else "deleted" if new is None else "updated"
                ),
            }
        )
    return updates


class _ProgressScope:
    """Emit structured ``started``/``completed``/``failed`` progress.

    The phases are this module's own lifecycle, not a reading of git's
    progress output — which is exactly why they are safe to expose: a
    client can drive a spinner without any assumption about git's
    human-facing text.
    """

    def __init__(
        self,
        emit: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        operation: str,
        repo: str,
        environment_id: str,
    ) -> None:
        self._emit = emit
        self._operation = operation
        self._repo = repo
        self._environment_id = environment_id
        self._operation_id = f"gitop_{secrets.token_hex(8)}"
        self._detail: Dict[str, Any] = {}

    async def _send(self, phase: str, detail: Optional[Dict[str, Any]] = None) -> None:
        if self._emit is None:
            return
        payload: Dict[str, Any] = {
            "environment_id": self._environment_id,
            "operation_id": self._operation_id,
            "repo": self._repo,
            "operation": self._operation,
            "phase": phase,
        }
        merged = {**self._detail, **(detail or {})}
        if merged:
            payload["detail"] = merged
        try:
            await self._emit(payload)
        except Exception:  # pragma: no cover - notification best effort
            logger.debug("git progress notification failed", exc_info=True)

    async def __aenter__(self) -> Callable[[Dict[str, Any]], Awaitable[None]]:
        await self._send("started")

        async def report(detail: Dict[str, Any]) -> None:
            self._detail.update(detail)

        return report

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            await self._send("completed")
        else:
            detail: Dict[str, Any] = {}
            if isinstance(exc, GitRpcError):
                detail["error_kind"] = exc.kind
            await self._send("failed", detail)
        return False
