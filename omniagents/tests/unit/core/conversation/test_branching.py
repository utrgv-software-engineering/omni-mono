"""Fork-from-turn, the descendant policy, and retention (tkt_eZldW-Gje6kn).

These drive the real production call sites — ``AgentService.fork_session``,
``delete_session``, ``archive_session``, ``purge_threads`` — not the
recorder in isolation, because the wiring is where the last round of forks
went wrong: ``record_fork`` was correct while the fork RPC never reached it.
"""

from __future__ import annotations

import asyncio

import pytest

from omniagents.core.conversation.model import ItemKind, ThreadStatus
from omniagents.core.conversation.service import (
    ConversationService,
    ForkPointInvalid,
    ItemNotFound,
    ThreadHasDescendants,
    ThreadMetadataInvalid,
    ThreadOnLegalHold,
)
from omniagents.core.conversation.store import (
    get_conversation_store,
    reset_conversation_store,
)


@pytest.fixture
def store():
    reset_conversation_store()
    backend = get_conversation_store(None, None)
    yield backend
    reset_conversation_store()


@pytest.fixture
def service():
    from omniagents.core.agents.service import AgentService
    from omniagents.core.agents.specs import AgentSpec

    async def build_agent(settings, mcp, app):  # pragma: no cover - never run
        raise AssertionError("no agent build in these tests")

    spec = AgentSpec(
        name="BranchAgent",
        welcome_text="hi",
        get_agent_instructions=lambda: "hi",
        build_agent=build_agent,
    )
    return AgentService(spec)


def run_turn(service, session, run_id, prompt, *, tool_output="ok"):
    """Drive a whole turn through the notification chokepoint.

    Uses the real recorder entry point, so the domain rows under test are
    the ones production writes — including the raw ``history`` rows, which
    is what a fork has to truncate alongside the transcript.
    """
    from omniagents.core.session.event_log import stamp_session_event

    session.active_run_id = run_id
    session.append_message({"role": "user", "content": prompt})
    for method, payload in [
        ("run_started", {"run_id": run_id, "prompt": prompt, "prompt_role": "user"}),
        (
            "tool_called",
            {
                "run_id": run_id,
                "tool": "read_file",
                "input": "{}",
                "call_id": f"{run_id}-c1",
            },
        ),
        (
            "tool_result",
            {
                "run_id": run_id,
                "tool": "read_file",
                "output": tool_output,
                "call_id": f"{run_id}-c1",
            },
        ),
        ("message_output", {"run_id": run_id, "content": f"reply to {prompt}"}),
        ("run_end", {"run_id": run_id, "end_reason": "completed"}),
    ]:
        stamp_session_event(session, method, {"session_id": session.id, **payload})
    session.append_message({"role": "assistant", "content": f"reply to {prompt}"})
    session.active_run_id = None


def seed_three_turns(service, session_id="sess-fork"):
    session = service._session_manager.get_or_create(session_id)
    for index in (1, 2, 3):
        run_turn(service, session, f"run{index}", f"question {index}")
    return session


def mid_run_user_message(service, session, run_id, text):
    """A user message appended INSIDE a running turn.

    ``send_user_message`` does this in production, and it is the case that
    makes turn counts and raw user rows diverge: after one of these the
    thread holds more ``role: "user"`` history rows than it holds turns.
    """
    from omniagents.core.session.event_log import stamp_session_event

    session.active_run_id = run_id
    stamp_session_event(
        session,
        "tool_called",
        {
            "session_id": session.id,
            "run_id": run_id,
            "tool": "read_file",
            "input": "{}",
            "call_id": f"{run_id}-pre",
        },
    )
    stamp_session_event(
        session,
        "tool_result",
        {
            "session_id": session.id,
            "run_id": run_id,
            "tool": "read_file",
            "output": "ok",
            "call_id": f"{run_id}-pre",
        },
    )
    session.append_message({"role": "user", "content": text})
    service._record_conversation(
        session,
        lambda rec: rec.record_user_message(session.id, run_id=run_id, content=text),
    )


def run_turn_with_mid_run_message(service, session, run_id, prompt, injected):
    """One turn holding TWO user rows: the prompt and a mid-run message."""
    from omniagents.core.session.event_log import stamp_session_event

    session.active_run_id = run_id
    session.append_message({"role": "user", "content": prompt})
    stamp_session_event(
        session,
        "run_started",
        {
            "session_id": session.id,
            "run_id": run_id,
            "prompt": prompt,
            "prompt_role": "user",
        },
    )
    mid_run_user_message(service, session, run_id, injected)
    stamp_session_event(
        session,
        "message_output",
        {"session_id": session.id, "run_id": run_id, "content": f"reply to {injected}"},
    )
    stamp_session_event(
        session,
        "run_end",
        {"session_id": session.id, "run_id": run_id, "end_reason": "completed"},
    )
    session.append_message({"role": "assistant", "content": f"reply to {injected}"})
    session.active_run_id = None


# ---------------------------------------------------------------------
# Fork from a completed turn
# ---------------------------------------------------------------------


