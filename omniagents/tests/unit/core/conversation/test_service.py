"""Read path: pagination over large histories, lazy migration, errors."""

import pytest

from omniagents.core.conversation.model import Item, ItemKind, ItemStatus, Thread
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.service import (
    MAX_PAGE_SIZE,
    ConversationService,
    InvalidCursor,
    ItemNotFound,
    ThreadNotFound,
)
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.session.history_db import append_message, register_session

THREAD = "sess-1"


@pytest.fixture
def store(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    yield backend
    backend.close()


@pytest.fixture
def service(store):
    return ConversationService(store=store)


def seed_items(store, count, *, thread_id=THREAD, turn_id="run1"):
    store.upsert_thread(
        Thread(
            thread_id=thread_id,
            created_at=1.0,
            updated_at=1.0,
            last_seq=count,
            item_count=count,
        )
    )
    for seq in range(1, count + 1):
        store.upsert_item(
            Item(
                item_id=f"itm_{seq}",
                thread_id=thread_id,
                turn_id=turn_id,
                seq=seq,
                kind=ItemKind.AGENT_MESSAGE if seq % 2 else ItemKind.TOOL_CALL,
                status=ItemStatus.COMPLETED,
                created_at=1.0,
                updated_at=1.0,
                content={"text": f"m{seq}"},
            )
        )


class TestPagination:
    @pytest.mark.timeout(300)  # thousands of durable commits; slow on Windows
    def test_walks_a_large_history_exactly_once(self, store, service):
        """5,000 items — the case pagination exists for. Every item is seen
        exactly once, in ascending seq order, with no gaps and no repeats."""
        total = 5000
        seed_items(store, total)

        seen = []
        cursor = None
        pages = 0
        while True:
            page = service.list_items(THREAD, limit=200, cursor=cursor)
            pages += 1
            seen.extend(item["seq"] for item in page["items"])
            assert page["total"] == total
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            cursor = page["next_cursor"]
            assert cursor is not None
            assert pages < 100, "pagination failed to terminate"

        assert seen == list(range(1, total + 1))
        assert pages == 25

    def test_a_page_is_never_larger_than_requested(self, store, service):
        seed_items(store, 100)
        assert len(service.list_items(THREAD, limit=17)["items"]) == 17

    def test_limit_is_clamped_to_the_ceiling(self, store, service):
        seed_items(store, MAX_PAGE_SIZE + 50)
        page = service.list_items(THREAD, limit=100_000)
        assert len(page["items"]) == MAX_PAGE_SIZE
        assert page["has_more"] is True

    def test_items_appended_mid_walk_are_not_skipped_or_repeated(self, store, service):
        """Keyset paging, not offsets: writes landing between page requests
        must not shift the window."""
        seed_items(store, 30)
        first = service.list_items(THREAD, limit=10)
        # A new item lands while the client holds a cursor.
        store.upsert_item(
            Item(
                item_id="itm_31",
                thread_id=THREAD,
                turn_id="run1",
                seq=31,
                kind=ItemKind.AGENT_MESSAGE,
                status=ItemStatus.COMPLETED,
                created_at=2.0,
                updated_at=2.0,
                content={"text": "late"},
            )
        )
        seen = [item["seq"] for item in first["items"]]
        cursor = first["next_cursor"]
        while cursor:
            page = service.list_items(THREAD, limit=10, cursor=cursor)
            seen.extend(item["seq"] for item in page["items"])
            cursor = page["next_cursor"]
        assert seen == list(range(1, 32))

    def test_descending_order_pages_backwards(self, store, service):
        seed_items(store, 25)
        seen = []
        cursor = None
        while True:
            page = service.list_items(THREAD, limit=10, cursor=cursor, descending=True)
            seen.extend(item["seq"] for item in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        assert seen == list(range(25, 0, -1))

    def test_turn_scoping_and_totals(self, store, service):
        seed_items(store, 10, turn_id="run1")
        store.upsert_item(
            Item(
                item_id="itm_99",
                thread_id=THREAD,
                turn_id="run2",
                seq=99,
                kind=ItemKind.AGENT_MESSAGE,
                status=ItemStatus.COMPLETED,
                created_at=1.0,
                updated_at=1.0,
                content={"text": "other turn"},
            )
        )
        page = service.list_items(THREAD, turn_id="run2")
        assert [item["seq"] for item in page["items"]] == [99]
        assert page["total"] == 1

    def test_kind_filtering(self, store, service):
        seed_items(store, 10)
        page = service.list_items(THREAD, kinds=["tool_call"])
        assert {item["kind"] for item in page["items"]} == {"tool_call"}

    def test_total_is_counted_under_the_same_filter_as_the_page(self, store, service):
        """REGRESSION: `total` ignored `kinds`, reporting 21 next to one
        returned item."""
        seed_items(store, 10)
        page = service.list_items(THREAD, kinds=["tool_call"])
        assert page["total"] == len(page["items"]) == 5

    def test_total_is_counted_under_the_turn_filter_too(self, store, service):
        seed_items(store, 10, turn_id="run1")
        page = service.list_items(THREAD, turn_id="run1")
        assert page["total"] == 10

    def test_clamping_is_a_contract_not_a_surprise(self, store, service):
        seed_items(store, 10)
        for limit in (0, -5):
            page = service.list_items(THREAD, limit=limit)
            assert len(page["items"]) == 1
            assert page["has_more"] is True

    def test_an_entirely_unknown_kind_filter_returns_an_empty_page(
        self, store, service
    ):
        seed_items(store, 10)
        page = service.list_items(THREAD, kinds=["not_a_kind"])
        assert page["items"] == []
        assert page["has_more"] is False

    def test_an_unknown_kind_alongside_a_known_one_still_filters(self, store, service):
        seed_items(store, 10)
        page = service.list_items(THREAD, kinds=["not_a_kind", "tool_call"])
        assert {item["kind"] for item in page["items"]} == {"tool_call"}

    def test_turn_pagination(self, store):
        recorder = ConversationRecorder(store)
        recorder.ensure_thread(THREAD)
        for index in range(12):
            recorder.record(
                THREAD,
                "run_started",
                {"run_id": f"run{index}", "session_id": THREAD, "prompt": "x"},
            )
        service = ConversationService(store=store)
        seen = []
        cursor = None
        while True:
            page = service.list_turns(THREAD, limit=5, cursor=cursor)
            seen.extend(turn["ordinal"] for turn in page["turns"])
            assert page["total"] == 12
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        assert seen == list(range(1, 13))


class TestErrors:
    def test_unknown_thread_raises_a_typed_error(self, service):
        with pytest.raises(ThreadNotFound) as exc:
            service.get_thread("nope")
        assert exc.value.code == -32080
        assert exc.value.data == {"kind": "thread_not_found", "thread_id": "nope"}

    def test_unknown_item_raises_a_typed_error(self, store, service):
        seed_items(store, 1)
        with pytest.raises(ItemNotFound) as exc:
            service.get_item(THREAD, "itm_missing")
        assert exc.value.code == -32081
        assert exc.value.data["kind"] == "item_not_found"

    def test_malformed_cursor_raises_a_typed_error(self, store, service):
        seed_items(store, 5)
        with pytest.raises(InvalidCursor) as exc:
            service.list_items(THREAD, cursor="!!!not-a-cursor!!!")
        assert exc.value.code == -32082
        assert exc.value.data["kind"] == "cursor_invalid"

    def test_a_cursor_without_the_expected_key_is_rejected(self, store, service):
        from omniagents.core.conversation.model import encode_cursor

        seed_items(store, 5)
        with pytest.raises(InvalidCursor):
            service.list_items(THREAD, cursor=encode_cursor({"offset": 3}))

    def test_a_negative_cursor_position_is_rejected(self, store, service):
        from omniagents.core.conversation.model import encode_cursor

        seed_items(store, 5)
        with pytest.raises(InvalidCursor):
            service.list_items(THREAD, cursor=encode_cursor({"seq": -10}))

    def test_a_boolean_cursor_position_is_rejected(self, store, service):
        """``bool`` is an ``int`` subclass; it is not a valid position."""
        from omniagents.core.conversation.model import encode_cursor

        seed_items(store, 5)
        with pytest.raises(InvalidCursor):
            service.list_items(THREAD, cursor=encode_cursor({"seq": True}))

    def test_a_cursor_past_the_end_returns_an_empty_final_page(self, store, service):
        from omniagents.core.conversation.model import encode_cursor

        seed_items(store, 5)
        page = service.list_items(THREAD, cursor=encode_cursor({"seq": 9999}))
        assert page["items"] == []
        assert page["has_more"] is False

    def test_error_codes_do_not_collide_with_reserved_ranges(self):
        taken = set(range(-32013, -32009))
        taken |= {-32020, -32030}
        taken |= set(range(-32042, -32039))
        taken |= set(range(-32052, -32049))
        taken |= set(range(-32063, -32059))
        taken |= set(range(-32076, -32069))
        assert {-32080, -32081, -32082}.isdisjoint(taken)


class TestSingleItemRead:
    def test_reads_one_item_by_canonical_id(self, store, service):
        seed_items(store, 3)
        item = service.get_item(THREAD, "itm_2")
        assert item["item_id"] == "itm_2"
        assert item["seq"] == 2


class TestLazyMigration:
    """A session written before this feature existed must stay readable.

    ``append_message`` writes the same legacy rows the runtime does; the
    service must migrate them on first read and then serve from the store.
    """

    def test_reads_a_thread_that_has_no_domain_rows(self, tmp_path, store):
        register_session(THREAD)
        for message in [
            {"role": "user", "content": "add a test"},
            {
                "type": "function_call",
                "name": "read_file",
                "call_id": "c1",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "c1", "output": "def f(): ..."},
            {
                "role": "assistant",
                "type": "message",
                "content": [{"type": "output_text", "text": "done", "annotations": []}],
            },
        ]:
            append_message(THREAD, message)

        service = ConversationService(store=store)
        thread = service.get_thread(THREAD)
        assert thread["source"] == "adapter"
        assert thread["item_count"] == 3

        items = service.list_items(THREAD)["items"]
        assert [item["kind"] for item in items] == [
            "user_message",
            "tool_call",
            "agent_message",
        ]
        assert items[1]["content"]["output"] == "def f(): ..."
        assert all(item["status"] == "completed" for item in items)

    def test_migration_runs_once_and_is_stable(self, store):
        register_session(THREAD)
        append_message(THREAD, {"role": "user", "content": "hi"})
        service = ConversationService(store=store)
        first = service.list_items(THREAD)["items"]
        second = service.list_items(THREAD)["items"]
        assert first == second
        assert store.count_items(THREAD) == 1

    def test_resuming_a_legacy_session_keeps_its_prior_transcript(self, store):
        """REGRESSION: resuming a pre-existing session used to erase it.

        The recorder claims a thread on the first notification of the new
        run. When projection was a read-time fallback keyed on
        ``source == "recorded"``, that claim locked the adapter out forever
        and the entire prior transcript vanished from the read surface.
        Projection is now a precondition of the thread row existing.
        """
        register_session(THREAD)
        for message in [
            {"role": "user", "content": "old question"},
            {
                "type": "function_call",
                "name": "read_file",
                "call_id": "old1",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "old1",
                "output": "old contents",
            },
            {"role": "assistant", "type": "message", "content": "old answer"},
        ]:
            append_message(THREAD, message)

        recorder = ConversationRecorder(store)
        for method, payload in [
            (
                "run_started",
                {"run_id": "resumed", "prompt": "new question", "prompt_role": "user"},
            ),
            ("message_output", {"run_id": "resumed", "content": "new answer"}),
            ("run_end", {"run_id": "resumed", "end_reason": "completed"}),
        ]:
            recorder.record(THREAD, method, {"session_id": THREAD, **payload})

        service = ConversationService(store=store)
        items = service.list_items(THREAD, limit=100)["items"]
        texts = [
            item["content"].get("text") or item["content"].get("tool") for item in items
        ]
        assert "old question" in texts
        assert "read_file" in texts
        assert "old answer" in texts
        assert "new question" in texts
        assert "new answer" in texts
        assert [item["seq"] for item in items] == list(range(1, len(items) + 1))

    def test_reading_then_writing_destroys_nothing(self, store):
        """REGRESSION: a read used to roll the ordinal cursor backwards.

        ``ensure_thread`` re-projected on every read and ``write_projection``
        rewrote the whole ``threads`` row including ``last_seq``. The next
        recorder write then allocated an already-used ordinal and the
        item holding it was silently deleted. Turn 1 must survive turn 2.
        """
        register_session(THREAD)
        append_message(THREAD, {"role": "user", "content": "legacy opener"})

        service = ConversationService(store=store)
        recorder = ConversationRecorder(store)

        def run(run_id, prompt):
            for method, payload in [
                (
                    "run_started",
                    {"run_id": run_id, "prompt": prompt, "prompt_role": "user"},
                ),
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
                        "output": "x",
                        "call_id": f"{run_id}-c1",
                    },
                ),
                (
                    "message_output",
                    {"run_id": run_id, "content": f"reply for {run_id}"},
                ),
                ("run_end", {"run_id": run_id, "end_reason": "completed"}),
            ]:
                recorder.record(THREAD, method, {"session_id": THREAD, **payload})

        service.list_items(THREAD)
        run("turn1", "first")
        service.list_items(THREAD)
        service.get_thread(THREAD)
        run("turn2", "second")
        service.list_items(THREAD)

        items = service.list_items(THREAD, limit=100)["items"]
        seqs = [item["seq"] for item in items]
        assert len(seqs) == len(set(seqs)), f"duplicate ordinals: {seqs}"
        assert seqs == sorted(seqs)
        texts = [
            item["content"].get("text") or item["content"].get("tool") for item in items
        ]
        assert "legacy opener" in texts
        assert "first" in texts and "reply for turn1" in texts
        assert "second" in texts and "reply for turn2" in texts
        assert texts.count("read_file") == 2

    def test_a_read_never_re_projects_an_existing_thread(self, store):
        """Reads are pure: repeated reads must not rewrite rows."""
        register_session(THREAD)
        append_message(THREAD, {"role": "user", "content": "hi"})
        service = ConversationService(store=store)

        calls = {"n": 0}
        original = store.write_projection

        def counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        store.write_projection = counting  # type: ignore[method-assign]
        service.get_thread(THREAD)
        service.get_thread(THREAD)
        service.list_items(THREAD)
        service.list_items(THREAD)
        service.list_turns(THREAD)
        assert calls["n"] == 1, "projection must happen exactly once"

    def test_the_ordinal_cursor_never_moves_backwards(self, store):
        """A stale in-memory Thread must not roll ``last_seq`` back."""
        store.upsert_thread(Thread(thread_id=THREAD, created_at=1.0, updated_at=1.0))
        assert store.allocate_seq(THREAD) == 1
        assert store.allocate_seq(THREAD) == 2
        stale = store.get_thread(THREAD)
        stale.last_seq = 0
        store.upsert_thread(stale)
        assert store.get_thread(THREAD).last_seq == 2
        assert store.allocate_seq(THREAD) == 3

    def test_a_thread_with_neither_domain_rows_nor_history_is_not_found(self, store):
        service = ConversationService(store=store)
        with pytest.raises(ThreadNotFound):
            service.list_items("never-existed")
