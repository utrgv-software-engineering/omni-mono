"""Run-level unified diff: the NET workspace change a turn is responsible for.

Individual edits already exist as ``tool_call`` items carrying
``content.tool_kind == "file"``, and each one ships its own little diff in
``ui_metadata``. What has nowhere to live is the aggregate — "what did this
whole run change?" — which is what a reviewer actually wants and what
``ItemKind.RUN_DIFF`` was reserved for.

Why content snapshots and not composed per-edit patches
-------------------------------------------------------

The acceptance bar is that the aggregate equals the *net* change
attributable to the run. Composing the per-edit patches does not clear it:

* Two edits to the same file yield two overlapping patches whose textual
  concatenation is not a valid patch against the original file, and whose
  composition requires replaying them in order against the pre-image
  anyway.
* A file created and then deleted inside one run yields an "add" patch and
  a "delete" patch; the net change is *nothing*, and the run diff must show
  nothing rather than two entries that cancel.

So the accumulator stores, per path, the content at **first touch** and the
content after the **latest touch**, and diffs those two. Both cases fall out
for free: repeated edits collapse because only the first pre-image is kept,
and create-then-delete drops out because before == after == absent.

Why not the typed git surface
-----------------------------

``git_diff`` (tkt_l_l0lUi4A5V2) is the wrong instrument here even though it
is right next door:

* **Attribution.** ``git diff`` reports the working tree against the index
  or a revision, so a workspace that was already dirty when the run started
  attributes the user's pre-existing edits to the agent. The run diff must
  be what *this run* did.
* **Availability.** A workspace is not required to be a git repository, and
  most sandbox workspaces are not. A run-diff that silently produced
  nothing outside a repo would be a feature that looks present and defends
  nothing.
* **Path semantics.** ``Workspace`` path meaning differs between host and
  sandbox (tkt_z8T1amZ-GUs0, open), so a host-side git invocation cannot be
  assumed to see the same tree the agent edited. Snapshotting *through the
  active workspace* sidesteps the question entirely: whatever the tools
  wrote is what we read back.

The git surface stays the right tool for "what is uncommitted in this repo
right now", which is a different question with a different consumer.

Known boundary — read this before trusting "net"
-----------------------------------------------

The accumulator observes the workspace's *content* primitives:
:func:`~omniagents.workspace.write_text`,
:func:`~omniagents.workspace.write_bytes` and
:func:`~omniagents.workspace.remove`. That covers every builtin FILE tool.
It does **not** cover :func:`~omniagents.workspace.exec` /
:func:`~omniagents.workspace.spawn`, which is how ``execute_bash`` runs
(``builtin/tools/bash_tool.py``) — and that is not an exotic path:

* formatters and codegen the agent runs on its own output — ``npm run fix``,
  ``black``, ``ruff --fix``, ``prettier -w``, and in this repository
  ``scripts/protocol/generate_gui_protocol``;
* ``git checkout`` / ``git stash`` / ``git apply``, ``mv``, ``cp``;
* dependency installs rewriting a lockfile.

A run that edits three files and then formats them ships a diff for the
three edits and *not* the formatter's rewrite of them; a run that
regenerates artifacts entirely through a script ships a diff that omits
them. So the run diff is the net change **the agent's file tools made**, not
the net change to the workspace, and for a formatter- or codegen-heavy run
those differ. Instrumenting ``exec`` (a post-command rescan of the paths a
command could have touched) is the fix and is recorded as a follow-up.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_SNAPSHOT_BYTES",
    "MAX_DIFF_CHARS",
    "MAX_DIFF_FILES",
    "FileChange",
    "RunDiffAccumulator",
    "RunDiffObserver",
    "unified_diff_for",
]

#: Per-side cap on the content we hold for one path. Above it the change is
#: still REPORTED (path + change type) but carries no hunks: a run diff that
#: silently omitted a 400 MB file would be worse than one that says "this
#: file changed, too large to render".
MAX_SNAPSHOT_BYTES = 1_048_576

#: Cap on how many per-file entries ride the snapshot. ``MAX_DIFF_CHARS``
#: bounds the rendered text but nothing bounded ``files``, so a 2,000-file
#: run pushed a ~400 KB JSON notification after EVERY mutation.
#: ``stats.files_changed`` stays the true total, and ``files_truncated``
#: says the list was cut.
MAX_DIFF_FILES = 500

#: Cap on the rendered diff text. The snapshot rides a WebSocket
#: notification and lands in a JSON column; an unbounded one stalls the
#: socket (the ``MAX_PAGE_SIZE`` posture). ``truncated`` says so explicitly.
MAX_DIFF_CHARS = 400_000

#: Sentinel distinguishing "we could not hold this content" from "the file
#: does not exist". Both are non-``str``; only the latter is ``None``.
_OVERSIZE = object()


@dataclass
class FileChange:
    """One path's net change across a run."""

    path: str
    #: Content at first touch. ``None`` = the path did not exist.
    before: Optional[str]
    #: Content after the latest touch. ``None`` = the path does not exist.
    after: Optional[str]
    #: True when either side was binary or above :data:`MAX_SNAPSHOT_BYTES`,
    #: so no textual diff can be produced for it.
    opaque: bool = False
    before_exists: bool = False
    after_exists: bool = False
    #: False when the run's baseline for this path was never observed — the
    #: observer was bound mid-run, or the pre-image read failed. The change
    #: is then reported WITHOUT claiming creation or deletion, because
    #: either claim would be a guess about a state nobody saw.
    before_known: bool = True
    #: Content digests, present whenever bytes were read for that side.
    before_digest: Optional[str] = None
    after_digest: Optional[str] = None
    #: Memoized :meth:`hunks`. The snapshot is rebuilt after EVERY observed
    #: edit and both the rendered diff and the per-file add/delete counts
    #: need the same hunks, so without this a run touching N files pays for
    #: 2N diffs on every single edit.
    _hunks: Optional[List[str]] = field(default=None, repr=False, compare=False)
    #: Memoized :meth:`stats`. Memoizing ``hunks`` alone was not enough:
    #: ``stats`` re-scanned every one of those cached lines on every
    #: snapshot, so a run touching N files re-walked all of their diff lines
    #: on every mutation. At 400 files of 10 KB that was 80.5M
    #: ``str.startswith`` calls and 7.1s of the run's own write path.
    _stats: Optional[Dict[str, int]] = field(default=None, repr=False, compare=False)

    @property
    def change_type(self) -> str:
        if not self.before_known:
            # Never assert creation from an unobserved baseline.
            return "modified" if self.after_exists else "deleted"
        if not self.before_exists and self.after_exists:
            return "added"
        if self.before_exists and not self.after_exists:
            return "deleted"
        return "modified"

    @property
    def is_noop(self) -> bool:
        """True when the run left this path exactly as it found it.

        Covers the two cases the ticket names explicitly: a file created and
        then deleted (neither side exists), and a file edited and then
        reverted (both sides equal).
        """
        if not self.before_known:
            # A mutation was definitely observed; we just cannot prove what
            # it did. Reporting nothing would hide a real change.
            return False
        if self.before_exists != self.after_exists:
            return False
        if not self.before_exists:
            return True
        if self.opaque:
            # A binary or oversize file: the bytes are not held, so equality
            # is decided on the digests. Only when a digest is missing do we
            # fall back to reporting a change — claiming "unchanged" without
            # evidence would hide a real edit.
            if self.before_digest is not None and self.after_digest is not None:
                return self.before_digest == self.after_digest
            return False
        return self.before == self.after

    def hunks(self) -> List[str]:
        """Unified-diff lines for this path, or ``[]`` when unrenderable."""
        if self._hunks is not None:
            return self._hunks
        if self.opaque or not self.before_known:
            self._hunks = []
            return self._hunks
        before_lines = (self.before or "").splitlines(keepends=True)
        after_lines = (self.after or "").splitlines(keepends=True)
        from_file = f"a/{self.path}" if self.before_exists else "/dev/null"
        to_file = f"b/{self.path}" if self.after_exists else "/dev/null"
        self._hunks = list(
            difflib.unified_diff(
                before_lines, after_lines, fromfile=from_file, tofile=to_file, n=3
            )
        )
        return self._hunks

    def stats(self) -> Dict[str, int]:
        if self._stats is not None:
            return self._stats
        additions = 0
        deletions = 0
        for line in self.hunks():
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
        self._stats = {"additions": additions, "deletions": deletions}
        return self._stats

    def to_dict(self) -> Dict[str, Any]:
        stats = self.stats()
        return {
            "path": self.path,
            "change_type": self.change_type,
            "additions": stats["additions"],
            "deletions": stats["deletions"],
            "opaque": self.opaque,
            "baseline_unknown": not self.before_known,
        }


