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
    ThreadFilter,
    ThreadStatus,
    Turn,
    TurnStatus,
)
from omniagents.core.conversation.store import (
    SearchUnavailable,
    SqliteConversationStore,
)


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


# =====================================================================
# Organization: listing, search and ancestry (tkt_eZldW-Gje6kn)
#
# These classes are re-run against Postgres by ``test_postgres_store``.
# Every behaviour asserted here is a behaviour BOTH backends must have —
# a search that works on SQLite and not on PG fails there, not here.
# =====================================================================


def seed_threads(store, count, *, user_id="u1", prefix="th", **overrides):
    """*count* threads with distinct, increasing ``updated_at`` values."""
    for index in range(count):
        base = dict(
            thread_id=f"{prefix}-{index:04d}",
            user_id=user_id,
            title=f"Conversation {index}",
            created_at=1000.0 + index,
            updated_at=1000.0 + index,
        )
        base.update(overrides)
        store.upsert_thread(Thread(**base))


class TestThreadListing:
    def test_a_large_history_is_walked_by_cursor_exactly_once(self, store):
        """The headline acceptance criterion, at a size that would break a
        naive offset pager: 400 threads, paged 37 at a time."""
        seed_threads(store, 400)
        seen = []
        after = None
        while True:
            page = store.list_threads(ThreadFilter(user_id="u1"), limit=37, after=after)
            if not page:
                break
            seen.extend(thread.thread_id for thread in page)
            after = (page[-1].updated_at, page[-1].thread_id)
        assert len(seen) == 400
        assert len(set(seen)) == 400, "a page boundary skipped or repeated rows"
        assert seen == sorted(seen, reverse=True)
        assert store.count_threads(ThreadFilter(user_id="u1")) == 400

    def test_the_keyset_is_total_when_timestamps_collide(self, store):
        """Every projected thread carries ``updated_at: 0.0``. A keyset on
        the timestamp alone would loop or skip; the id tiebreaker is what
        makes paging them sound."""
        seed_threads(store, 60, updated_at=0.0, created_at=0.0)
        seen = []
        after = None
        for _ in range(20):
            page = store.list_threads(ThreadFilter(user_id="u1"), limit=9, after=after)
            if not page:
                break
            seen.extend(thread.thread_id for thread in page)
            after = (page[-1].updated_at, page[-1].thread_id)
        assert len(seen) == 60
        assert len(set(seen)) == 60

    def test_ascending_order_walks_the_same_set(self, store):
        seed_threads(store, 30)
        seen = []
        after = None
        while True:
            page = store.list_threads(
                ThreadFilter(user_id="u1"), limit=8, after=after, descending=False
            )
            if not page:
                break
            seen.extend(thread.thread_id for thread in page)
            after = (page[-1].updated_at, page[-1].thread_id)
        assert seen == sorted(seen)
        assert len(seen) == 30

    def test_ownership_scope_is_applied_with_the_limit_not_after_it(self, store):
        """The listing authorization trap: 40 rows belong to someone else
        and sort NEWEST, so a page-then-filter implementation returns an
        empty first page while claiming there is more."""
        seed_threads(store, 10, user_id="u1", prefix="mine")
        seed_threads(store, 40, user_id="u2", prefix="theirs", updated_at=9000.0)
        page = store.list_threads(ThreadFilter(user_id="u1"), limit=10)
        assert len(page) == 10
        assert {thread.user_id for thread in page} == {"u1"}
        assert store.count_threads(ThreadFilter(user_id="u1")) == 10

    def test_an_unowned_thread_is_invisible_to_a_named_principal(self, store):
        store.upsert_thread(make_thread(thread_id="orphan", user_id=None))
        seed_threads(store, 2, user_id="u1")
        assert [
            t.thread_id
            for t in store.list_threads(ThreadFilter(user_id="u1"), limit=10)
        ] == ["th-0001", "th-0000"]
        assert store.count_threads(ThreadFilter(user_id="u1")) == 2

    def test_status_and_pin_filters(self, store):
        seed_threads(store, 3, user_id="u1", prefix="act")
        seed_threads(store, 2, user_id="u1", prefix="arc", status=ThreadStatus.ARCHIVED)
        seed_threads(store, 4, user_id="u1", prefix="pin", pinned=True)
        assert (
            store.count_threads(
                ThreadFilter(user_id="u1", status=ThreadStatus.ARCHIVED)
            )
            == 2
        )
        assert (
            store.count_threads(ThreadFilter(user_id="u1", status=ThreadStatus.ACTIVE))
            == 7
        )
        assert store.count_threads(ThreadFilter(user_id="u1", pinned=True)) == 4
        assert store.count_threads(ThreadFilter(user_id="u1", pinned=False)) == 5

    def test_source_and_parent_filters(self, store):
        seed_threads(store, 2, user_id="u1", prefix="rec")
        seed_threads(store, 3, user_id="u1", prefix="adp", source="adapter")
        seed_threads(store, 2, user_id="u1", prefix="kid", parent_thread_id="rec-0000")
        assert store.count_threads(ThreadFilter(user_id="u1", source="adapter")) == 3
        assert (
            store.count_threads(ThreadFilter(user_id="u1", parent_thread_id="rec-0000"))
            == 2
        )

    def test_date_bounds_are_half_open(self, store):
        seed_threads(store, 5, user_id="u1")  # created_at 1000..1004
        assert (
            store.count_threads(ThreadFilter(user_id="u1", created_after=1002.0)) == 3
        )
        assert (
            store.count_threads(ThreadFilter(user_id="u1", created_before=1002.0)) == 2
        )
        assert (
            store.count_threads(
                ThreadFilter(user_id="u1", updated_after=1001.0, updated_before=1004.0)
            )
            == 3
        )

    def test_model_filter_matches_through_the_thread_s_turns(self, store):
        seed_threads(store, 3, user_id="u1")
        store.upsert_turn(make_turn(thread_id="th-0000", model="gpt-a"))
        store.upsert_turn(make_turn(thread_id="th-0001", model="gpt-b"))
        store.upsert_turn(make_turn(thread_id="th-0002", turn_id="r1", model="gpt-a"))
        store.upsert_turn(make_turn(thread_id="th-0002", turn_id="r2", model="gpt-b"))
        assert {
            t.thread_id
            for t in store.list_threads(
                ThreadFilter(user_id="u1", model="gpt-a"), limit=10
            )
        } == {"th-0000", "th-0002"}
        assert store.count_threads(ThreadFilter(user_id="u1", model="gpt-zz")) == 0

    def test_pinned_and_metadata_round_trip(self, store):
        store.upsert_thread(
            make_thread(pinned=True, metadata={"label": "release", "n": 3})
        )
        loaded = store.get_thread("t1")
        assert loaded.pinned is True
        assert loaded.metadata == {"label": "release", "n": 3}