class TestForkFromTurn:
    def test_child_holds_exactly_the_parent_prefix(self, store, service):
        parent = seed_three_turns(service)
        parent_items = store.list_items(parent.id, limit=100)
        turn2_last = max(i.seq for i in parent_items if i.turn_id == "run2")

        result = asyncio.run(
            service.fork_session(
                parent.id, new_session_id="sess-child", from_turn_id="run2"
            )
        )

        child_items = store.list_items("sess-child", limit=100)
        assert [i.seq for i in child_items] == list(range(1, turn2_last + 1))
        # Same CONTENT, in order, for the copied prefix — not merely the
        # same count.
        assert [i.content for i in child_items] == [
            i.content for i in parent_items[:turn2_last]
        ]
        # …and nothing from turn 3.
        assert all(item.turn_id in ("run1", "run2") for item in child_items)
        assert {i.turn_id for i in parent_items} == {"run1", "run2", "run3"}
        assert (
            result["branched_from_item_id"]
            == child_items[-1].source_ref["forked_from_item_id"]
        )

    def test_ancestry_points_at_the_branch_point(self, store, service):
        parent = seed_three_turns(service)
        parent_items = store.list_items(parent.id, limit=100)
        branch = max(
            (i for i in parent_items if i.turn_id == "run2"), key=lambda i: i.seq
        )

        asyncio.run(
            service.fork_session(
                parent.id, new_session_id="sess-child", from_item_id=branch.item_id
            )
        )

        child = store.get_thread("sess-child")
        assert child.parent_thread_id == parent.id
        # ``branched_from_item_id`` names the PARENT's item (protocol.md
        # § Conversation Domain Objects), so a client can jump to the
        # branch point in the conversation it branched from.
        assert child.branched_from_item_id == branch.item_id
        last = store.list_items("sess-child", limit=100)[-1]
        assert last.item_id != branch.item_id, "child ids must be re-derived"
        assert last.source_ref["forked_from_item_id"] == branch.item_id
        assert last.source_ref["forked_from_thread_id"] == parent.id

    def test_the_child_s_turns_are_clipped_not_inherited(self, store, service):
        """Branch at a MID-RUN user message, so turn 2 is copied partially.

        That is an exactly-cuttable position (one user item, one user row)
        and the only kind of mid-turn branch the surface accepts."""
        session = service._session_manager.get_or_create("sess-clip")
        run_turn(service, session, "run1", "question 1")
        run_turn_with_mid_run_message(
            service, session, "run2", "question 2", "actually do this instead"
        )
        run_turn(service, session, "run3", "question 3")

        parent_items = store.list_items(session.id, limit=100)
        branch = next(
            item
            for item in parent_items
            if item.turn_id == "run2"
            and item.kind is ItemKind.USER_MESSAGE
            and item.content.get("text") == "actually do this instead"
        )

        asyncio.run(
            service.fork_session(
                session.id, new_session_id="sess-child", from_item_id=branch.item_id
            )
        )

        turns = {t.turn_id: t for t in store.list_turns("sess-child", limit=10)}
        assert set(turns) == {"run1", "run2"}, "turn 3 was copied with no items"
        assert turns["run2"].last_seq == branch.seq
        # A turn advertising a last_seq past the end of the thread is a
        # keyset window a client pages into and finds empty.
        assert turns["run2"].last_seq <= store.get_thread("sess-child").last_seq
        child_turn2 = [
            i for i in store.list_items("sess-child", limit=100) if i.turn_id == "run2"
        ]
        assert turns["run2"].item_count == len(child_turn2)
        assert len(child_turn2) < len([i for i in parent_items if i.turn_id == "run2"])

    def test_the_next_append_cannot_collide_with_a_copied_row(self, store, service):
        """The #312 class of bug: an inherited cursor hands out an ordinal
        an existing row already holds and the unique index eats an item."""
        parent = seed_three_turns(service)
        parent_items = store.list_items(parent.id, limit=100)
        # The tail of turn 1 — an exactly-cuttable branch point.
        branch = max(
            (i for i in parent_items if i.turn_id == "run1"), key=lambda i: i.seq
        )

        asyncio.run(
            service.fork_session(
                parent.id, new_session_id="sess-child", from_item_id=branch.item_id
            )
        )
        child_before = store.list_items("sess-child", limit=100)
        assert store.get_thread("sess-child").last_seq == child_before[-1].seq

        next_seq = store.allocate_seq("sess-child")
        assert next_seq > child_before[-1].seq
        assert all(item.seq != next_seq for item in child_before)

        # And a real turn on the child keeps every copied row.
        child_session = service._session_manager.get_or_create("sess-child")
        run_turn(service, child_session, "run-child", "branch question")
        after = store.list_items("sess-child", limit=100)
        seqs = [item.seq for item in after]
        assert len(seqs) == len(set(seqs)), f"duplicate ordinals: {seqs}"
        assert len(after) > len(child_before)
        assert [i.item_id for i in child_before] == [
            i.item_id for i in after[: len(child_before)]
        ]

    def test_the_child_s_raw_history_stops_at_the_branch_too(self, store, service):
        """A child whose transcript ends at turn 2 while its model context
        still holds turn 3 is a mislabelled copy, not a branch."""
        parent = seed_three_turns(service)
        asyncio.run(
            service.fork_session(
                parent.id, new_session_id="sess-child", from_turn_id="run2"
            )
        )
        child_session = service._session_manager.get_or_create("sess-child")
        prompts = [
            row.get("content")
            for row in child_session.history
            if row.get("role") == "user"
        ]
        assert prompts == ["question 1", "question 2"]
        assert "question 3" not in [row.get("content") for row in child_session.history]

    def test_an_untruncated_fork_still_copies_everything(self, store, service):
        parent = seed_three_turns(service)
        asyncio.run(service.fork_session(parent.id, new_session_id="sess-whole"))
        assert len(store.list_items("sess-whole", limit=100)) == len(
            store.list_items(parent.id, limit=100)
        )
        assert store.get_thread("sess-whole").parent_thread_id == parent.id

    def test_forking_a_running_turn_is_refused(self, store, service):
        session = service._session_manager.get_or_create("sess-running")
        run_turn(service, session, "run1", "done question")
        # Open a second turn and leave it running.
        from omniagents.core.session.event_log import stamp_session_event

        session.active_run_id = "run2"
        stamp_session_event(
            session,
            "run_started",
            {"session_id": session.id, "run_id": "run2", "prompt": "in flight"},
        )

        with pytest.raises(ForkPointInvalid) as exc:
            asyncio.run(
                service.fork_session(
                    session.id, new_session_id="sess-x", from_turn_id="run2"
                )
            )
        assert exc.value.data["kind"] == "fork_point_invalid"
        assert exc.value.data["status"] == "running"
        assert store.get_thread("sess-x") is None

    def test_forking_from_an_ITEM_inside_a_running_turn_is_refused(
        self, store, service
    ):
        """The turn-id path and the item-id path each need the check: a
        client can name any item of a turn that is still landing rows."""
        from omniagents.core.session.event_log import stamp_session_event

        session = service._session_manager.get_or_create("sess-run-item")
        run_turn(service, session, "run1", "settled question")
        session.active_run_id = "run2"
        session.append_message({"role": "user", "content": "in flight"})
        stamp_session_event(
            session,
            "run_started",
            {
                "session_id": session.id,
                "run_id": "run2",
                "prompt": "in flight",
                "prompt_role": "user",
            },
        )
        live = [
            item
            for item in store.list_items(session.id, limit=100)
            if item.turn_id == "run2"
        ]
        assert live, "the running turn recorded nothing to branch at"

        with pytest.raises(ForkPointInvalid) as exc:
            asyncio.run(
                service.fork_session(
                    session.id,
                    new_session_id="sess-run-item-child",
                    from_item_id=live[-1].item_id,
                )
            )
        assert exc.value.data["status"] == "running"
        assert store.get_thread("sess-run-item-child") is None

    def test_an_unknown_branch_point_is_refused(self, store, service):
        parent = seed_three_turns(service)
        with pytest.raises(ItemNotFound):
            asyncio.run(
                service.fork_session(
                    parent.id, new_session_id="sess-x", from_item_id="itm_nope"
                )
            )
        with pytest.raises(ForkPointInvalid):
            asyncio.run(
                service.fork_session(
                    parent.id, new_session_id="sess-y", from_turn_id="run-nope"
                )
            )

    def test_both_fork_point_parameters_together_are_refused(self, store, service):
        parent = seed_three_turns(service)
        items = store.list_items(parent.id, limit=100)
        with pytest.raises(ForkPointInvalid):
            asyncio.run(
                service.fork_session(
                    parent.id,
                    new_session_id="sess-x",
                    from_item_id=items[0].item_id,
                    from_turn_id="run1",
                )
            )

    def test_a_compaction_floor_above_the_branch_is_dropped(self, store, service):
        parent = seed_three_turns(service)
        thread = store.get_thread(parent.id)
        last = store.list_items(parent.id, limit=100)[-1]
        thread.compaction = {
            "checkpoint_item_id": last.item_id,
            "checkpoint_seq": last.seq,
        }
        store.upsert_thread(thread)

        first_turn_last = max(
            i.seq for i in store.list_items(parent.id, limit=100) if i.turn_id == "run1"
        )
        asyncio.run(
            service.fork_session(
                parent.id, new_session_id="sess-child", from_turn_id="run1"
            )
        )
        assert store.get_thread("sess-child").compaction == {}
        assert store.get_thread("sess-child").last_seq == first_turn_last


# ---------------------------------------------------------------------
# Descendant policy
# ---------------------------------------------------------------------