def _render_header(change: FileChange) -> str:
    """``diff --git`` header plus the add/delete markers git itself emits.

    The output is a real unified diff, not a bespoke shape, so a client can
    hand it to any existing diff renderer.
    """
    lines = [f"diff --git a/{change.path} b/{change.path}\n"]
    if change.change_type == "added":
        lines.append("new file\n")
    elif change.change_type == "deleted":
        lines.append("deleted file\n")
    if change.opaque:
        lines.append(
            f"Binary or oversize files a/{change.path} and b/{change.path} differ\n"
        )
    return "".join(lines)


def unified_diff_for(changes: List[FileChange]) -> tuple[str, bool]:
    """Render *changes* as one unified diff. Returns ``(text, truncated)``.

    Truncation drops WHOLE FILE BLOCKS, never part of one. A diff cut
    mid-hunk is not a unified diff: the line counts in the surviving ``@@``
    header no longer match the body, so every renderer either errors or
    silently mis-applies it — and the point of this surface is that a client
    does not have to parse or repair anything.

    ``files`` and ``stats`` on the snapshot are NOT truncated, so a client
    always knows the full set of paths the run touched even when the
    rendered text stops early.
    """
    parts: List[str] = []
    total = 0
    truncated = False
    for change in changes:
        block = _render_header(change) + "".join(change.hunks())
        if total + len(block) > MAX_DIFF_CHARS:
            truncated = True
            break
        parts.append(block)
        total += len(block)
    return "".join(parts), truncated


