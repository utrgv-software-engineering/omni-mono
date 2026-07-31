"""Store contract: persistence, ordinal allocation, keyset queries.

Every test here runs against the SQLite backend. The Postgres backend is
exercised by the SAME contract suite in ``test_postgres_store.py``, which
imports these cases and re-runs them against a live database.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from omniagents.core.conversation.model import (
    Item,
    ItemKind,
    ItemStatus,
    Thread,
    ThreadStatus,
    Turn,
    TurnStatus,
)
from omniagents.core.conversation.store import SqliteConversationStore


@pytest.fixture
def store(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    yield backend
    backend.close()


def make_thread(thread_id="t1", **overrides):
    base = dict(thread_id=thread_id, created_at=1.0, updated_at=1.0)
    base.update(overrides)
    return Thread(**base)


def make_turn(thread_id="t1", turn_id="run1", ordinal=1, **overrides):
    base = dict(
        thread_id=thread_id,
        turn_id=turn_id,
        ordinal=ordinal,
        created_at=1.0,
        updated_at=1.0,
    )
    base.update(overrides)
    return Turn(**base)


def make_item(seq, thread_id="t1", turn_id="run1", **overrides):
    base = dict(
        item_id=f"itm_{seq}",
        thread_id=thread_id,
        turn_id=turn_id,
        seq=seq,
        kind=ItemKind.AGENT_MESSAGE,
        status=ItemStatus.COMPLETED,
        created_at=1.0,
        updated_at=1.0,
        content={"text": f"m{seq}"},
    )
    base.update(overrides)
    return Item(**base)


class TestThreads:
    def test_roundtrip_preserves_every_field(self, store):
        thread = make_thread(
            user_id="u1",
            status=ThreadStatus.ARCHIVED,
            title="A title",
            last_seq=7,
            turn_count=2,
            item_count=9,
            parent_thread_id="t0",
            branched_from_item_id="itm_parent",
            usage={"input_tokens": 11},
            compaction={"checkpoint_seq": 4},
            source="adapter",
        )
        store.upsert_thread(thread)
        loaded = store.get_thread("t1")
        assert loaded is not None
        assert loaded.to_dict() == thread.to_dict()

    def test_missing_thread_is_none(self, store):
        assert store.get_thread("nope") is None

    def test_delete_removes_turns_and_items(self, store):
        store.upsert_thread(make_thread())
        store.upsert_turn(make_turn())
        store.upsert_item(make_item(1))
        assert store.delete_thread("t1") is True
        assert store.get_thread("t1") is None
        assert store.list_turns("t1", limit=10) == []
        assert store.list_items("t1", limit=10) == []

    def test_delete_reports_a_missing_thread(self, store):
        assert store.delete_thread("nope") is False


class TestSeqAllocation:
    def test_allocation_is_dense_and_increasing(self, store):
        store.upsert_thread(make_thread())
        assert [store.allocate_seq("t1") for _ in range(3)] == [1, 2, 3]

    def test_bulk_allocation_returns_the_first_reserved_ordinal(self, store):
        store.upsert_thread(make_thread())
        assert store.allocate_seq("t1", 5) == 1
        assert store.allocate_seq("t1") == 6

    def test_allocation_on_an_unknown_thread_raises(self, store):
        with pytest.raises(KeyError):
            store.allocate_seq("nope")

    def test_concurrent_allocation_never_hands_out_a_duplicate(self, store):
        """Two tool completions landing at once must not collide: the
        unique (thread_id, seq) index would reject one and lose an item."""
        store.upsert_thread(make_thread())
        with ThreadPoolExecutor(max_workers=8) as pool:
            allocated = list(pool.map(lambda _: store.allocate_seq("t1"), range(200)))
        assert sorted(allocated) == list(range(1, 201))

    def test_rejects_non_positive_counts(self, store):
        store.upsert_thread(make_thread())
        with pytest.raises(ValueError):
            store.allocate_seq("t1", 0)


class TestTurns:
    def test_roundtrip_preserves_every_field(self, store):
        turn = make_turn(
            status=TurnStatus.FAILED,
            completed_at=9.0,
            prompt="do it",
            prompt_role="user",
            end_reason="error",
            error={"message": "boom", "type": "RuntimeError"},
            usage={"input_tokens": 3},
            model="gpt-x",
            model_ref="catalog/gpt-x",
            item_count=4,
            first_seq=1,
            last_seq=4,
            attempts=2,
            source="adapter",
        )
        store.upsert_turn(turn)
        loaded = store.get_turn("t1", "run1")
        assert loaded is not None
        assert loaded.to_dict() == turn.to_dict()

    def test_null_error_stays_null(self, store):
        store.upsert_turn(make_turn())
        assert store.get_turn("t1", "run1").error is None

    def test_ordering_counting_and_max_ordinal(self, store):
        for ordinal in range(1, 6):
            store.upsert_turn(make_turn(turn_id=f"run{ordinal}", ordinal=ordinal))
        assert [t.ordinal for t in store.list_turns("t1", limit=10)] == [1, 2, 3, 4, 5]
        assert store.count_turns("t1") == 5
        assert store.max_turn_ordinal("t1") == 5

    def test_max_ordinal_of_an_empty_thread_is_zero(self, store):
        assert store.max_turn_ordinal("t1") == 0

    def test_keyset_paging_forward_and_backward(self, store):
        for ordinal in range(1, 6):
            store.upsert_turn(make_turn(turn_id=f"run{ordinal}", ordinal=ordinal))
        assert [
            t.ordinal for t in store.list_turns("t1", limit=2, after_ordinal=2)
        ] == [
            3,
            4,
        ]
        assert [
            t.ordinal
            for t in store.list_turns("t1", limit=2, before_ordinal=4, descending=True)
        ] == [3, 2]


class TestItems:
    def test_roundtrip_preserves_every_field(self, store):
        item = make_item(
            1,
            kind=ItemKind.TOOL_CALL,
            status=ItemStatus.FAILED,
            role="assistant",
            completed_at=5.0,
            revision=3,
            content={"tool": "execute_bash", "output": "err"},
            source_ref={"call_id": "c1"},
            source="adapter",
        )
        store.upsert_item(item)
        loaded = store.get_item("t1", "itm_1")
        assert loaded is not None
        assert loaded.to_dict() == item.to_dict()

    def test_upsert_replaces_in_place(self, store):
        store.upsert_item(make_item(1))
        store.upsert_item(make_item(1, content={"text": "changed"}, revision=1))
        assert store.count_items("t1") == 1
        assert store.get_item("t1", "itm_1").content == {"text": "changed"}

    def test_listing_is_ordered_by_seq(self, store):
        for seq in (3, 1, 2):
            store.upsert_item(make_item(seq))
        assert [i.seq for i in store.list_items("t1", limit=10)] == [1, 2, 3]

    def test_descending_listing(self, store):
        for seq in range(1, 4):
            store.upsert_item(make_item(seq))
        assert [i.seq for i in store.list_items("t1", limit=10, descending=True)] == [
            3,
            2,
            1,
        ]

    def test_turn_scoping(self, store):
        store.upsert_item(make_item(1, turn_id="run1"))
        store.upsert_item(make_item(2, turn_id="run2"))
        assert [i.seq for i in store.list_items("t1", limit=10, turn_id="run2")] == [2]
        assert store.count_items("t1", turn_id="run2") == 1

    def test_kind_filtering(self, store):
        store.upsert_item(make_item(1, kind=ItemKind.USER_MESSAGE))
        store.upsert_item(make_item(2, kind=ItemKind.TOOL_CALL))
        store.upsert_item(make_item(3, kind=ItemKind.ARTIFACT))
        filtered = store.list_items(
            "t1", limit=10, kinds=[ItemKind.TOOL_CALL, ItemKind.ARTIFACT]
        )
        assert [i.seq for i in filtered] == [2, 3]

    def test_keyset_paging_walks_the_whole_thread_exactly_once(self, store):
        for seq in range(1, 51):
            store.upsert_item(make_item(seq))
        seen = []
        after = None
        while True:
            page = store.list_items("t1", limit=7, after_seq=after)
            if not page:
                break
            seen.extend(i.seq for i in page)
            after = page[-1].seq
        assert seen == list(range(1, 51))

    def test_open_items_returns_only_non_terminal_items(self, store):
        store.upsert_item(make_item(1, status=ItemStatus.STARTED))
        store.upsert_item(make_item(2, status=ItemStatus.COMPLETED))
        store.upsert_item(make_item(3, status=ItemStatus.FAILED))
        store.upsert_item(make_item(4, status=ItemStatus.CANCELLED))
        store.upsert_item(make_item(5, status=ItemStatus.STARTED, turn_id="run2"))
        assert [i.seq for i in store.open_items("t1")] == [1, 5]
        assert [i.seq for i in store.open_items("t1", turn_id="run1")] == [1]


class TestProjectionWrite:
    def test_writes_thread_turns_and_items_together(self, store):
        thread = make_thread(source="adapter", item_count=2, turn_count=1)
        turns = [make_turn(source="adapter")]
        items = [make_item(1, source="adapter"), make_item(2, source="adapter")]
        store.write_projection(thread, turns, items)
        assert store.get_thread("t1").source == "adapter"
        assert store.count_turns("t1") == 1
        assert store.count_items("t1") == 2

    def test_a_failing_projection_leaves_no_partial_rows(self, store):
        """A half-written projection would look like a partially recorded
        thread and would never be retried — atomicity is correctness."""

        class Exploding(list):
            def __iter__(self):
                yield make_item(1)
                raise RuntimeError("storage fault")

        with pytest.raises(RuntimeError):
            store.write_projection(make_thread(), [make_turn()], Exploding())
        assert store.get_thread("t1") is None
        assert store.count_items("t1") == 0