class TestDescendantPolicy:
    def _parent_with_fork(self, service):
        parent = seed_three_turns(service)
        asyncio.run(service.fork_session(parent.id, new_session_id="sess-child"))
        return parent

    def test_deleting_a_parent_with_forks_is_refused(self, store, service):
        parent = self._parent_with_fork(service)
        with pytest.raises(ThreadHasDescendants) as exc:
            asyncio.run(service.delete_session(parent.id))
        assert exc.value.data["descendant_thread_ids"] == ["sess-child"]
        # Nothing was destroyed by the refusal.
        assert store.get_thread(parent.id) is not None
        assert store.get_thread("sess-child") is not None

    def test_cascade_deletes_the_whole_subtree(self, store, service):
        parent = self._parent_with_fork(service)
        asyncio.run(service.fork_session("sess-child", new_session_id="sess-grand"))
        assert asyncio.run(service.delete_session(parent.id, cascade=True)) is True
        for thread_id in (parent.id, "sess-child", "sess-grand"):
            assert store.get_thread(thread_id) is None
            assert store.list_items(thread_id, limit=10) == []

    def test_deleting_a_leaf_needs_no_cascade(self, store, service):
        parent = self._parent_with_fork(service)
        assert asyncio.run(service.delete_session("sess-child")) is True
        assert store.get_thread("sess-child") is None
        assert store.get_thread(parent.id) is not None

    def test_archiving_cascades_to_the_subtree(self, store, service):
        parent = self._parent_with_fork(service)
        asyncio.run(service.fork_session("sess-child", new_session_id="sess-grand"))

        assert asyncio.run(service.archive_session(parent.id)) is True

        for thread_id in (parent.id, "sess-child", "sess-grand"):
            assert store.get_thread(thread_id).status is ThreadStatus.ARCHIVED

    def test_archiving_a_leaf_leaves_its_ancestors_alone(self, store, service):
        parent = self._parent_with_fork(service)
        service._session_manager.get_or_create("sess-child")
        assert asyncio.run(service.archive_session("sess-child")) is True
        assert store.get_thread("sess-child").status is ThreadStatus.ARCHIVED
        assert store.get_thread(parent.id).status is ThreadStatus.ACTIVE

    def test_unarchiving_through_update_thread_cascades_back(self, store, service):
        parent = self._parent_with_fork(service)
        asyncio.run(service.archive_session(parent.id))
        assert store.get_thread("sess-child").status is ThreadStatus.ARCHIVED

        result = asyncio.run(service.update_thread(parent.id, status="active"))
        assert result["cascaded_thread_ids"] == ["sess-child"]
        assert store.get_thread("sess-child").status is ThreadStatus.ACTIVE

    def test_the_legacy_archive_flag_is_mirrored_for_descendants(self, store, service):
        from omniagents.core.session.history_db import get_archived

        parent = self._parent_with_fork(service)
        asyncio.run(service.archive_session(parent.id))
        assert get_archived(parent.id) is True
        assert get_archived("sess-child") is True


# ---------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------


class TestThreadMetadata:
    def test_rename_pin_and_metadata_merge(self, store, service):
        session = seed_three_turns(service, "sess-meta")

        renamed = asyncio.run(service.update_thread(session.id, title="Release prep"))
        assert renamed["changed"] == ["title"]
        assert renamed["thread"]["title"] == "Release prep"

        pinned = asyncio.run(service.update_thread(session.id, pinned=True))
        assert pinned["changed"] == ["pinned"]
        assert store.get_thread(session.id).pinned is True

        asyncio.run(service.update_thread(session.id, metadata={"a": 1, "b": 2}))
        merged = asyncio.run(
            service.update_thread(session.id, metadata={"b": None, "c": 3})
        )
        assert merged["thread"]["metadata"] == {"a": 1, "c": 3}

    def test_an_unchanged_write_reports_no_change(self, store, service):
        session = seed_three_turns(service, "sess-meta")
        asyncio.run(service.update_thread(session.id, pinned=True))
        again = asyncio.run(service.update_thread(session.id, pinned=True))
        assert again["changed"] == []

    def test_an_omitted_title_is_not_a_clear(self, store, service):
        session = seed_three_turns(service, "sess-meta")
        asyncio.run(service.update_thread(session.id, title="Kept"))
        asyncio.run(service.update_thread(session.id, pinned=True))
        assert store.get_thread(session.id).title == "Kept"
        cleared = asyncio.run(service.update_thread(session.id, title=None))
        assert cleared["changed"] == ["title"]
        assert store.get_thread(session.id).title is None


# ---------------------------------------------------------------------
# Export + retention
# ---------------------------------------------------------------------


class TestExportAndRetention:
    def test_export_pages_every_item_with_no_silent_cap(self, store, service):
        session = seed_three_turns(service, "sess-export")
        expected = [item.item_id for item in store.list_items(session.id, limit=500)]

        seen: list[str] = []
        cursor = None
        first_page = True
        while True:
            page = asyncio.run(
                service.export_thread(session.id, limit=2, cursor=cursor)
            )
            seen.extend(item["item_id"] for item in page["items"])
            if first_page:
                assert page["thread"]["thread_id"] == session.id
                assert len(page["turns"]) == 3
                first_page = False
            else:
                assert page["turns"] == []
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        assert seen == expected

    def test_export_includes_content_history_never_holds(self, store, service):
        """Approvals never reach the ``history`` table, so a history-based
        export would silently drop them."""
        from omniagents.core.session.event_log import stamp_session_event

        session = service._session_manager.get_or_create("sess-approval")
        run_turn(service, session, "run1", "do it")
        stamp_session_event(
            session,
            "tool_approval_requested",
            {
                "session_id": session.id,
                "run_id": "run1",
                "call_id": "c-appr",
                "tool_name": "execute_bash",
                "arguments": "{}",
            },
        )
        page = asyncio.run(service.export_thread(session.id, limit=500))
        kinds = [item["kind"] for item in page["items"]]
        assert ItemKind.APPROVAL.value in kinds

    def test_export_lists_descendants_on_request(self, store, service):
        session = seed_three_turns(service, "sess-export")
        asyncio.run(service.fork_session(session.id, new_session_id="sess-branch"))
        without = asyncio.run(service.export_thread(session.id))
        assert without["descendant_thread_ids"] == []
        with_kids = asyncio.run(
            service.export_thread(session.id, include_descendants=True)
        )
        assert with_kids["descendant_thread_ids"] == ["sess-branch"]

    def test_deleting_a_session_leaves_no_journal_behind(self, store, service):
        """The journal holds the notification payloads verbatim — the same
        conversation. It used to survive a delete unless the session
        happened to be loaded in the process."""
        from omniagents.core.session.event_log import SessionEventLog

        session = seed_three_turns(service, "sess-journal")
        assert SessionEventLog(session.id).retained_events()
        service._session_manager.drop(session.id)

        assert asyncio.run(service.delete_session("sess-journal")) is True
        assert SessionEventLog("sess-journal").retained_events() == []
        assert store.get_thread("sess-journal") is None

    def test_retention_purges_every_store(self, store, service):
        import sqlite3

        from omniagents.core.session.event_log import SessionEventLog
        from omniagents.core.session import history_db

        session = seed_three_turns(service, "sess-old")
        assert store.get_thread("sess-old") is not None
        assert SessionEventLog("sess-old").retained_events()

        # Age the row past the window.
        conn, lock = history_db._get_store(None, None)
        with lock:
            conn.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                ("2000-01-01 00:00:00", "sess-old"),
            )
            conn.commit()

        result = asyncio.run(service.purge_threads(30))
        assert result["purged_thread_ids"] == ["sess-old"]
        assert store.get_thread("sess-old") is None
        assert SessionEventLog("sess-old").retained_events() == []
        assert history_db.load_history("sess-old") == []
        assert isinstance(conn, sqlite3.Connection)

    def test_retention_dry_run_destroys_nothing(self, store, service):
        from omniagents.core.session import history_db

        seed_three_turns(service, "sess-old")
        conn, lock = history_db._get_store(None, None)
        with lock:
            conn.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                ("2000-01-01 00:00:00", "sess-old"),
            )
            conn.commit()

        preview = asyncio.run(service.purge_threads(30, dry_run=True))
        assert preview["dry_run"] is True
        assert preview["purged_thread_ids"] == ["sess-old"]
        assert store.get_thread("sess-old") is not None
        assert history_db.load_history("sess-old") != []

    def test_retention_skips_a_session_on_legal_hold(self, store, service):
        from omniagents.core.session import history_db

        seed_three_turns(service, "sess-held")
        conn, lock = history_db._get_store(None, None)
        with lock:
            conn.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                ("2000-01-01 00:00:00", "sess-held"),
            )
            conn.commit()
        history_db.set_session_hold("sess-held", True)

        assert asyncio.run(service.purge_threads(30))["purged_thread_ids"] == []
        assert (
            asyncio.run(service.purge_threads(30, dry_run=True))["purged_thread_ids"]
            == []
        )
        assert store.get_thread("sess-held") is not None

    def test_retention_with_a_non_positive_window_is_a_no_op(self, store, service):
        seed_three_turns(service, "sess-keep")
        assert asyncio.run(service.purge_threads(0))["count"] == 0
        assert store.get_thread("sess-keep") is not None