@dataclass(frozen=True)
class _Capture:
    """One successful read of a path.

    Absence is a VALUE here (``exists=False``); a failed read is ``None`` at
    the call site. ``trackable=False`` means the path is real but is not a
    file whose content can be diffed (a directory).
    """

    key: str
    content: Optional[str]
    exists: bool
    opaque: bool
    trackable: bool = True
    #: Content digest, present whenever bytes were read. It is the ONLY way
    #: to tell "this binary was rewritten identically" from "this binary
    #: changed", and without it every rewrite of an opaque file is reported
    #: as a modification the run may not have made.
    digest: Optional[str] = None


@dataclass
class _Entry:
    before: Any = None
    before_exists: bool = False
    before_captured: bool = False
    before_digest: Optional[str] = None
    after: Any = None
    after_exists: bool = False
    after_digest: Optional[str] = None
    #: See :attr:`FileChange.before_known`.
    before_known: bool = True
    #: Memoized :class:`FileChange` for this path, invalidated whenever the
    #: path is touched. ``changes()`` runs after EVERY observed mutation, so
    #: rebuilding (and re-diffing) every path each time is quadratic in the
    #: number of edits — and it happens inside the write path.
    change: "Optional[FileChange]" = None


@dataclass
class RunDiffAccumulator:
    """Per-path first-touch / latest-touch content, for one run.

    Not thread-safe on purpose: one accumulator belongs to one run, and a
    run's tool calls are driven from a single event loop. Sharing one across
    runs is exactly the singleton mistake this ticket removes, so the
    accumulator is created per run and discarded with it.
    """

    entries: Dict[str, _Entry] = field(default_factory=dict)

    def note_before(
        self,
        path: str,
        content: Optional[str],
        *,
        exists: bool,
        opaque: bool = False,
        digest: Optional[str] = None,
    ) -> None:
        """Record the pre-image of *path*. Only the FIRST call per path wins.

        That is the whole net-diff rule: the second edit of a file must not
        overwrite the state the run inherited, or repeated edits would each
        look like a fresh baseline and the aggregate would show only the
        last one.
        """
        entry = self.entries.setdefault(path, _Entry())
        if entry.before_captured:
            return
        entry.change = None
        entry.before_captured = True
        entry.before = _OVERSIZE if opaque else content
        entry.before_exists = exists
        entry.before_digest = digest
        # An unwitnessed post-image would render the path as "deleted"; seed
        # it with the pre-image so a mutation whose after-capture failed is
        # a no-op rather than a fabricated deletion.
        entry.after = entry.before
        entry.after_exists = exists
        entry.after_digest = digest

    def note_after(
        self,
        path: str,
        content: Optional[str],
        *,
        exists: bool,
        opaque: bool = False,
        digest: Optional[str] = None,
    ) -> None:
        """Record the post-image of *path*. Every call wins (latest touch)."""
        entry = self.entries.setdefault(path, _Entry())
        entry.change = None
        if not entry.before_captured:
            # No pre-image was taken (the observer was installed mid-run, or
            # the pre-read failed). Record the change but mark the baseline
            # UNOBSERVED: dropping it would hide a real edit, and treating
            # the path as previously absent would fabricate a creation for a
            # file that may well have existed.
            entry.before_captured = True
            entry.before = None
            entry.before_exists = False
            entry.before_known = False
        entry.after = _OVERSIZE if opaque else content
        entry.after_exists = exists
        entry.after_digest = digest

    def changes(self) -> List[FileChange]:
        """Net changes, path-sorted, with no-ops dropped.

        Each path's :class:`FileChange` — and therefore its diff — is built
        once and reused until that path is touched again. Rebuilding all of
        them on every call re-diffed every file the run had touched after
        every single edit, inside the write path.
        """
        result: List[FileChange] = []
        for path in sorted(self.entries):
            entry = self.entries[path]
            change = entry.change
            if change is None:
                change = FileChange(
                    path=path,
                    before=entry.before if isinstance(entry.before, str) else None,
                    after=entry.after if isinstance(entry.after, str) else None,
                    opaque=(entry.before is _OVERSIZE or entry.after is _OVERSIZE),
                    before_exists=entry.before_exists,
                    after_exists=entry.after_exists,
                    before_known=entry.before_known,
                    before_digest=entry.before_digest,
                    after_digest=entry.after_digest,
                )
                entry.change = change
            if change.is_noop:
                continue
            result.append(change)
        return result

    def snapshot(self) -> Dict[str, Any]:
        """The persisted ``run_diff`` item content."""
        changes = self.changes()
        diff_text, truncated = unified_diff_for(changes)
        entries = [change.to_dict() for change in changes]
        return {
            "diff": diff_text,
            "files": entries[:MAX_DIFF_FILES],
            "files_truncated": len(entries) > MAX_DIFF_FILES,
            "truncated": truncated,
            "stats": {
                # The TRUE totals, over every changed path — not over the
                # (possibly capped) ``files`` list a client received.
                "files_changed": len(entries),
                "additions": sum(entry["additions"] for entry in entries),
                "deletions": sum(entry["deletions"] for entry in entries),
            },
        }


