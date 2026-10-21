"""Run-level aggregated diffs.

The acceptance criterion is that the aggregate equals the NET workspace
change attributable to the run, so the two cases the ticket names — several
edits to the same file, and a file created then deleted inside one run —
are asserted directly against the rendered diff, not against intermediate
bookkeeping.
"""

import pytest

from omniagents.core.conversation.model import ItemKind, ItemStatus, derive_item_id
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.run_diff import (
    MAX_DIFF_CHARS,
    MAX_DIFF_FILES,
    MAX_SNAPSHOT_BYTES,
    RunDiffAccumulator,
    RunDiffObserver,
)
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.workspace import (
    HostWorkspace,
    WorkspaceEscapeError,
    reset_mutation_observer,
    reset_workspace,
    set_mutation_observer,
    set_workspace,
)
import omniagents.workspace as ws

THREAD = "sess-diff"
RUN = "run-1"


@pytest.fixture
def store(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    yield backend
    backend.close()


@pytest.fixture
def recorder(store):
    rec = ConversationRecorder(store)
    rec.ensure_thread(THREAD)
    return rec


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    token = set_workspace(HostWorkspace(root=str(root)))
    try:
        yield root
    finally:
        reset_workspace(token)


@pytest.fixture
def observed(workspace):
    """A live observer bound to the workspace, collecting published snapshots."""
    published = []

    async def publish(snapshot):
        published.append(snapshot)

    observer = RunDiffObserver(publish)
    token = set_mutation_observer(observer)
    try:
        yield observer, published
    finally:
        reset_mutation_observer(token)


# ---------------------------------------------------------------------
# Aggregation semantics (pure)
# ---------------------------------------------------------------------


class TestAggregation:
    def test_multiple_edits_to_one_file_yield_one_net_diff(self):
        acc = RunDiffAccumulator()
        acc.note_before("a.txt", "one\n", exists=True)
        acc.note_after("a.txt", "two\n", exists=True)
        acc.note_before("a.txt", "two\n", exists=True)  # ignored: first touch wins
        acc.note_after("a.txt", "three\n", exists=True)

        snapshot = acc.snapshot()
        assert snapshot["stats"]["files_changed"] == 1
        assert snapshot["diff"].count("diff --git a/a.txt b/a.txt") == 1
        # The intermediate state must not appear at all: the run's net effect
        # is one → three.
        assert "-one" in snapshot["diff"]
        assert "+three" in snapshot["diff"]
        assert "+two" not in snapshot["diff"]
        assert "-two" not in snapshot["diff"]
        assert snapshot["stats"] == {
            "files_changed": 1,
            "additions": 1,
            "deletions": 1,
        }

    def test_create_then_delete_within_one_run_is_no_change(self):
        acc = RunDiffAccumulator()
        acc.note_before("tmp.txt", None, exists=False)
        acc.note_after("tmp.txt", "scratch\n", exists=True)
        acc.note_after("tmp.txt", None, exists=False)

        snapshot = acc.snapshot()
        assert snapshot["files"] == []
        assert snapshot["diff"] == ""
        assert snapshot["stats"]["files_changed"] == 0

    def test_edit_then_revert_is_no_change(self):
        acc = RunDiffAccumulator()
        acc.note_before("a.txt", "same\n", exists=True)
        acc.note_after("a.txt", "different\n", exists=True)
        acc.note_after("a.txt", "same\n", exists=True)
        assert acc.snapshot()["files"] == []

    def test_added_file_diffs_against_dev_null(self):
        acc = RunDiffAccumulator()
        acc.note_before("new.txt", None, exists=False)
        acc.note_after("new.txt", "hello\n", exists=True)
        snapshot = acc.snapshot()
        assert snapshot["files"][0]["change_type"] == "added"
        assert "--- /dev/null" in snapshot["diff"]
        assert "+++ b/new.txt" in snapshot["diff"]
        assert snapshot["stats"] == {
            "files_changed": 1,
            "additions": 1,
            "deletions": 0,
        }

    def test_deleted_file_diffs_to_dev_null(self):
        acc = RunDiffAccumulator()
        acc.note_before("gone.txt", "bye\n", exists=True)
        acc.note_after("gone.txt", None, exists=False)
        snapshot = acc.snapshot()
        assert snapshot["files"][0]["change_type"] == "deleted"
        assert "--- a/gone.txt" in snapshot["diff"]
        assert "+++ /dev/null" in snapshot["diff"]

    def test_files_are_path_sorted_for_a_stable_rendering(self):
        acc = RunDiffAccumulator()
        for path in ("z.txt", "a.txt", "m.txt"):
            acc.note_before(path, None, exists=False)
            acc.note_after(path, "x\n", exists=True)
        assert [entry["path"] for entry in acc.snapshot()["files"]] == [
            "a.txt",
            "m.txt",
            "z.txt",
        ]

    def test_binary_change_is_reported_without_hunks(self):
        acc = RunDiffAccumulator()
        acc.note_before("img.png", None, exists=True, opaque=True)
        acc.note_after("img.png", None, exists=True, opaque=True)
        snapshot = acc.snapshot()
        assert snapshot["files"][0]["opaque"] is True
        assert snapshot["files"][0]["additions"] == 0
        assert "Binary or oversize files" in snapshot["diff"]

    def test_an_identically_rewritten_binary_is_not_a_change(self):
        """Without a digest every rewrite of an opaque file was reported as
        a modification the run may not have made."""
        acc = RunDiffAccumulator()
        acc.note_before("img.png", None, exists=True, opaque=True, digest="abc")
        acc.note_after("img.png", None, exists=True, opaque=True, digest="abc")
        assert acc.snapshot()["files"] == []

    def test_a_genuinely_rewritten_binary_is_a_change(self):
        acc = RunDiffAccumulator()
        acc.note_before("img.png", None, exists=True, opaque=True, digest="abc")
        acc.note_after("img.png", None, exists=True, opaque=True, digest="def")
        entry = acc.snapshot()["files"][0]
        assert entry["change_type"] == "modified"
        assert entry["opaque"] is True

    def test_an_opaque_side_without_a_digest_is_reported_as_changed(self):
        """No evidence of equality is not evidence of equality."""
        acc = RunDiffAccumulator()
        acc.note_before("img.png", None, exists=True, opaque=True, digest=None)
        acc.note_after("img.png", None, exists=True, opaque=True, digest="def")
        assert acc.snapshot()["files"][0]["change_type"] == "modified"

    def test_the_file_list_is_capped_but_the_stats_are_not(self):
        """``MAX_DIFF_CHARS`` bounded the rendered text; nothing bounded
        ``files``, so a 2,000-file run pushed a ~400 KB notification after
        every single mutation."""
        acc = RunDiffAccumulator()
        for index in range(MAX_DIFF_FILES + 25):
            path = f"f{index:05d}.txt"
            acc.note_before(path, None, exists=False)
            acc.note_after(path, "x\n", exists=True)
        snapshot = acc.snapshot()
        assert len(snapshot["files"]) == MAX_DIFF_FILES
        assert snapshot["files_truncated"] is True
        assert snapshot["stats"]["files_changed"] == MAX_DIFF_FILES + 25
        assert snapshot["stats"]["additions"] == MAX_DIFF_FILES + 25

    def test_an_uncapped_file_list_says_so(self):
        acc = RunDiffAccumulator()
        acc.note_before("a.txt", None, exists=False)
        acc.note_after("a.txt", "x\n", exists=True)
        assert acc.snapshot()["files_truncated"] is False

    def test_a_paths_diff_is_computed_once_until_it_is_touched_again(self):
        """``changes()`` runs after EVERY observed mutation. Rebuilding all
        of them each time re-diffed every path so far, inside the write
        path — quadratic in the number of edits."""
        acc = RunDiffAccumulator()
        acc.note_before("a.txt", "one\n", exists=True)
        acc.note_after("a.txt", "two\n", exists=True)
        first = acc.changes()[0]
        assert acc.changes()[0] is first  # reused, not rebuilt

        acc.note_after("a.txt", "three\n", exists=True)
        rebuilt = acc.changes()[0]
        assert rebuilt is not first
        assert "+three" in "".join(rebuilt.hunks())

    def test_stats_are_computed_once_per_path(self):
        """Memoizing ``hunks`` alone was not enough: ``stats`` re-scanned
        every cached diff line on every snapshot, i.e. after every mutation
        for every file the run had touched — 95% of the aggregation cost."""
        acc = RunDiffAccumulator()
        acc.note_before("a.txt", "one\n", exists=True)
        acc.note_after("a.txt", "two\n", exists=True)
        change = acc.changes()[0]
        first = change.stats()
        assert change.stats() is first  # reused, not recomputed

        acc.note_after("a.txt", "three\n", exists=True)
        rebuilt = acc.changes()[0]
        assert rebuilt.stats() is not first
        assert rebuilt.stats() == {"additions": 1, "deletions": 1}

    def test_an_untouched_path_is_not_re_diffed_when_another_changes(self):
        acc = RunDiffAccumulator()
        for path in ("a.txt", "b.txt"):
            acc.note_before(path, None, exists=False)
            acc.note_after(path, "x\n", exists=True)
        stable = {c.path: c for c in acc.changes()}

        acc.note_after("b.txt", "y\n", exists=True)
        after = {c.path: c for c in acc.changes()}
        assert after["a.txt"] is stable["a.txt"]
        assert after["b.txt"] is not stable["b.txt"]

    def test_a_mutation_whose_post_image_never_arrived_is_not_a_deletion(self):
        """A failed write must not be reported as if it removed the file."""
        acc = RunDiffAccumulator()
        acc.note_before("a.txt", "content\n", exists=True)
        assert acc.snapshot()["files"] == []

    def test_an_unobserved_baseline_never_makes_a_change_a_no_op(self):
        """N10. When the baseline was never observed AND the path is absent
        afterwards, both "sides" are non-existent — so the ordinary
        create-then-delete rule would cancel the entry and the deletion
        would vanish. A mutation WAS observed; the only honest answer is to
        report it and say the baseline is unknown."""
        acc = RunDiffAccumulator()
        acc.note_after("gone.txt", None, exists=False)
        entry = acc.snapshot()["files"][0]
        assert entry == {
            "path": "gone.txt",
            "change_type": "deleted",
            "additions": 0,
            "deletions": 0,
            "opaque": False,
            "baseline_unknown": True,
        }

    def test_an_unobserved_baseline_is_reported_even_when_both_sides_match(self):
        """The same guard, from the other direction: identical unknown
        sides must not be collapsed into "nothing happened"."""
        acc = RunDiffAccumulator()
        acc.note_after("x.txt", None, exists=True, opaque=True, digest="abc")
        assert acc.snapshot()["stats"]["files_changed"] == 1

    def test_a_post_image_without_a_pre_image_is_reported_without_guessing(self):
        """The change is kept, but neither creation nor deletion is claimed:
        nobody observed what the path looked like before."""
        acc = RunDiffAccumulator()
        acc.note_after("late.txt", "value\n", exists=True)
        entry = acc.snapshot()["files"][0]
        assert entry["change_type"] == "modified"
        assert entry["baseline_unknown"] is True
        assert entry["additions"] == 0 and entry["deletions"] == 0

    def test_oversize_diff_is_truncated_on_a_file_boundary(self):
        """A diff cut mid-hunk is not a unified diff — the surviving ``@@``
        line counts stop matching the body. Whole blocks are dropped."""
        acc = RunDiffAccumulator()
        # Sized so ONE rendered block fits under the cap and two do not
        # (a unified-diff add line is ``+x\n``, three chars per source line).
        big = "x\n" * (MAX_DIFF_CHARS // 4)
        for path in ("also.txt", "big.txt"):
            acc.note_before(path, None, exists=False)
            acc.note_after(path, big, exists=True)
        snapshot = acc.snapshot()
        assert snapshot["truncated"] is True
        assert len(snapshot["diff"]) <= MAX_DIFF_CHARS
        # Exactly one whole block survived, and it is intact.
        assert snapshot["diff"].count("diff --git") == 1
        assert snapshot["diff"].startswith("diff --git a/also.txt b/also.txt\n")
        assert snapshot["diff"].endswith("\n")
        assert "big.txt" not in snapshot["diff"]
        # ``files`` and ``stats`` stay COMPLETE so the client still knows
        # every path the run touched.
        assert [entry["path"] for entry in snapshot["files"]] == [
            "also.txt",
            "big.txt",
        ]
        assert snapshot["stats"]["files_changed"] == 2

    def test_a_single_oversize_file_renders_no_partial_block(self):
        acc = RunDiffAccumulator()
        acc.note_before("huge.txt", None, exists=False)
        acc.note_after("huge.txt", "x\n" * MAX_DIFF_CHARS, exists=True)
        snapshot = acc.snapshot()
        assert snapshot["truncated"] is True
        assert snapshot["diff"] == ""
        assert snapshot["files"][0]["path"] == "huge.txt"


# ---------------------------------------------------------------------
# Observation through the real workspace API
# ---------------------------------------------------------------------


class TestObservation:
    @pytest.mark.asyncio
    async def test_write_text_is_observed(self, observed):
        observer, published = observed
        await ws.write_text("a.txt", "hello\n")
        assert published
        assert published[-1]["files"][0]["path"] == "a.txt"
        assert published[-1]["files"][0]["change_type"] == "added"

    @pytest.mark.asyncio
    async def test_two_writes_to_one_file_aggregate_to_the_net_change(self, observed):
        observer, published = observed
        await ws.write_text("a.txt", "v1\n")
        await ws.write_text("a.txt", "v2\n")
        await ws.write_text("a.txt", "v3\n")
        snapshot = published[-1]
        assert snapshot["stats"]["files_changed"] == 1
        assert "+v3" in snapshot["diff"]
        assert "+v1" not in snapshot["diff"]
        assert "+v2" not in snapshot["diff"]

    @pytest.mark.asyncio
    async def test_pre_existing_content_is_the_baseline(self, workspace, observed):
        (workspace / "a.txt").write_text("original\n")
        observer, published = observed
        await ws.write_text("a.txt", "edited\n")
        snapshot = published[-1]
        assert snapshot["files"][0]["change_type"] == "modified"
        assert "-original" in snapshot["diff"]
        assert "+edited" in snapshot["diff"]

    @pytest.mark.asyncio
    async def test_create_then_remove_publishes_an_empty_diff(self, observed):
        observer, published = observed
        await ws.write_text("tmp.txt", "scratch\n")
        await ws.remove("tmp.txt")
        assert published[-1]["files"] == []
        assert published[-1]["stats"]["files_changed"] == 0

    @pytest.mark.asyncio
    async def test_a_run_that_changes_nothing_publishes_nothing(
        self, workspace, observed
    ):
        (workspace / "a.txt").write_text("same\n")
        observer, published = observed
        await ws.write_text("a.txt", "same\n")
        assert published == []

    @pytest.mark.asyncio
    async def test_removing_a_pre_existing_file_is_a_deletion(
        self, workspace, observed
    ):
        (workspace / "a.txt").write_text("bye\n")
        observer, published = observed
        await ws.remove("a.txt")
        assert published[-1]["files"][0]["change_type"] == "deleted"

    @pytest.mark.asyncio
    async def test_recursive_removal_reports_every_file_it_deleted(
        self, workspace, observed
    ):
        """A tree removal is N deletions, not "nothing happened"."""
        (workspace / "sub").mkdir()
        (workspace / "sub" / "a.txt").write_text("a\n")
        (workspace / "sub" / "b.txt").write_text("b\n")
        (workspace / "sub" / "nested").mkdir()
        (workspace / "sub" / "nested" / "c.txt").write_text("c\n")
        observer, published = observed

        await ws.remove("sub", recursive=True)

        entries = {f["path"]: f for f in published[-1]["files"]}
        assert set(entries) == {"sub/a.txt", "sub/b.txt", "sub/nested/c.txt"}
        assert {f["change_type"] for f in entries.values()} == {"deleted"}

    @pytest.mark.asyncio
    async def test_a_file_created_then_removed_with_its_tree_cancels(
        self, workspace, observed
    ):
        """The inversion this fixes: files the run created and then deleted
        with the tree used to survive in the aggregate as ``added``,
        asserting paths that no longer exist."""
        observer, published = observed
        await ws.write_text("pkg/a.py", "a\n")
        await ws.write_text("pkg/b.py", "b\n")
        assert {f["path"] for f in published[-1]["files"]} == {"pkg/a.py", "pkg/b.py"}

        await ws.remove("pkg", recursive=True)
        assert published[-1]["files"] == []

    @pytest.mark.asyncio
    async def test_an_edited_file_removed_with_its_tree_is_a_deletion(
        self, workspace, observed
    ):
        (workspace / "pkg").mkdir()
        (workspace / "pkg" / "keep.py").write_text("original\n")
        observer, published = observed

        await ws.write_text("pkg/keep.py", "edited\n")
        assert published[-1]["files"][0]["change_type"] == "modified"

        await ws.remove("pkg", recursive=True)
        entry = published[-1]["files"][0]
        assert entry["path"] == "pkg/keep.py"
        assert entry["change_type"] == "deleted"

    @pytest.mark.asyncio
    async def test_an_unenumerable_directory_is_left_untracked(
        self, workspace, observed, monkeypatch
    ):
        (workspace / "sub").mkdir()
        (workspace / "sub" / "x.txt").write_text("x\n")
        observer, published = observed

        async def broken_glob(*args, **kwargs):
            raise OSError("cannot walk")

        monkeypatch.setattr(ws, "glob", broken_glob)
        await ws.remove("sub", recursive=True)
        assert published == []

    @pytest.mark.asyncio
    async def test_binary_content_is_reported_as_opaque(self, observed):
        observer, published = observed
        await ws.write_bytes("blob.bin", b"\x00\x01\x02\xff")
        entry = published[-1]["files"][0]
        assert entry["path"] == "blob.bin"
        assert entry["opaque"] is True

    @pytest.mark.asyncio
    async def test_oversize_file_is_reported_without_content(self, observed):
        observer, published = observed
        await ws.write_bytes("huge.bin", b"a" * (MAX_SNAPSHOT_BYTES + 1))
        entry = published[-1]["files"][0]
        assert entry["opaque"] is True
        assert entry["additions"] == 0

    @pytest.mark.asyncio
    async def test_an_observer_fault_never_fails_the_write(self, workspace):
        class Exploding:
            async def before_mutation(self, path):
                raise RuntimeError("boom")

            async def after_mutation(self, path):
                raise RuntimeError("boom")

        token = set_mutation_observer(Exploding())
        try:
            await ws.write_text("a.txt", "still written\n")
        finally:
            reset_mutation_observer(token)
        assert (workspace / "a.txt").read_text() == "still written\n"

    @pytest.mark.asyncio
    async def test_no_observer_bound_is_a_no_op(self, workspace):
        await ws.write_text("a.txt", "fine\n")
        assert (workspace / "a.txt").read_text() == "fine\n"

    @pytest.mark.asyncio
    async def test_a_failed_post_read_never_fabricates_a_deletion(
        self, workspace, observed, monkeypatch
    ):
        """A transient FS/sandbox fault on the read-back must not report the
        file as deleted. "The read failed" and "the file is gone" are
        different facts, and the run diff is the authoritative record."""
        (workspace / "a.txt").write_text("original\n")
        observer, published = observed

        real_exists = ws.exists
        calls = {"n": 0}

        async def flaky_exists(path):
            calls["n"] += 1
            if calls["n"] > 1:  # the post-write read
                raise OSError("transient sandbox hiccup")
            return await real_exists(path)

        monkeypatch.setattr(ws, "exists", flaky_exists)
        await ws.write_text("a.txt", "updated\n")

        assert published == []
        assert observer.accumulator.snapshot()["files"] == []
        # The write itself still landed.
        assert (workspace / "a.txt").read_text() == "updated\n"

    @pytest.mark.asyncio
    async def test_a_failed_pre_read_does_not_fabricate_a_creation(
        self, workspace, observed, monkeypatch
    ):
        """The mirror case: an unreadable baseline must not turn an edit to
        an existing file into an "added" entry."""
        (workspace / "a.txt").write_text("original\n")
        observer, published = observed

        real_read = ws.read_bytes
        calls = {"n": 0}

        async def flaky_read(path):
            calls["n"] += 1
            if calls["n"] == 1:  # the pre-write read
                raise OSError("transient sandbox hiccup")
            return await real_read(path)

        monkeypatch.setattr(ws, "read_bytes", flaky_read)
        await ws.write_text("a.txt", "updated\n")

        entry = published[-1]["files"][0]
        assert entry["change_type"] == "modified"
        assert entry["baseline_unknown"] is True

    @pytest.mark.asyncio
    async def test_a_genuine_absence_is_still_an_addition(self, observed):
        """The guard must not swallow the ordinary case: a path that is
        genuinely not there reads as absent, not as a failure."""
        observer, published = observed
        await ws.write_text("new.txt", "hello\n")
        entry = published[-1]["files"][0]
        assert entry["change_type"] == "added"
        assert entry["baseline_unknown"] is False

    @pytest.mark.asyncio
    async def test_a_failed_pre_read_then_a_removal_still_reports_the_deletion(
        self, workspace, observed, monkeypatch
    ):
        """N10 end to end: the pre-read faults, then the file is removed.
        Both observed "sides" are absent, so without the unknown-baseline
        guard the deletion is silently dropped from the run's record."""
        (workspace / "gone.txt").write_text("bye\n")
        observer, published = observed

        real_read = ws.read_bytes

        async def flaky_read(path):
            if path.endswith("gone.txt"):
                raise OSError("transient sandbox hiccup")
            return await real_read(path)

        monkeypatch.setattr(ws, "read_bytes", flaky_read)
        await ws.remove("gone.txt")

        assert published, "the deletion was dropped"
        entry = published[-1]["files"][0]
        assert entry["path"] == "gone.txt"
        assert entry["change_type"] == "deleted"
        assert entry["baseline_unknown"] is True
        assert not (workspace / "gone.txt").exists()

    @pytest.mark.asyncio
    async def test_a_path_replaced_by_a_directory_is_not_reported_as_emptied(
        self, workspace, observed
    ):
        """N25. ``rm foo && mkdir foo`` through a shell leaves a DIRECTORY
        where the run wrote a file. Re-reading it at finalize time yields
        "exists, no content", which is indistinguishable from an emptied
        file — a content claim the run never made."""
        observer, published = observed
        await ws.write_text("mod.py", "def f():\n    pass\n")
        assert "+def f():" in published[-1]["diff"]

        (workspace / "mod.py").unlink()
        (workspace / "mod.py").mkdir()

        await observer.finalize()
        diff = published[-1]["diff"]
        # A directory has no content. Recording that as the post-image
        # replaces the run's real change with an empty one.
        assert "+def f():" in diff
        entry = published[-1]["files"][0]
        assert entry["change_type"] == "added"
        # The discriminating scalar, asserted alongside the content so the
        # test cannot go vacuous if the content check is ever loosened:
        # unguarded, the post-image is empty and this is 0.
        assert entry["additions"] == 2

    @pytest.mark.asyncio
    async def test_an_edited_path_replaced_by_a_directory_is_not_emptied(
        self, workspace, observed
    ):
        """N25, the damaging direction: the file EXISTED, so a contentless
        post-image reads as "the run deleted every line"."""
        (workspace / "mod.py").write_text("original\n")
        observer, published = observed
        await ws.write_text("mod.py", "edited\n")

        (workspace / "mod.py").unlink()
        (workspace / "mod.py").mkdir()

        await observer.finalize()
        diff = published[-1]["diff"]
        assert "+edited" in diff
        entry = published[-1]["files"][0]
        # ``deletions == 1`` holds in BOTH directions — guarded gives
        # (1 deletion, 1 addition), unguarded gives (1 deletion, 0
        # additions) because the post-image became empty. ``additions`` is
        # the scalar that actually discriminates; asserting it beside the
        # content keeps the test non-vacuous by construction.
        assert entry["deletions"] == 1
        assert entry["additions"] == 1

    @pytest.mark.asyncio
    async def test_finalize_warns_when_it_reconciles_nothing(
        self, workspace, observed, monkeypatch, caplog
    ):
        """A closed sandbox session would make every re-read fail, so the
        pass silently does nothing and the settled diff keeps its stale
        content. The whole point of the pass is that it does not."""
        observer, published = observed
        await ws.write_text("a.txt", "v1\n")

        async def dead_workspace(path):
            raise OSError("workspace session closed")

        monkeypatch.setattr(ws, "exists", dead_workspace)
        with caplog.at_level("WARNING", logger="omniagents.core.conversation.run_diff"):
            await observer.finalize()

        assert any(
            "reconciled none of 1 tracked paths" in record.getMessage()
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    @pytest.mark.asyncio
    async def test_finalize_does_not_warn_when_it_reconciles(
        self, workspace, observed, caplog
    ):
        observer, published = observed
        await ws.write_text("a.txt", "v1\n")
        with caplog.at_level("WARNING", logger="omniagents.core.conversation.run_diff"):
            await observer.finalize()
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("spelling", ["absolute", "relative", "symlink"])
    async def test_host_edits_outside_the_root_are_captured(
        self, tmp_path, workspace, observed, spelling
    ):
        """The host root anchors paths; it does not restrict agent file I/O."""
        observer, published = observed
        outside = tmp_path / "outside.txt"
        outside.write_text("before\n")
        (workspace / "link.txt").symlink_to(outside)
        path = {
            "absolute": str(outside),
            "relative": "../outside.txt",
            "symlink": "link.txt",
        }[spelling]

        await ws.write_text(path, "after\n")
        assert outside.read_text() == "after\n"
        assert published[-1]["files"][0]["path"] == "../outside.txt"
        assert "-before" in published[-1]["diff"]
        assert "+after" in published[-1]["diff"]

        # Final reconciliation must resolve the stored relative key to the
        # same external file, rather than losing the edit at run completion.
        outside.write_text("settled\n")
        await observer.finalize()
        assert "+settled" in published[-1]["diff"]
        assert "+after" not in published[-1]["diff"]
        assert published[-1]["stats"]["files_changed"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("spelling", ["absolute", "relative", "symlink"])
    async def test_a_path_outside_a_confined_workspace_is_never_captured(
        self, tmp_path, workspace, observed, spelling, monkeypatch
    ):
        """The observer reads content back through the workspace, so it
        inherits the same boundary the tools do — an escaping path is
        refused before any content is read, not sanitised afterwards."""
        observer, published = observed
        outside = tmp_path / "outside.txt"
        outside.write_text("secret\n")
        (workspace / "link.txt").symlink_to(outside)

        # Host workspaces (including mounted ones) are unrestricted. Model
        # a confined backend explicitly to exercise the observer's boundary
        # handling without depending on a sandbox service.
        class ConfinedTestWorkspace(HostWorkspace):
            @property
            def enforces_confinement(self):
                return True

            def resolve(self, path):
                absolute = super().resolve(path)
                if not self.contains_path(absolute):
                    raise WorkspaceEscapeError(f"Outside test workspace: {path}")
                return absolute

        confined = ConfinedTestWorkspace(str(workspace))
        path = {
            "absolute": str(outside),
            "relative": "../outside.txt",
            "symlink": "link.txt",
        }[spelling]
        from unittest.mock import AsyncMock

        read = AsyncMock(wraps=confined.read_bytes)
        monkeypatch.setattr(confined, "read_bytes", read)
        token = set_workspace(confined)
        try:
            with pytest.raises(WorkspaceEscapeError):
                await ws.write_text(path, "clobbered\n")
            await observer.finalize()
        finally:
            reset_workspace(token)
        read.assert_not_awaited()
        assert published == []
        assert observer.accumulator.entries == {}
        assert outside.read_text() == "secret\n"

    @pytest.mark.asyncio
    async def test_concurrent_runs_do_not_share_an_accumulator(self, workspace):
        """The per-run contextvar binding is what keeps two runs apart.

        Both runs install their observer BEFORE either writes, so a
        process-global binding would hand every mutation to whichever
        observer was installed last and starve the other — the exact failure
        mode the singleton plan bridge had.
        """
        import asyncio

        results: dict[str, list] = {}
        installed = asyncio.Event()
        pending = {"count": 2}

        async def run(name, path):
            published: list = []

            async def publish(snapshot):
                published.append(snapshot)

            set_mutation_observer(RunDiffObserver(publish))
            pending["count"] -= 1
            if pending["count"] == 0:
                installed.set()
            await installed.wait()
            await ws.write_text(path, f"{name}\n")
            results[name] = published

        await asyncio.gather(
            asyncio.create_task(run("a", "a.txt")),
            asyncio.create_task(run("b", "b.txt")),
        )
        assert [f["path"] for f in results["a"][-1]["files"]] == ["a.txt"]
        assert [f["path"] for f in results["b"][-1]["files"]] == ["b.txt"]
        assert len(results["a"]) == 1
        assert len(results["b"]) == 1


# ---------------------------------------------------------------------
# Persistence + lifecycle
# ---------------------------------------------------------------------


class TestPersistence:
    def test_run_diff_is_recorded_as_a_turn_scoped_item(self, store, recorder):
        recorder.record(THREAD, "run_started", {"run_id": RUN, "prompt": "go"})
        recorder.record_run_diff(
            THREAD, run_id=RUN, snapshot={"diff": "d", "files": [], "stats": {}}
        )
        rows = store.list_items(THREAD, limit=10, kinds=[ItemKind.RUN_DIFF])
        assert len(rows) == 1
        assert rows[0].turn_id == RUN
        assert rows[0].long_lived is False
        assert rows[0].content["run_id"] == RUN

    def test_further_edits_revise_the_same_item(self, store, recorder):
        recorder.record(THREAD, "run_started", {"run_id": RUN, "prompt": "go"})
        first = recorder.record_run_diff(
            THREAD, run_id=RUN, snapshot={"diff": "one", "files": []}
        )
        second = recorder.record_run_diff(
            THREAD, run_id=RUN, snapshot={"diff": "two", "files": []}
        )
        assert first.item_id == second.item_id
        assert second.revision > first.revision
        assert second.content["diff"] == "two"

    def test_run_end_settles_a_successful_runs_diff_as_completed(self, store, recorder):
        """Without the explicit settlement the generic orphan sweep would
        stamp a successful run's diff ``failed``."""
        recorder.record(THREAD, "run_started", {"run_id": RUN, "prompt": "go"})
        recorder.record_run_diff(
            THREAD, run_id=RUN, snapshot={"diff": "d", "files": []}
        )
        recorder.record(THREAD, "run_end", {"run_id": RUN, "end_reason": "completed"})

        item = store.get_item(
            THREAD,
            derive_item_id(THREAD, ConversationRecorder.run_diff_natural_key(RUN)),
        )
        assert item.status is ItemStatus.COMPLETED

    @pytest.mark.parametrize(
        "end_reason,expected",
        [
            ("completed", ItemStatus.COMPLETED),
            (None, ItemStatus.COMPLETED),
            ("cancelled", ItemStatus.CANCELLED),
            ("canceled", ItemStatus.CANCELLED),
            ("stopped", ItemStatus.CANCELLED),
            ("error", ItemStatus.FAILED),
            ("timeout", ItemStatus.FAILED),
        ],
    )
    def test_the_settled_diff_mirrors_how_the_run_ended(
        self, store, recorder, end_reason, expected
    ):
        """Hard-coding ``completed`` made the diff the one item of a
        cancelled turn claiming it finished, while every sibling item of the
        same turn was ``cancelled``."""
        recorder.record(THREAD, "run_started", {"run_id": RUN, "prompt": "go"})
        recorder.record_run_diff(
            THREAD, run_id=RUN, snapshot={"diff": "d", "files": []}
        )
        recorder.record(THREAD, "run_end", {"run_id": RUN, "end_reason": end_reason})

        item = store.get_item(
            THREAD,
            derive_item_id(THREAD, ConversationRecorder.run_diff_natural_key(RUN)),
        )
        assert item.status is expected
        # The record of what the run changed survives however it ended.
        assert item.content["diff"] == "d"

    def test_a_late_edit_still_revises_a_settled_diff(self, store, recorder):
        recorder.record(THREAD, "run_started", {"run_id": RUN, "prompt": "go"})
        recorder.record_run_diff(
            THREAD, run_id=RUN, snapshot={"diff": "d", "files": []}
        )
        recorder.record(THREAD, "run_end", {"run_id": RUN, "end_reason": "completed"})
        revised = recorder.record_run_diff(
            THREAD, run_id=RUN, snapshot={"diff": "late", "files": []}
        )
        assert revised.content["diff"] == "late"
        # The terminal STATUS is authoritative and must not be re-opened.
        assert revised.status is ItemStatus.COMPLETED

    def test_run_end_pushes_the_settled_diff(self, store):
        """The terminal landing has no notification of its own, so without a
        push a live client's run diff sits at ``started`` forever and only
        converges if it happens to refetch on run_end."""
        settled = []
        rec = ConversationRecorder(store, on_settled=settled.append)
        rec.ensure_thread(THREAD)
        rec.record(THREAD, "run_started", {"run_id": RUN, "prompt": "go"})
        rec.record_run_diff(THREAD, run_id=RUN, snapshot={"diff": "d", "files": []})
        assert settled == []
        rec.record(THREAD, "run_end", {"run_id": RUN, "end_reason": "completed"})

        assert [item.kind for item in settled] == [ItemKind.RUN_DIFF]
        assert settled[0].status is ItemStatus.COMPLETED

    def test_a_listener_fault_never_breaks_the_settlement(self, store):
        def explode(item):
            raise RuntimeError("listener down")

        rec = ConversationRecorder(store, on_settled=explode)
        rec.ensure_thread(THREAD)
        rec.record(THREAD, "run_started", {"run_id": RUN, "prompt": "go"})
        rec.record_run_diff(THREAD, run_id=RUN, snapshot={"diff": "d", "files": []})
        rec.record(THREAD, "run_end", {"run_id": RUN, "end_reason": "completed"})

        item = store.get_item(
            THREAD,
            derive_item_id(THREAD, ConversationRecorder.run_diff_natural_key(RUN)),
        )
        assert item.status is ItemStatus.COMPLETED

    def test_two_runs_get_separate_diffs(self, store, recorder):
        for run in ("run-a", "run-b"):
            recorder.record(THREAD, "run_started", {"run_id": run, "prompt": "go"})
            recorder.record_run_diff(
                THREAD, run_id=run, snapshot={"diff": run, "files": []}
            )
            recorder.record(
                THREAD, "run_end", {"run_id": run, "end_reason": "completed"}
            )
        rows = store.list_items(THREAD, limit=10, kinds=[ItemKind.RUN_DIFF])
        assert {row.turn_id for row in rows} == {"run-a", "run-b"}
        assert {row.content["diff"] for row in rows} == {"run-a", "run-b"}