class TestThreadSearch:
    def test_search_finds_items_and_reports_a_preview(self, store):
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="Alpha"))
        store.upsert_item(
            make_item(1, content={"text": "we rewrote the tokenizer to stream"})
        )
        store.upsert_item(make_item(2, content={"text": "unrelated chatter"}))
        hits, total = store.search("tokenizer", ThreadFilter(user_id="u1"), limit=10)
        assert total == 1
        assert len(hits) == 1
        assert hits[0].thread_id == "t1"
        assert hits[0].item_id == "itm_1"
        assert hits[0].seq == 1
        assert hits[0].match_kind == ItemKind.AGENT_MESSAGE.value
        assert "tokenizer" in hits[0].preview

    def test_search_matches_a_thread_title(self, store):
        store.upsert_thread(
            make_thread(thread_id="t1", user_id="u1", title="Sprocket redesign")
        )
        hits, total = store.search("sprocket", ThreadFilter(user_id="u1"), limit=10)
        assert total == 1
        assert hits[0].match_kind == "thread_title"
        assert hits[0].item_id is None
        assert "Sprocket" in hits[0].preview

    def test_search_indexes_tool_content_not_only_messages(self, store):
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        store.upsert_item(
            make_item(
                1,
                kind=ItemKind.TOOL_CALL,
                content={
                    "tool": "read_file",
                    "input": '{"path":"widgets.py"}',
                    "output": "def frobnicate(): ...",
                },
            )
        )
        for term in ("read_file", "widgets", "frobnicate"):
            hits, total = store.search(term, ThreadFilter(user_id="u1"), limit=10)
            assert total == 1, f"{term!r} did not match indexed tool content"
            assert hits[0].match_kind == ItemKind.TOOL_CALL.value

    def test_multiple_terms_are_anded(self, store):
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        store.upsert_thread(make_thread(thread_id="t2", user_id="u1", title="y"))
        store.upsert_item(make_item(1, thread_id="t1", content={"text": "alpha beta"}))
        store.upsert_item(
            make_item(
                1, thread_id="t2", item_id="itm_2", content={"text": "alpha only"}
            )
        )
        assert store.search("alpha", ThreadFilter(user_id="u1"), limit=10)[1] == 2
        assert store.search("alpha beta", ThreadFilter(user_id="u1"), limit=10)[1] == 1
        assert (
            store.search("alpha missingterm", ThreadFilter(user_id="u1"), limit=10)[1]
            == 0
        )

    def test_a_punctuated_token_is_searchable_by_its_parts(self, store):
        """The cross-backend tokenization contract.

        Postgres keeps ``widgets.py`` as one ``file`` lexeme while SQLite's
        tokenizer splits on every non-alphanumeric, so a query for
        ``widgets`` matched on one backend and missed on the other until
        both the query split and the PG tsvector were normalized. This
        pins that: identical answers, on either store.
        """
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        store.upsert_item(
            make_item(
                1,
                content={
                    "text": "edited widgets.py and src/core/thing_two.rs for "
                    "user@example.com"
                },
            )
        )
        scope = ThreadFilter(user_id="u1")
        for query in (
            "widgets",
            "widgets.py",
            "py",
            "thing_two",
            "thing",
            "example.com",
            "user@example.com",
            "src/core",
        ):
            assert store.search(query, scope, limit=5)[1] == 1, query
        assert store.search("widgets nonexistent", scope, limit=5)[1] == 0

    def test_terms_are_anded_not_matched_as_a_phrase(self, store):
        """NON-adjacent terms, so AND and phrase-match give different
        answers and the test can tell them apart."""
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        store.upsert_thread(make_thread(thread_id="t2", user_id="u1", title="y"))
        store.upsert_item(
            make_item(1, thread_id="t1", content={"text": "alpha in between beta"})
        )
        store.upsert_item(
            make_item(
                1,
                thread_id="t2",
                item_id="itm_2",
                content={"text": "alpha without the other one"},
            )
        )
        scope = ThreadFilter(user_id="u1")
        # Both terms present but not adjacent -> matches under AND, would
        # NOT match as the phrase "alpha beta".
        assert store.search("alpha beta", scope, limit=10)[1] == 1
        # Order must not matter either.
        assert store.search("beta alpha", scope, limit=10)[1] == 1
        assert store.search("alpha", scope, limit=10)[1] == 2

    def test_an_elicitation_answer_is_never_indexed_or_previewed(self, store):
        """A session-scoped MCP server can raise "paste your API key" as a
        form elicitation. The prompt is findable; the answer is not."""
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        store.upsert_item(
            make_item(
                1,
                kind=ItemKind.ELICITATION,
                content={
                    "message": "Paste your Acme API key",
                    "value": {"api_key": "hunter2-mcp-api-key-ABCDEF"},
                    "action": "accept",
                },
            )
        )
        scope = ThreadFilter(user_id="u1")
        hits, total = store.search("Acme", scope, limit=10)
        assert total == 1, "the elicitation PROMPT should stay findable"
        assert "hunter2" not in hits[0].preview
        assert "api_key" not in hits[0].preview
        for secret in ("hunter2", "ABCDEF", "hunter2-mcp-api-key-ABCDEF"):
            assert store.search(secret, scope, limit=10)[1] == 0, secret

    def test_a_query_with_backend_operators_does_not_raise(self, store):
        """User input never reaches the FTS5 / tsquery parser."""
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        store.upsert_item(make_item(1, content={"text": "alpha"}))
        for hostile in ('bad" query*', "a & b | !c", "NEAR(x y)", "'", '"""', "*"):
            hits, total = store.search(hostile, ThreadFilter(user_id="u1"), limit=10)
            assert isinstance(total, int)
        assert store.search("   ", ThreadFilter(user_id="u1"), limit=10) == ([], 0)

    def test_search_is_scoped_to_the_owner_in_sql(self, store):
        store.upsert_thread(make_thread(thread_id="mine", user_id="u1", title="x"))
        store.upsert_thread(make_thread(thread_id="theirs", user_id="u2", title="x"))
        store.upsert_item(
            make_item(1, thread_id="mine", content={"text": "shared secret word"})
        )
        store.upsert_item(
            make_item(
                1,
                thread_id="theirs",
                item_id="itm_other",
                content={"text": "shared secret word"},
            )
        )
        hits, total = store.search("secret", ThreadFilter(user_id="u1"), limit=10)
        assert total == 1
        assert [hit.thread_id for hit in hits] == ["mine"]
        # …and the preview of the other principal's item is never produced.
        assert all("theirs" != hit.thread_id for hit in hits)

    def test_search_honours_the_same_filters_as_the_listing(self, store):
        store.upsert_thread(
            make_thread(thread_id="a", user_id="u1", title="x", pinned=True)
        )
        store.upsert_thread(
            make_thread(
                thread_id="b", user_id="u1", title="x", status=ThreadStatus.ARCHIVED
            )
        )
        for thread_id in ("a", "b"):
            store.upsert_item(
                make_item(
                    1,
                    thread_id=thread_id,
                    item_id=f"itm_{thread_id}",
                    content={"text": "quokka"},
                )
            )
        assert store.search("quokka", ThreadFilter(user_id="u1"), limit=10)[1] == 2
        assert (
            store.search("quokka", ThreadFilter(user_id="u1", pinned=True), limit=10)[1]
            == 1
        )
        assert (
            store.search(
                "quokka",
                ThreadFilter(user_id="u1", status=ThreadStatus.ARCHIVED),
                limit=10,
            )[1]
            == 1
        )

    def test_a_revised_item_is_reindexed(self, store):
        """The index is maintained on the write path, not built once."""
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        store.upsert_item(make_item(1, content={"text": "kumquat"}))
        assert store.search("kumquat", ThreadFilter(user_id="u1"), limit=10)[1] == 1
        store.upsert_item(make_item(1, content={"text": "rambutan"}, revision=1))
        assert store.search("kumquat", ThreadFilter(user_id="u1"), limit=10)[1] == 0
        assert store.search("rambutan", ThreadFilter(user_id="u1"), limit=10)[1] == 1

    def test_a_renamed_thread_is_reindexed(self, store):
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="kumquat"))
        assert store.search("kumquat", ThreadFilter(user_id="u1"), limit=10)[1] == 1
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="rambutan"))
        assert store.search("kumquat", ThreadFilter(user_id="u1"), limit=10)[1] == 0
        assert store.search("rambutan", ThreadFilter(user_id="u1"), limit=10)[1] == 1

    def test_deleting_a_thread_removes_it_from_the_index(self, store):
        """A search index that outlived the transcript would keep serving
        previews of deleted content — the same retention defect as leaving
        the rows behind."""
        store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="kumquat"))
        store.upsert_item(make_item(1, content={"text": "rambutan"}))
        assert store.search("rambutan", ThreadFilter(user_id="u1"), limit=10)[1] == 1
        store.delete_thread("t1")
        assert store.search("rambutan", ThreadFilter(user_id="u1"), limit=10) == ([], 0)
        assert store.search("kumquat", ThreadFilter(user_id="u1"), limit=10) == ([], 0)

    def test_bulk_projection_writes_are_indexed(self, store):
        """``write_projection`` is a separate write path; the index has to
        cover it too or a migrated thread is unsearchable."""
        thread = make_thread(thread_id="t1", user_id="u1", title="x", source="adapter")
        items = [
            make_item(1, content={"text": "projected marmoset"}, source="adapter"),
            make_item(2, item_id="itm_2", content={"text": "other"}, source="adapter"),
        ]
        store.write_projection(thread, [make_turn(source="adapter")], items)
        hits, total = store.search("marmoset", ThreadFilter(user_id="u1"), limit=10)
        assert total == 1
        assert hits[0].item_id == "itm_1"

    def test_match_count_and_offset_paging(self, store):
        for index in range(12):
            store.upsert_thread(
                make_thread(
                    thread_id=f"t{index:02d}", user_id="u1", title=f"title {index}"
                )
            )
            for item in range(index + 1):
                store.upsert_item(
                    make_item(
                        item + 1,
                        thread_id=f"t{index:02d}",
                        item_id=f"itm_{index}_{item}",
                        content={"text": "capybara sighting"},
                    )
                )
        first, total = store.search("capybara", ThreadFilter(user_id="u1"), limit=5)
        assert total == 12
        assert len(first) == 5
        second, _ = store.search(
            "capybara", ThreadFilter(user_id="u1"), limit=5, offset=5
        )
        assert len(second) == 5
        assert not ({h.thread_id for h in first} & {h.thread_id for h in second})
        counts = {hit.thread_id: hit.match_count for hit in first + second}
        # Every thread in the two pages reports its OWN number of matching
        # items: thread tNN was seeded with NN+1 of them.
        assert len(counts) == 10
        for thread_id, count in counts.items():
            assert count == int(thread_id[1:]) + 1, (thread_id, count)