# ---------------------------------------------------------------------
# Turn-boundary truncation helper (shared with the legacy projection)
# ---------------------------------------------------------------------


def test_history_truncation_counts_user_rows_not_turns():
    from omniagents.core.conversation.adapter import (
        project_thread,
        truncate_history_at_user_row,
    )

    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "three"},
    ]
    # With one user row per turn the two readings coincide, and the
    # projection of every cut agrees with the count asked for.
    for count in range(0, 4):
        cut = truncate_history_at_user_row(history, count)
        projected = project_thread("t", cut)
        assert len(projected.turns) == min(
            count, 3
        ), f"cutting at {count} user rows projected {len(projected.turns)}"
    assert truncate_history_at_user_row(history, 0) == []
    assert truncate_history_at_user_row(history, 99) == history
    # include_next_row keeps the question and none of the answer.
    assert (
        truncate_history_at_user_row(history, 1, include_next_row=True) == history[:3]
    )
    assert (
        truncate_history_at_user_row(history, 0, include_next_row=True) == history[:1]
    )


def test_a_service_level_thread_listing_is_scoped_and_paged():
    """``ConversationService.list_threads`` must not widen the filter it is
    handed, and must page the same set the count reports."""
    from omniagents.core.conversation.model import Thread, ThreadFilter
    from omniagents.core.conversation.store import SqliteConversationStore
    import tempfile
    import os

    directory = tempfile.mkdtemp()
    backend = SqliteConversationStore(path=os.path.join(directory, "domain.db"))
    try:
        for index in range(120):
            backend.upsert_thread(
                Thread(
                    thread_id=f"t{index:04d}",
                    user_id="u1" if index % 2 else "u2",
                    created_at=float(index),
                    updated_at=float(index),
                )
            )
        service = ConversationService(store=backend)
        seen: list[str] = []
        cursor = None
        while True:
            page = service.list_threads(
                thread_filter=ThreadFilter(user_id="u1"), limit=13, cursor=cursor
            )
            assert page["total"] == 60
            seen.extend(entry["thread_id"] for entry in page["threads"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        assert len(seen) == 60
        assert len(set(seen)) == 60
    finally:
        backend.close()


# ---------------------------------------------------------------------
# The notification path is a materialization path in its own right
# ---------------------------------------------------------------------


class TestForkOverTheNotificationPath:
    """``session_forked`` is how a fork is materialized when the RPC
    handler is not the producer — a replayed emission, or any other caller
    of the chokepoint. The branch point therefore has to travel ON the
    notification: a fork materialized from a payload without it would copy
    the parent's ENTIRE transcript and the branch would silently not be a
    branch."""

    def _recorder(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder

        return ConversationRecorder(store)

    def test_the_notification_truncates_at_its_branch_point(self, store, service):
        parent = seed_three_turns(service, "sess-notify")
        parent_items = store.list_items(parent.id, limit=100)
        branch = max(
            (i for i in parent_items if i.turn_id == "run1"), key=lambda i: i.seq
        )

        self._recorder(store).record(
            parent.id,
            "session_forked",
            {
                "session_id": parent.id,
                "new_session_id": "sess-notify-child",
                "from_item_id": branch.item_id,
            },
        )

        child_items = store.list_items("sess-notify-child", limit=100)
        assert [i.seq for i in child_items] == list(range(1, branch.seq + 1))
        assert len(child_items) < len(parent_items)
        assert store.get_thread("sess-notify-child").branched_from_item_id == (
            branch.item_id
        )

    def test_a_notification_without_a_branch_point_copies_everything(
        self, store, service
    ):
        parent = seed_three_turns(service, "sess-notify")
        self._recorder(store).record(
            parent.id,
            "session_forked",
            {"session_id": parent.id, "new_session_id": "sess-notify-whole"},
        )
        assert len(store.list_items("sess-notify-whole", limit=100)) == len(
            store.list_items(parent.id, limit=100)
        )

    def test_an_unresolvable_branch_point_materializes_nothing(self, store, service):
        """Falling back to an untruncated copy would be silent data
        expansion: the user asked for a branch and would get the lot."""
        parent = seed_three_turns(service, "sess-notify")
        self._recorder(store).record(
            parent.id,
            "session_forked",
            {
                "session_id": parent.id,
                "new_session_id": "sess-notify-bad",
                "from_item_id": "itm_does_not_exist",
            },
        )
        assert store.list_items("sess-notify-bad", limit=100) == []


# ---------------------------------------------------------------------
# Retention timestamp parsing
# ---------------------------------------------------------------------


class TestRetentionTimestampParsing:
    def test_an_undecodable_timestamp_reads_as_recent(self):
        """A parse failure that read as epoch 0 would make a dry run
        propose deleting every session it could not decode."""
        from datetime import datetime, timezone

        from omniagents.core.agents.service import _session_row_epoch

        assert _session_row_epoch({"created_at": "not a timestamp"}) == float("inf")
        assert _session_row_epoch({"created_at": None}) == float("inf")
        assert _session_row_epoch({}) == float("inf")
        assert _session_row_epoch({"created_at": 1234.5}) == 1234.5
        assert _session_row_epoch({"created_at": "2000-01-01 00:00:00"}) == (
            datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
        )
        assert (
            _session_row_epoch(
                {"created_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}
            )
            == datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
        )

    def test_a_session_with_an_undecodable_timestamp_is_not_proposed(
        self, store, service
    ):
        from omniagents.core.session import history_db

        seed_three_turns(service, "sess-garbled")
        conn, lock = history_db._get_store(None, None)
        with lock:
            conn.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                ("garbled-not-a-date", "sess-garbled"),
            )
            conn.commit()

        preview = asyncio.run(service.purge_threads(30, dry_run=True))
        assert preview["purged_thread_ids"] == []


# ---------------------------------------------------------------------
# The child's model context must not outrun its transcript, EVEN when a
# turn holds more than one user row (send_user_message mid-run).
# ---------------------------------------------------------------------


class TestForkContextMatchesTranscript:
    def _user_texts(self, service, session_id):
        session = service._session_manager.get_or_create(session_id)
        return [
            row.get("content") for row in session.history if row.get("role") == "user"
        ]

    def _assistant_texts(self, service, session_id):
        session = service._session_manager.get_or_create(session_id)
        return [
            row.get("content")
            for row in session.history
            if row.get("role") == "assistant"
        ]

    def test_a_mid_run_user_message_does_not_shift_the_cut(self, store, service):
        """The defect a turn COUNT hides.

        Turn 2 carries two user rows, so "two turns" and "two user rows"
        name different positions in the raw history. Forking at the tail of
        turn 2 must keep BOTH of turn 2's questions in the child's context,
        and none of turn 3.
        """
        session = service._session_manager.get_or_create("sess-ctx")
        run_turn(service, session, "run1", "question one")
        run_turn_with_mid_run_message(
            service, session, "run2", "question two", "second thoughts"
        )
        run_turn(service, session, "run3", "question three")

        # The shape that breaks a turn count: more user rows than turns.
        assert len(self._user_texts(service, session.id)) == 4
        assert store.count_turns(session.id) == 3

        asyncio.run(
            service.fork_session(
                session.id, new_session_id="sess-ctx-child", from_turn_id="run2"
            )
        )

        child_items = store.list_items("sess-ctx-child", limit=100)
        child_texts = {
            item.content.get("text") for item in child_items if item.content.get("text")
        }
        assert "question two" in child_texts
        assert "second thoughts" in child_texts
        assert "question three" not in child_texts

        history_users = self._user_texts(service, "sess-ctx-child")
        assert history_users == ["question one", "question two", "second thoughts"], (
            "the model context does not match the transcript: a turn count "
            "and a user-row cut landed in different places"
        )
        assert "reply to second thoughts" in self._assistant_texts(
            service, "sess-ctx-child"
        )
        assert "reply to question three" not in self._assistant_texts(
            service, "sess-ctx-child"
        )

    def test_branching_at_a_question_excludes_its_answer(self, store, service):
        """ "Re-ask from here": the child holds the question, not the reply —
        and every completed turn BEFORE it, answers included."""
        session = service._session_manager.get_or_create("sess-reask")
        run_turn(service, session, "run1", "question one")
        run_turn(service, session, "run2", "question two")
        run_turn(service, session, "run3", "question three")

        items = store.list_items(session.id, limit=100)
        branch = next(
            item
            for item in items
            if item.turn_id == "run3" and item.kind is ItemKind.USER_MESSAGE
        )
        asyncio.run(
            service.fork_session(
                session.id,
                new_session_id="sess-reask-child",
                from_item_id=branch.item_id,
            )
        )

        child_items = store.list_items("sess-reask-child", limit=100)
        assert max(i.seq for i in child_items) == branch.seq
        assert self._user_texts(service, "sess-reask-child") == [
            "question one",
            "question two",
            "question three",
        ]
        replies = self._assistant_texts(service, "sess-reask-child")
        # Everything before the branch point stays — cutting too much is
        # just as wrong as cutting too little.
        assert "reply to question one" in replies
        assert "reply to question two" in replies
        # …and the answer being re-asked does not.
        assert "reply to question three" not in replies

    def test_a_non_cuttable_position_is_refused_not_approximated(self, store, service):
        """Mid-segment: a tool call with the turn's reply still to come."""
        session = service._session_manager.get_or_create("sess-mid")
        run_turn(service, session, "run1", "question one")
        items = store.list_items(session.id, limit=100)
        tool = next(i for i in items if i.kind is ItemKind.TOOL_CALL)
        assert tool.seq < max(i.seq for i in items)

        with pytest.raises(ForkPointInvalid) as exc:
            asyncio.run(
                service.fork_session(
                    session.id,
                    new_session_id="sess-mid-child",
                    from_item_id=tool.item_id,
                )
            )
        assert exc.value.data["kind"] == "fork_point_invalid"
        assert exc.value.data["item_id"] == tool.item_id
        assert store.get_thread("sess-mid-child") is None

    def test_an_item_with_no_history_row_does_not_block_a_cut(self, store, service):
        """An approval puts nothing into history, so a branch at the item
        before it is still exactly cuttable."""
        from omniagents.core.session.event_log import stamp_session_event

        session = service._session_manager.get_or_create("sess-appr")
        run_turn(service, session, "run1", "question one")
        session.active_run_id = "run1"
        stamp_session_event(
            session,
            "tool_approval_requested",
            {
                "session_id": session.id,
                "run_id": "run1",
                "call_id": "c-appr",
                "tool_name": "execute_bash",
                "arguments": "{}",
            },
        )
        items = store.list_items(session.id, limit=100)
        assert items[-1].kind is ItemKind.APPROVAL
        message = next(i for i in items if i.kind is ItemKind.AGENT_MESSAGE)

        asyncio.run(
            service.fork_session(
                session.id,
                new_session_id="sess-appr-child",
                from_item_id=message.item_id,
            )
        )
        child = store.list_items("sess-appr-child", limit=100)
        assert max(i.seq for i in child) == message.seq


# ---------------------------------------------------------------------
# Cascade ordering, unbounded walk, foreign owners, legal hold
# ---------------------------------------------------------------------


class TestCascadeSafety:
    def _chain(self, store, depth, prefix="deep", user_id=None):
        from omniagents.core.conversation.model import Thread

        previous = None
        for index in range(depth):
            thread_id = f"{prefix}-{index:03d}"
            store.upsert_thread(
                Thread(
                    thread_id=thread_id,
                    user_id=user_id,
                    parent_thread_id=previous,
                    created_at=1.0 + index,
                    updated_at=1.0 + index,
                )
            )
            previous = thread_id
        return [f"{prefix}-{index:03d}" for index in range(depth)]

    def test_the_guard_walk_is_not_bounded_by_the_read_caps(self, store, service):
        from omniagents.core.conversation.model import Thread
        from omniagents.core.conversation.service import MAX_DESCENDANTS

        store.upsert_thread(Thread(thread_id="wide", created_at=1.0, updated_at=1.0))
        total = MAX_DESCENDANTS + 5
        for index in range(total):
            store.upsert_thread(
                Thread(
                    thread_id=f"wide-{index:04d}",
                    parent_thread_id="wide",
                    created_at=2.0,
                    updated_at=2.0,
                )
            )

        with pytest.raises(ThreadHasDescendants) as exc:
            asyncio.run(service.delete_session("wide"))
        # The COUNT is exact even though the enumerated list is clipped.
        assert exc.value.data["descendant_count"] == total
        assert exc.value.data["truncated"] is True
        assert len(exc.value.data["descendant_thread_ids"]) < total

        asyncio.run(service.delete_session("wide", cascade=True))
        assert store.get_thread("wide") is None
        for index in range(total):
            assert (
                store.get_thread(f"wide-{index:04d}") is None
            ), f"wide-{index:04d} survived with a dangling parent_thread_id"

    def test_a_chain_deeper_than_the_read_cap_is_fully_deleted(self, store, service):
        from omniagents.core.conversation.service import MAX_DESCENDANT_DEPTH

        ids = self._chain(store, MAX_DESCENDANT_DEPTH + 5)
        asyncio.run(service.delete_session(ids[0], cascade=True))
        survivors = [tid for tid in ids if store.get_thread(tid) is not None]
        assert survivors == [], f"orphaned below the read depth cap: {survivors}"

    def test_the_cascade_deletes_deepest_first(self, store, service):
        """An interrupted cascade must leave a shorter valid chain, never a
        child pointing at a parent that is already gone."""
        ids = self._chain(store, 6, prefix="order")
        order: list[str] = []
        original = service._delete_one_session

        async def recording(session_id):
            order.append(session_id)
            return await original(session_id)

        service._delete_one_session = recording  # type: ignore[assignment]
        try:
            asyncio.run(service.delete_session(ids[0], cascade=True))
        finally:
            service._delete_one_session = original  # type: ignore[assignment]

        assert order == list(reversed(ids)), order
        # …and at every prefix of that order the survivors form a valid
        # chain: nobody points at a thread that is already deleted.
        for cut in range(len(order) + 1):
            deleted = set(order[:cut])
            for thread_id in ids:
                if thread_id in deleted:
                    continue
                index = ids.index(thread_id)
                parent = ids[index - 1] if index else None
                assert parent not in deleted, (
                    f"after deleting {order[:cut]}, {thread_id} points at a "
                    f"deleted parent {parent}"
                )

    def test_the_archive_cascade_is_not_bounded_by_the_read_caps(self, store, service):
        """The archive cascade shares the guard walk, so it shares the fix.

        A subtree bigger than the ancestry READ caps must archive whole:
        leaving the overflow active is the "hiding the trunk while the
        branches stay in view" failure the cascade policy exists to
        prevent."""
        from omniagents.core.conversation.model import Thread, ThreadStatus
        from omniagents.core.conversation.service import (
            MAX_DESCENDANTS,
            MAX_DESCENDANT_DEPTH,
        )

        store.upsert_thread(
            Thread(thread_id="arch-root", created_at=1.0, updated_at=1.0)
        )
        wide = MAX_DESCENDANTS + 5
        for index in range(wide):
            store.upsert_thread(
                Thread(
                    thread_id=f"arch-{index:04d}",
                    parent_thread_id="arch-root",
                    created_at=2.0,
                    updated_at=2.0,
                )
            )
        previous = "arch-root"
        for index in range(MAX_DESCENDANT_DEPTH + 5):
            thread_id = f"archdeep-{index:03d}"
            store.upsert_thread(
                Thread(
                    thread_id=thread_id,
                    parent_thread_id=previous,
                    created_at=3.0,
                    updated_at=3.0,
                )
            )
            previous = thread_id

        asyncio.run(service.update_thread("arch-root", status="archived"))
        stragglers = [
            thread_id
            for thread_id in (
                [f"arch-{i:04d}" for i in range(wide)]
                + [f"archdeep-{i:03d}" for i in range(MAX_DESCENDANT_DEPTH + 5)]
            )
            if store.get_thread(thread_id).status is not ThreadStatus.ARCHIVED
        ]
        assert stragglers == [], (
            f"{len(stragglers)} descendants stayed active past the read caps: "
            f"{stragglers[:5]}"
        )

    def test_a_foreign_owned_descendant_blocks_a_plain_delete(self, store, service):
        """The guard is an INTEGRITY question, so it must see descendants it
        would not be allowed to read."""
        from omniagents.core.conversation.model import Thread

        parent = seed_three_turns(service, "sess-owned")
        store.upsert_thread(
            Thread(
                thread_id="foreign-child",
                user_id="someone-else",
                parent_thread_id=parent.id,
                created_at=9.0,
                updated_at=9.0,
            )
        )
        with pytest.raises(ThreadHasDescendants) as exc:
            asyncio.run(service.delete_session(parent.id))
        assert exc.value.data["descendant_thread_ids"] == ["foreign-child"]
        assert store.get_thread(parent.id) is not None
        assert store.get_thread("foreign-child") is not None

    def test_an_unowned_descendant_blocks_a_plain_delete(self, store, service):
        """``user_id IS NULL`` is the normal state of a backfilled shard."""
        from omniagents.core.conversation.model import Thread

        parent = seed_three_turns(service, "sess-null")
        store.upsert_thread(
            Thread(
                thread_id="unowned-child",
                user_id=None,
                parent_thread_id=parent.id,
                created_at=9.0,
                updated_at=9.0,
            )
        )
        with pytest.raises(ThreadHasDescendants):
            asyncio.run(service.delete_session(parent.id))
        assert store.get_thread("unowned-child") is not None

    def test_legal_hold_is_checked_before_anything_is_deleted(self, store, service):
        """A hold that only takes effect after the branches are gone is not
        a hold."""
        from omniagents.core.session import history_db

        parent = seed_three_turns(service, "sess-held")
        asyncio.run(service.fork_session(parent.id, new_session_id="held-child"))
        asyncio.run(service.fork_session("held-child", new_session_id="held-grand"))
        assert history_db.set_session_hold(parent.id, True) is True

        with pytest.raises(ThreadOnLegalHold) as exc:
            asyncio.run(service.delete_session(parent.id, cascade=True))
        assert exc.value.data["held_thread_ids"] == [parent.id]
        for thread_id in (parent.id, "held-child", "held-grand"):
            assert (
                store.get_thread(thread_id) is not None
            ), f"{thread_id} was destroyed before the hold was discovered"

    def test_a_held_descendant_blocks_the_whole_cascade(self, store, service):
        from omniagents.core.session import history_db

        parent = seed_three_turns(service, "sess-held2")
        asyncio.run(service.fork_session(parent.id, new_session_id="held2-child"))
        asyncio.run(service.fork_session("held2-child", new_session_id="held2-grand"))
        history_db.set_session_hold("held2-child", True)

        with pytest.raises(ThreadOnLegalHold) as exc:
            asyncio.run(service.delete_session(parent.id, cascade=True))
        assert exc.value.data["held_thread_ids"] == ["held2-child"]
        for thread_id in (parent.id, "held2-child", "held2-grand"):
            assert store.get_thread(thread_id) is not None

    def test_a_failing_hold_lookup_refuses_the_delete(
        self, store, service, monkeypatch
    ):
        """Fails CLOSED: a storage hiccup must not delete data marked
        undeletable."""
        from omniagents.core.session import history_db

        parent = seed_three_turns(service, "sess-holderr")

        def boom(*args, **kwargs):
            raise RuntimeError("hold store unavailable")

        monkeypatch.setattr(history_db, "get_session_hold", boom)
        with pytest.raises(ThreadOnLegalHold):
            asyncio.run(service.delete_session(parent.id))
        assert store.get_thread(parent.id) is not None


# ---------------------------------------------------------------------
# Metadata caps
# ---------------------------------------------------------------------


class TestMetadataCaps:
    def test_an_oversized_metadata_value_is_refused(self, store, service):
        from omniagents.core.conversation.service import METADATA_MAX_BYTES

        session = seed_three_turns(service, "sess-bigmeta")
        with pytest.raises(ThreadMetadataInvalid) as exc:
            asyncio.run(
                service.update_thread(
                    session.id, metadata={"blob": "x" * (METADATA_MAX_BYTES + 10)}
                )
            )
        assert exc.value.data["kind"] == "thread_metadata_invalid"
        assert store.get_thread(session.id).metadata == {}

    def test_too_many_metadata_keys_are_refused(self, store, service):
        from omniagents.core.conversation.service import METADATA_MAX_KEYS

        session = seed_three_turns(service, "sess-manymeta")
        with pytest.raises(ThreadMetadataInvalid):
            asyncio.run(
                service.update_thread(
                    session.id,
                    metadata={f"k{i}": i for i in range(METADATA_MAX_KEYS + 1)},
                )
            )
        assert store.get_thread(session.id).metadata == {}

    def test_the_cap_applies_to_the_merged_record_not_the_delta(self, store, service):
        """Otherwise a client grows past any cap one small update at a time."""
        from omniagents.core.conversation.service import METADATA_MAX_KEYS

        session = seed_three_turns(service, "sess-growmeta")
        for index in range(METADATA_MAX_KEYS):
            asyncio.run(service.update_thread(session.id, metadata={f"k{index}": 1}))
        assert len(store.get_thread(session.id).metadata) == METADATA_MAX_KEYS
        with pytest.raises(ThreadMetadataInvalid):
            asyncio.run(service.update_thread(session.id, metadata={"one-more": 1}))

    def test_a_metadata_update_within_the_caps_still_works(self, store, service):
        session = seed_three_turns(service, "sess-okmeta")
        asyncio.run(service.update_thread(session.id, metadata={"label": "release"}))
        assert store.get_thread(session.id).metadata == {"label": "release"}


def test_truncation_keeps_everything_when_there_are_no_user_rows():
    """A thread whose turns were all opened by assistant-role wakeups
    (``prompt_role: "assistant"`` notification batches) has no user rows.
    "Keep up to the 1st user row" then means keep everything, not nothing —
    a short-circuit on the count would hand such a fork an empty context.
    """
    from omniagents.core.conversation.adapter import truncate_history_at_user_row

    history = [
        {"role": "assistant", "content": "wakeup batch"},
        {"role": "assistant", "content": "did the work"},
    ]
    assert truncate_history_at_user_row(history, 0) == history
    assert truncate_history_at_user_row(history, 0, include_next_row=True) == history
    # And a history that DOES open with a user row still cuts at it.
    with_user = [{"role": "user", "content": "q"}, *history]
    assert truncate_history_at_user_row(with_user, 0) == []


# ---------------------------------------------------------------------
# The wakeup-follow refusal, and the -32112 payload that explains it
# ---------------------------------------------------------------------


def wakeup_turn(service, session, run_id, prompt):
    """A turn opened by an ASSISTANT-role wakeup.

    Routines, ``enqueue_notification`` batches and resident-agent day
    sessions all open turns this way, so a thread that mixes user turns and
    wakeup turns is ordinary, not exotic.
    """
    from omniagents.core.session.event_log import stamp_session_event

    session.active_run_id = run_id
    session.append_message({"role": "assistant", "content": prompt})
    stamp_session_event(
        session,
        "run_started",
        {
            "session_id": session.id,
            "run_id": run_id,
            "prompt": prompt,
            "prompt_role": "assistant",
        },
    )
    stamp_session_event(
        session,
        "message_output",
        {"session_id": session.id, "run_id": run_id, "content": f"handled {prompt}"},
    )
    stamp_session_event(
        session,
        "run_end",
        {"session_id": session.id, "run_id": run_id, "end_reason": "completed"},
    )
    session.append_message({"role": "assistant", "content": f"handled {prompt}"})
    session.active_run_id = None


class TestWakeupFollowedTurns:
    def test_forking_a_turn_followed_by_a_wakeup_is_refused_with_a_reason(
        self, store, service
    ):
        """`from_turn_id` does NOT always resolve.

        When the next turn was opened by an assistant-role wakeup, its
        first item is an ``agent_message`` — history-bearing — so the
        previous turn's tail is not the end of its segment and the cut is
        not expressible in user rows. The refusal is honest; the payload
        has to say what blocked it.
        """
        session = service._session_manager.get_or_create("sess-wake")
        run_turn(service, session, "run1", "question one")
        wakeup_turn(service, session, "run2", "background batch finished")

        with pytest.raises(ForkPointInvalid) as exc:
            asyncio.run(
                service.fork_session(
                    session.id, new_session_id="sess-wake-child", from_turn_id="run1"
                )
            )
        data = exc.value.data
        assert data["kind"] == "fork_point_invalid"
        # The blocker is named AND typed: agent_message is what tells a
        # client "the next turn was a wakeup, not a question".
        assert data["next_item_kind"] == ItemKind.AGENT_MESSAGE.value
        blocker = store.get_item(session.id, data["next_item_id"])
        assert blocker is not None
        assert blocker.turn_id == "run2"
        assert store.get_thread("sess-wake-child") is None

    def test_the_wakeup_turn_itself_is_still_forkable(self, store, service):
        """The refusal is about the position, not about wakeups: the tail
        of the wakeup turn is the end of the thread and cuts exactly."""
        session = service._session_manager.get_or_create("sess-wake2")
        run_turn(service, session, "run1", "question one")
        wakeup_turn(service, session, "run2", "background batch finished")

        asyncio.run(
            service.fork_session(
                session.id, new_session_id="sess-wake2-child", from_turn_id="run2"
            )
        )
        child = store.list_items("sess-wake2-child", limit=100)
        assert [i.seq for i in child] == [
            i.seq for i in store.list_items(session.id, limit=100)
        ]

    def test_a_user_turn_after_a_wakeup_turn_still_resolves(self, store, service):
        """The common shape still works: a wakeup turn followed by a real
        question cuts at the question's user row."""
        session = service._session_manager.get_or_create("sess-wake3")
        run_turn(service, session, "run1", "question one")
        wakeup_turn(service, session, "run2", "background batch finished")
        run_turn(service, session, "run3", "question two")

        asyncio.run(
            service.fork_session(
                session.id, new_session_id="sess-wake3-child", from_turn_id="run2"
            )
        )
        child = store.list_items("sess-wake3-child", limit=100)
        assert max(i.seq for i in child) == max(
            i.seq
            for i in store.list_items(session.id, limit=100)
            if i.turn_id == "run2"
        )
        child_session = service._session_manager.get_or_create("sess-wake3-child")
        contents = [row.get("content") for row in child_session.history]
        assert "handled background batch finished" in contents
        assert "question two" not in contents


# ---------------------------------------------------------------------
# The archive cascade is an ACCESS decision, not just an integrity walk
# ---------------------------------------------------------------------


class TestArchiveCascadeScoping:
    def _tree(self, store, service):
        from omniagents.core.conversation.model import Thread

        parent = seed_three_turns(service, "sess-scope")
        for thread_id, owner in (
            ("mine", "alice"),
            ("theirs", "bob"),
            ("nobodys", None),
        ):
            store.upsert_thread(
                Thread(
                    thread_id=thread_id,
                    user_id=owner,
                    parent_thread_id=parent.id,
                    created_at=9.0,
                    updated_at=9.0,
                )
            )
        return parent

    def test_the_cascade_skips_what_the_caller_cannot_access(self, store, service):
        from omniagents.core.conversation.model import ThreadStatus

        parent = self._tree(store, service)
        outcome = service._conversation_service().set_status(
            parent.id,
            ThreadStatus.ARCHIVED,
            can_access=lambda owner: owner == "alice",
        )
        assert outcome.moved == ["mine"]
        assert outcome.skipped == 2
        assert store.get_thread("mine").status is ThreadStatus.ARCHIVED
        assert store.get_thread("theirs").status is ThreadStatus.ACTIVE
        assert store.get_thread("nobodys").status is ThreadStatus.ACTIVE
        # The named thread itself always moves — its access was decided by
        # the RPC gate before the cascade ran.
        assert store.get_thread(parent.id).status is ThreadStatus.ARCHIVED

    def test_an_unscoped_cascade_is_not_expressible(self, store, service):
        """``can_access`` has no default, for the same reason
        ``ThreadFilter.user_id`` has none."""
        from omniagents.core.conversation.model import ThreadStatus

        parent = self._tree(store, service)
        with pytest.raises(TypeError):
            service._conversation_service().set_status(parent.id, ThreadStatus.ARCHIVED)

    def test_a_permissive_policy_still_cascades_everything(self, store, service):
        """Single-user deployments must be untouched by the scoping."""
        from omniagents.core.conversation.model import ThreadStatus

        parent = self._tree(store, service)
        outcome = service._conversation_service().set_status(
            parent.id, ThreadStatus.ARCHIVED, can_access=lambda owner: True
        )
        assert sorted(outcome.moved) == ["mine", "nobodys", "theirs"]
        assert outcome.skipped == 0


# ---------------------------------------------------------------------
# Retention, at the byte level
# ---------------------------------------------------------------------

SECRET_MARKER = "zzretentionmarker314c"


def _files_containing(directory: str, needle: str) -> list[str]:
    import glob
    import os

    hits = []
    for path in glob.glob(os.path.join(directory, "**", "*"), recursive=True):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError:  # pragma: no cover - defensive
            continue
        if needle.encode() in blob:
            hits.append(os.path.basename(path))
    return sorted(hits)


class TestRetentionLeavesNoBytes:
    """ "Deletes every store's copy" has to be true of the FILE, not just of
    the query results.

    SQLite's FTS5 deletes are tombstones: the terms of a deleted transcript
    survive in the index's segment blobs, greppable with a hex editor,
    after ``delete_thread`` returns and even after ``VACUUM`` — the pages
    were never freed, so ``PRAGMA secure_delete`` cannot help either. The
    search API cannot see this (it joins ``threads``, which is gone), which
    is exactly why it needs a test that reads the bytes.
    """

    def _seed(self, service, session_id):
        session = service._session_manager.get_or_create(session_id)
        run_turn(service, session, "run1", f"remember {SECRET_MARKER} please")
        return session

    def _root(self):
        import os

        from omniagents.core import paths

        return os.path.dirname(paths.get_sessions_db_path(None, None))

    def test_delete_session_leaves_no_trace_in_any_file(self, store, service):
        self._seed(service, "sess-bytes")
        root = self._root()
        assert _files_containing(root, SECRET_MARKER), "the marker was never written"

        assert asyncio.run(service.delete_session("sess-bytes")) is True

        residue = _files_containing(root, SECRET_MARKER)
        assert residue == [], f"the deleted transcript survives in: {residue}"

    def test_an_unloaded_session_is_scrubbed_too(self, store, service):
        self._seed(service, "sess-bytes2")
        service._session_manager.drop("sess-bytes2")
        assert asyncio.run(service.delete_session("sess-bytes2")) is True
        assert _files_containing(self._root(), SECRET_MARKER) == []

    def test_purge_threads_leaves_no_trace_either(self, store, service):
        from omniagents.core.session import history_db

        self._seed(service, "sess-bytes3")
        conn, lock = history_db._get_store(None, None)
        with lock:
            conn.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                ("2000-01-01 00:00:00", "sess-bytes3"),
            )
            conn.commit()

        assert asyncio.run(service.purge_threads(30))["purged_thread_ids"] == [
            "sess-bytes3"
        ]
        residue = _files_containing(self._root(), SECRET_MARKER)
        assert residue == [], f"the purged transcript survives in: {residue}"

    def test_the_index_tombstones_are_what_needs_compacting(self, tmp_path):
        """Pin the underlying mechanism, so the fix cannot be quietly
        reduced to "the rows were deleted"."""
        from omniagents.core.conversation.model import (
            Item,
            ItemKind,
            ItemStatus,
            Thread,
        )
        from omniagents.core.conversation.store import SqliteConversationStore

        path = str(tmp_path / "domain.db")
        backend = SqliteConversationStore(path=path)
        try:
            backend.upsert_thread(
                Thread(thread_id="t1", user_id="u1", title=f"title {SECRET_MARKER}")
            )
            backend.upsert_item(
                Item(
                    item_id="i1",
                    thread_id="t1",
                    turn_id="r1",
                    seq=1,
                    kind=ItemKind.AGENT_MESSAGE,
                    status=ItemStatus.COMPLETED,
                    content={"text": f"remember {SECRET_MARKER}"},
                )
            )

            def present() -> bool:
                with open(path, "rb") as handle:
                    return SECRET_MARKER.encode() in handle.read()

            assert present()
            backend.delete_thread("t1")
            # Every ROW is gone and the search API is clean…
            assert (
                backend._connect()
                .execute("SELECT COUNT(*) FROM item_search")
                .fetchone()[0]
                == 0
            )
            # …and the bytes are still there, in the FTS segment blobs.
            assert present(), (
                "the tombstone premise no longer holds; this test's fix "
                "may no longer be needed"
            )
            backend.compact_search_index()
            assert not present(), "compaction did not reclaim the tombstones"
        finally:
            backend.close()


def test_export_declares_a_clipped_turn_header_list(store, service):
    """ "No silent cap" has to be true of all three lists or of none.

    The ITEMS are never capped. The turn HEADERS are a convenience with a
    ceiling, and a thread past it must say so rather than quietly return a
    short list — which is what the contract already promised.
    """
    from omniagents.core.conversation.model import Thread, Turn
    from omniagents.core.conversation.service import MAX_PAGE_SIZE

    store.upsert_thread(
        Thread(thread_id="sess-manyturns", created_at=1.0, updated_at=1.0)
    )
    for ordinal in range(MAX_PAGE_SIZE + 3):
        store.upsert_turn(
            Turn(
                thread_id="sess-manyturns",
                turn_id=f"run{ordinal:04d}",
                ordinal=ordinal + 1,
                created_at=1.0,
                updated_at=1.0,
            )
        )

    page = asyncio.run(service.export_thread("sess-manyturns"))
    assert len(page["turns"]) == MAX_PAGE_SIZE
    assert page["turns_truncated"] is True

    # A thread inside the ceiling reports the whole list and says so.
    small = seed_three_turns(service, "sess-fewturns")
    page = asyncio.run(service.export_thread(small.id))
    assert len(page["turns"]) == 3
    assert page["turns_truncated"] is False

    # Later pages carry no headers and make no claim about them.
    paged = asyncio.run(service.export_thread(small.id, limit=1))
    following = asyncio.run(
        service.export_thread(small.id, limit=1, cursor=paged["next_cursor"])
    )
    assert following["turns"] == []
    assert following["turns_truncated"] is False