class RunDiffObserver:
    """Feeds a :class:`RunDiffAccumulator` from workspace mutations.

    Implements
    :class:`~omniagents.workspace._runtime.WorkspaceMutationObserver`. Bound
    per run (never per process) and reads content back through the *active*
    workspace, so a sandboxed run snapshots container paths through the
    sandbox session and a host run snapshots the host — which is why this
    does not have to resolve the host/sandbox path ambiguity that
    tkt_z8T1amZ-GUs0 tracks.

    ``publish`` is awaited after every mutation with the current snapshot.
    It is the caller's job (the runtime bridge) to persist the ``run_diff``
    item and push ``item_updated``; keeping that out of here is what lets
    the aggregation logic be tested without a session, a store, or a socket.
    """

    def __init__(
        self,
        publish: Callable[[Dict[str, Any]], Awaitable[None]],
        *,
        accumulator: Optional[RunDiffAccumulator] = None,
    ) -> None:
        self._publish = publish
        self.accumulator = accumulator or RunDiffAccumulator()
        self._published = False
        #: Directory keys the pre-phase could not enumerate. Remembered
        #: because after a recursive removal the path is simply gone, and
        #: "gone" would otherwise read as a deletion of a file that never
        #: existed.
        self._untracked: set[str] = set()
        #: Directory key → the file keys captured under it at pre-mutation
        #: time, so a recursive removal can be resolved per member.
        self._trees: Dict[str, List[str]] = {}

    async def before_mutation(self, path: str) -> None:
        captured = await self._capture(path)
        if captured is None:
            return
        if not captured.trackable:
            # A directory. Expand it to its current file set so a recursive
            # removal is reported as the deletion of those files. Dropping
            # the hook (the previous behaviour) did worse than under-report:
            # files created earlier in the run and then removed with the
            # tree stayed in the aggregate as ``added``, asserting the
            # existence of paths that are gone.
            await self._note_tree(captured.key, path, phase="before")
            return
        self._untracked.discard(captured.key)
        self._note(captured, phase="before")

    async def after_mutation(self, path: str) -> None:
        captured = await self._capture(path)
        if captured is None:
            # The post-image could not be read (a transient filesystem or
            # sandbox fault, an unaddressable path). Recording it as
            # "absent" would be indistinguishable from a deletion, and the
            # run diff is the authoritative record of what the run did — a
            # fabricated deletion there is worse than a missed edit. The
            # seed ``note_before`` installed keeps the entry a no-op.
            return
        tree = self._trees.get(captured.key)
        if tree is not None:
            for member in tree:
                await self._recapture(member)
        elif not captured.trackable or captured.key in self._untracked:
            # Still a directory and we never managed to enumerate it.
            return
        else:
            self._note(captured, phase="after")
        await self._maybe_publish()

    async def finalize(self) -> None:
        """Re-read every tracked path and publish the settled aggregate.

        The observer only fires on the workspace's content primitives, so a
        file the agent wrote through ``write_file`` and then rewrote with a
        shell command (a formatter, ``git checkout``, ``mv``) kept the
        post-image captured at API-write time. The persisted diff then did
        not merely omit the shell's change — it ASSERTED content that is not
        on disk. One re-capture pass at the end of the run makes the settled
        aggregate agree with the workspace for every path the run touched.

        Paths the run only ever touched through a shell are still invisible;
        that boundary is documented on this module.
        """
        tracked = list(self.accumulator.entries)
        reconciled = 0
        for key in tracked:
            if await self._recapture(key):
                reconciled += 1
        if tracked and not reconciled:
            # Every re-read failed. The most likely cause is that the run's
            # workspace is already gone (a closed sandbox session), in which
            # case this method silently did nothing and the persisted diff
            # keeps its pre-finalize content. The whole point of the pass is
            # that the settled diff agrees with disk, so degrading to a
            # no-op is worth a signal rather than a debug line nobody reads.
            logger.warning(
                "run diff finalization reconciled none of %d tracked paths; "
                "the settled diff may not reflect the workspace",
                len(tracked),
            )
        await self._maybe_publish()

    # -- internals -------------------------------------------------------

    def _note(self, captured: _Capture, *, phase: str) -> None:
        note = (
            self.accumulator.note_before
            if phase == "before"
            else self.accumulator.note_after
        )
        note(
            captured.key,
            captured.content,
            exists=captured.exists,
            opaque=captured.opaque,
            digest=captured.digest,
        )

    async def _recapture(self, key: str) -> bool:
        """Refresh the post-image of an already-tracked path.

        ``False`` when nothing was recorded — the read failed, or the path
        is no longer a diffable file. Both are cases where guessing would be
        worse than leaving the last known state in place.
        """
        captured = await self._capture(key)
        if captured is None or not captured.trackable:
            return False
        self._note(captured, phase="after")
        return True

    async def _note_tree(self, key: str, path: str, *, phase: str) -> None:
        """Record every file under directory *path*."""
        from omniagents import workspace as ws

        try:
            members = await ws.glob("**/*", root=path, include_hidden=True)
        except Exception:
            logger.debug("run diff tree walk failed for %s", path, exc_info=True)
            self._untracked.add(key)
            return
        captured_keys: List[str] = []
        for member in members:
            member_capture = await self._capture(member)
            if member_capture is None or not member_capture.trackable:
                continue
            captured_keys.append(member_capture.key)
            self._note(member_capture, phase=phase)
        self._trees[key] = captured_keys

    async def _maybe_publish(self) -> None:
        snapshot = self.accumulator.snapshot()
        if not snapshot["files"] and not self._published:
            # Nothing net-changed yet (a write that produced identical
            # bytes, or a create-then-delete still in flight). Do not mint a
            # run_diff item for a run that has changed nothing. Once one
            # exists it keeps being updated, including back down to empty —
            # a diff that went to zero is a fact the client must see.
            return
        self._published = True
        await self._publish(snapshot)

    # -- capture ---------------------------------------------------------

    async def _capture(self, path: str) -> Optional[_Capture]:
        """Read *path* through the active workspace.

        Returns ``None`` — meaning **"do not record anything"** — when the
        path cannot be addressed (an escape attempt) or the read RAISES.
        The second case is the important one: a failed read is
        NOT the same fact as "the file is not there", and collapsing the two
        is how a transient fault turns into a fabricated deletion in the
        authoritative run diff. A file that is genuinely absent comes back
        as a ``_Capture`` with ``exists=False``.
        """
        from omniagents import workspace as ws

        try:
            key = ws.relative_to_workspace(path)
        except Exception:
            return None
        try:
            if not await ws.exists(path):
                return _Capture(key=key, content=None, exists=False, opaque=False)
            # A directory is not a file change. Recursive removal of a tree
            # is reported as no textual diff rather than as a bogus
            # single-path deletion — under-reporting here is honest, and
            # ``files`` never gains an entry that cannot be rendered.
            if not await ws.is_file(path):
                return _Capture(
                    key=key,
                    content=None,
                    exists=True,
                    opaque=False,
                    trackable=False,
                )
            data = await ws.read_bytes(path)
        except Exception:
            logger.debug("run diff capture failed for %s", path, exc_info=True)
            return None
        digest = hashlib.sha256(data).hexdigest()
        if len(data) > MAX_SNAPSHOT_BYTES:
            return _Capture(
                key=key, content=None, exists=True, opaque=True, digest=digest
            )
        try:
            return _Capture(
                key=key,
                content=data.decode("utf-8"),
                exists=True,
                opaque=False,
                digest=digest,
            )
        except UnicodeDecodeError:
            return _Capture(
                key=key, content=None, exists=True, opaque=True, digest=digest
            )