class TestPageCounts:
    def test_counts_are_exact_even_when_the_stored_counter_drifted(self, store):
        """The stored counters are non-transactional read-modify-write, and
        a listing is the surface a client sizes its UI from."""
        store.upsert_thread(
            make_thread(thread_id="t1", user_id="u1", item_count=999, turn_count=42)
        )
        store.upsert_thread(make_thread(thread_id="t2", user_id="u1"))
        store.upsert_turn(make_turn(thread_id="t1", turn_id="r1"))
        store.upsert_turn(make_turn(thread_id="t1", turn_id="r2"))
        for seq in (1, 2, 3):
            store.upsert_item(make_item(seq, thread_id="t1", item_id=f"itm_1_{seq}"))
        assert store.count_items_for_threads(["t1", "t2"]) == {"t1": 3, "t2": 0}
        assert store.count_turns_for_threads(["t1", "t2"]) == {"t1": 2, "t2": 0}

    def test_counting_no_threads_is_not_a_query(self, store):
        assert store.count_items_for_threads([]) == {}
        assert store.count_turns_for_threads([]) == {}

    def test_counts_cover_a_page_larger_than_one_batch(self, store):
        seed_threads(store, 900, prefix="cnt")
        ids = [f"cnt-{index:04d}" for index in range(900)]
        for index in (0, 450, 899):
            store.upsert_item(
                make_item(1, thread_id=f"cnt-{index:04d}", item_id=f"itm_{index}")
            )
        counts = store.count_items_for_threads(ids)
        assert len(counts) == 900
        assert counts["cnt-0000"] == 1
        assert counts["cnt-0450"] == 1
        assert counts["cnt-0001"] == 0


class TestUnboundedDescendantWalk:
    def test_the_walk_is_not_bounded_by_the_read_caps(self, store):
        from omniagents.core.conversation.service import (
            MAX_DESCENDANTS,
            MAX_DESCENDANT_DEPTH,
        )

        store.upsert_thread(make_thread(thread_id="root", user_id="u1"))
        for index in range(MAX_DESCENDANTS + 5):
            store.upsert_thread(
                make_thread(
                    thread_id=f"wide-{index:04d}",
                    user_id="u1",
                    parent_thread_id="root",
                )
            )
        previous = "root"
        for index in range(MAX_DESCENDANT_DEPTH + 5):
            thread_id = f"deep-{index:03d}"
            store.upsert_thread(
                make_thread(
                    thread_id=thread_id, user_id="u1", parent_thread_id=previous
                )
            )
            previous = thread_id

        rows = store.descendant_thread_rows("root")
        ids = {row[0] for row in rows}
        assert len(ids) == (MAX_DESCENDANTS + 5) + (MAX_DESCENDANT_DEPTH + 5)
        assert f"deep-{MAX_DESCENDANT_DEPTH + 4:03d}" in ids
        assert f"wide-{MAX_DESCENDANTS + 4:04d}" in ids

    def test_the_walk_reports_differently_owned_and_unowned_children(self, store):
        store.upsert_thread(make_thread(thread_id="root", user_id="u1"))
        store.upsert_thread(
            make_thread(thread_id="mine", user_id="u1", parent_thread_id="root")
        )
        store.upsert_thread(
            make_thread(thread_id="theirs", user_id="u2", parent_thread_id="root")
        )
        store.upsert_thread(
            make_thread(thread_id="nobodys", user_id=None, parent_thread_id="root")
        )
        rows = {row[0]: row[1] for row in store.descendant_thread_rows("root")}
        assert rows == {"mine": "u1", "theirs": "u2", "nobodys": None}

    def test_the_unbounded_walk_terminates_on_a_cycle(self, store):
        store.upsert_thread(make_thread(thread_id="a", user_id="u1"))
        store.upsert_thread(
            make_thread(thread_id="b", user_id="u1", parent_thread_id="a")
        )
        store.upsert_thread(
            make_thread(thread_id="c", user_id="u1", parent_thread_id="b")
        )
        store.upsert_thread(
            make_thread(thread_id="a", user_id="u1", parent_thread_id="c")
        )
        assert {row[0] for row in store.descendant_thread_rows("a")} == {"b", "c"}

    def test_parents_come_back_so_a_caller_can_order_deepest_first(self, store):
        store.upsert_thread(make_thread(thread_id="root", user_id="u1"))
        store.upsert_thread(
            make_thread(thread_id="kid", user_id="u1", parent_thread_id="root")
        )
        store.upsert_thread(
            make_thread(thread_id="grandkid", user_id="u1", parent_thread_id="kid")
        )
        rows = {row[0]: row[2] for row in store.descendant_thread_rows("root")}
        assert rows == {"kid": "root", "grandkid": "kid"}


class TestThreadAncestry:
    def _tree(self, store):
        store.upsert_thread(make_thread(thread_id="root", user_id="u1"))
        store.upsert_thread(
            make_thread(thread_id="c1", user_id="u1", parent_thread_id="root")
        )
        store.upsert_thread(
            make_thread(thread_id="c2", user_id="u1", parent_thread_id="root")
        )
        store.upsert_thread(
            make_thread(thread_id="g1", user_id="u1", parent_thread_id="c1")
        )
        store.upsert_thread(
            make_thread(thread_id="gg1", user_id="u1", parent_thread_id="g1")
        )

    def test_walks_the_whole_subtree_with_depths(self, store):
        self._tree(store)
        rows = store.list_descendant_threads("root", max_depth=10, limit=50)
        assert [(t.thread_id, d) for t, d in rows] == [
            ("c1", 1),
            ("c2", 1),
            ("g1", 2),
            ("gg1", 3),
        ]

    def test_max_depth_bounds_the_walk(self, store):
        self._tree(store)
        rows = store.list_descendant_threads("root", max_depth=2, limit=50)
        assert {t.thread_id for t, _ in rows} == {"c1", "c2", "g1"}

    def test_limit_bounds_the_walk(self, store):
        self._tree(store)
        assert len(store.list_descendant_threads("root", max_depth=10, limit=2)) == 2

    def test_a_parent_cycle_terminates(self, store):
        self._tree(store)
        store.upsert_thread(
            make_thread(thread_id="root", user_id="u1", parent_thread_id="gg1")
        )
        rows = store.list_descendant_threads("root", max_depth=32, limit=50)
        assert {t.thread_id for t, _ in rows} == {"c1", "c2", "g1", "gg1"}

    def test_a_differently_owned_child_is_not_surfaced(self, store):
        self._tree(store)
        store.upsert_thread(
            make_thread(thread_id="alien", user_id="u2", parent_thread_id="root")
        )
        rows = store.list_descendant_threads("root", max_depth=10, limit=50)
        assert "alien" not in {t.thread_id for t, _ in rows}

    def test_an_unknown_root_has_no_descendants(self, store):
        assert store.list_descendant_threads("nope", max_depth=10, limit=50) == []


# ---------------------------------------------------------------------
# SQLite-specific: the FTS5 tables are separate OBJECTS, so "deleted"
# has to mean deleted there too. (Postgres needs no counterpart: its
# tsvector is a column of the row, so deleting the row deletes the index
# entry — which is why these are module-level functions rather than
# members of the shared contract classes.)
# ---------------------------------------------------------------------


def test_deleting_a_thread_empties_the_fts_tables_not_just_the_rows(tmp_path):
    """The search API would look clean either way — it joins ``threads``,
    which is gone. But an FTS row that outlives its item keeps the deleted
    transcript readable in the database file, which is precisely the
    retention posture ``PRAGMA secure_delete`` exists to hold."""
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    try:
        backend.upsert_thread(make_thread(thread_id="t1", title="kumquat"))
        backend.upsert_item(make_item(1, content={"text": "rambutan"}))
        connection = backend._connect()

        assert (
            connection.execute(
                "SELECT COUNT(*) FROM item_search WHERE thread_id = 't1'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM thread_search WHERE thread_id = 't1'"
            ).fetchone()[0]
            == 1
        )

        backend.delete_thread("t1")

        assert connection.execute("SELECT COUNT(*) FROM item_search").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM thread_search").fetchone()[0] == 0
        )
        # And no fragment of the deleted text is still matchable.
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM item_search WHERE item_search MATCH 'rambutan'"
            ).fetchone()[0]
            == 0
        )
    finally:
        backend.close()


def test_a_revised_item_leaves_no_stale_row_in_the_index(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    try:
        backend.upsert_thread(make_thread(thread_id="t1", title="x"))
        backend.upsert_item(make_item(1, content={"text": "kumquat"}))
        backend.upsert_item(make_item(1, content={"text": "rambutan"}, revision=1))
        connection = backend._connect()
        assert connection.execute("SELECT COUNT(*) FROM item_search").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM item_search WHERE item_search MATCH 'kumquat'"
            ).fetchone()[0]
            == 0
        )
    finally:
        backend.close()


def test_search_reports_unavailable_rather_than_scanning_in_python(tmp_path):
    """A silent fallback would answer the same query differently on
    different machines."""
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    try:
        backend.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
        backend._connect()
        backend._fts_ready = False
        with pytest.raises(SearchUnavailable):
            backend.search("anything", ThreadFilter(user_id="u1"), limit=10)
    finally:
        backend.close()
