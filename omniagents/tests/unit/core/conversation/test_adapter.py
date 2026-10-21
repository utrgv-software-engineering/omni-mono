"""Compatibility adapter: legacy history rows → domain objects.

The row shapes here are the ones the runtime actually persists — OpenAI
Responses items (``message`` / ``reasoning`` / ``function_call`` /
``function_call_output``) plus the OmniAgents ``context_summary``
checkpoint marker written by ``core/runtime/compaction``.
"""

import pytest

from omniagents.core.conversation.adapter import backfill_thread, project_thread
from omniagents.core.conversation.model import (
    Item,
    ItemKind,
    ItemStatus,
    Thread,
    TurnStatus,
    derive_item_id,
)
from omniagents.core.conversation.store import SqliteConversationStore

THREAD = "legacy-1"


@pytest.fixture
def store(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    yield backend
    backend.close()


def user(text):
    return {"role": "user", "content": text}


def assistant(text, message_id=None):
    item = {
        "role": "assistant",
        "type": "message",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    if message_id:
        item["id"] = message_id
    return item


def call(name, call_id, arguments="{}"):
    return {
        "type": "function_call",
        "name": name,
        "call_id": call_id,
        "arguments": arguments,
    }


def output(call_id, text):
    return {"type": "function_call_output", "call_id": call_id, "output": text}


class TestProjection:
    def test_user_messages_open_turns(self):
        thread, turns, items = project_thread(
            THREAD, [user("one"), assistant("a"), user("two"), assistant("b")]
        )
        assert [t.ordinal for t in turns] == [1, 2]
        assert thread.turn_count == 2
        assert [i.turn_id for i in items] == [
            turns[0].turn_id,
            turns[0].turn_id,
            turns[1].turn_id,
            turns[1].turn_id,
        ]

    def test_inferred_turns_are_labelled_as_such(self):
        thread, turns, items = project_thread(THREAD, [user("one")])
        assert thread.source == "adapter"
        assert turns[0].source == "adapter"
        assert turns[0].turn_id.startswith("trn_legacy_")
        assert items[0].source == "adapter"

    def test_projected_turns_are_closed(self):
        """Legacy history is the record of runs that already finished; a
        still-running run has live state, which is recorded, not projected."""
        _, turns, _ = project_thread(THREAD, [user("one"), assistant("a")])
        assert all(turn.status is TurnStatus.COMPLETED for turn in turns)

    def test_seq_is_dense_and_ordered(self):
        _, _, items = project_thread(
            THREAD, [user("one"), assistant("a"), user("two"), assistant("b")]
        )
        assert [i.seq for i in items] == [1, 2, 3, 4]

    def test_call_and_output_collapse_into_one_completed_item(self):
        _, _, items = project_thread(
            THREAD,
            [
                user("go"),
                call("execute_bash", "c1", '{"command":"ls"}'),
                output("c1", "a\nb"),
            ],
        )
        tools = [i for i in items if i.kind is ItemKind.TOOL_CALL]
        assert len(tools) == 1
        assert tools[0].status is ItemStatus.COMPLETED
        assert tools[0].content["input"] == '{"command":"ls"}'
        assert tools[0].content["output"] == "a\nb"
        assert tools[0].content["tool_kind"] == "command"

    @pytest.mark.parametrize("run_id", [None, "r1"])
    def test_projected_tool_ids_preserve_legacy_identity(self, store, run_id):
        """Legacy identity matches unscoped events, not modern run-scoped ones."""
        from omniagents.core.conversation.recorder import ConversationRecorder

        recorder = ConversationRecorder(store)
        recorder.ensure_thread(THREAD)
        for method, payload in [
            ("run_started", {"run_id": "r1", "prompt": "go", "prompt_role": "user"}),
            (
                "tool_called",
                {
                    "run_id": run_id,
                    "tool": "read_file",
                    "input": "{}",
                    "call_id": "c1",
                },
            ),
            (
                "tool_result",
                {"run_id": run_id, "tool": "read_file", "output": "x", "call_id": "c1"},
            ),
        ]:
            recorder.record(THREAD, method, {"session_id": THREAD, **payload})
        recorded = next(
            item
            for item in store.list_items(THREAD, limit=50)
            if item.kind is ItemKind.TOOL_CALL
        )

        _, _, projected_items = project_thread(
            THREAD, [user("go"), call("read_file", "c1"), output("c1", "x")]
        )
        projected = next(i for i in projected_items if i.kind is ItemKind.TOOL_CALL)

        assert (projected.item_id == recorded.item_id) is (run_id is None)
        assert projected.item_id == derive_item_id(THREAD, "tool:c1")

    def test_repeated_provider_ids_keep_distinct_legacy_tool_results(self):
        _, _, items = project_thread(
            THREAD,
            [
                user("first"),
                call("read_file", "c1"),
                output("c1", "first"),
                user("second"),
                call("read_file", "c1"),
                output("c1", "second"),
            ],
        )
        tools = [item for item in items if item.kind is ItemKind.TOOL_CALL]
        assert len({item.item_id for item in tools}) == 2
        assert [item.content["output"] for item in tools] == ["first", "second"]

    def test_a_call_with_no_output_row_is_failed_not_started(self):
        """It never returned; leaving it at 'started' would make a completed
        conversation look like it still had work in flight."""
        _, _, items = project_thread(THREAD, [user("go"), call("execute_bash", "c1")])
        tool = next(i for i in items if i.kind is ItemKind.TOOL_CALL)
        assert tool.status is ItemStatus.FAILED
        assert tool.content["finalized_by"] == "projection"

    def test_an_orphaned_output_is_preserved_not_dropped(self):
        _, _, items = project_thread(
            THREAD, [user("go"), output("gone", "result text")]
        )
        tool = next(i for i in items if i.kind is ItemKind.TOOL_CALL)
        assert tool.content["orphaned_output"] is True
        assert tool.content["output"] == "result text"
        assert tool.status is ItemStatus.COMPLETED

    def test_reasoning_items_are_projected(self):
        _, _, items = project_thread(
            THREAD,
            [
                user("go"),
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [{"type": "summary_text", "text": "plan"}],
                },
            ],
        )
        reasoning = next(i for i in items if i.kind is ItemKind.REASONING)
        assert reasoning.content["summary"] == "plan"
        assert reasoning.content["reasoning_id"] == "rs_1"

    def test_checkpoint_markers_become_compaction_items_and_set_the_floor(self):
        checkpoint = {
            "role": "assistant",
            "content": "summary of earlier work",
            "omniagents": {"kind": "context_summary", "version": 1},
        }
        thread, _, items = project_thread(
            THREAD, [user("go"), checkpoint, assistant("after")]
        )
        compaction = next(i for i in items if i.kind is ItemKind.COMPACTION)
        assert compaction.content["checkpoint_kind"] == "context_summary"
        assert thread.compaction["checkpoint_item_id"] == compaction.item_id
        assert thread.compaction["compacted_below_seq"] == compaction.seq

    def test_content_parts_are_flattened_to_text(self):
        _, _, items = project_thread(
            THREAD, [assistant("hello there", message_id="msg_1")]
        )
        assert items[0].content["text"] == "hello there"
        assert items[0].content["message_id"] == "msg_1"

    def test_items_before_the_first_user_message_still_get_a_turn(self):
        _, turns, items = project_thread(THREAD, [assistant("system opener")])
        assert len(turns) == 1
        assert items[0].turn_id == turns[0].turn_id

    def test_unknown_item_types_are_preserved_verbatim(self):
        _, _, items = project_thread(
            THREAD,
            [{"type": "something_new", "role": "assistant", "content": "payload"}],
        )
        assert items[0].content["raw_type"] == "something_new"
        assert items[0].content["text"] == "payload"

    def test_non_dict_rows_are_skipped(self):
        _, _, items = project_thread(THREAD, [user("go"), "junk", None, assistant("a")])
        assert len(items) == 2

    def test_projection_is_pure_and_repeatable(self):
        history = [
            user("go"),
            call("read_file", "c1"),
            output("c1", "x"),
            assistant("done"),
        ]
        first = project_thread(THREAD, history)
        second = project_thread(THREAD, history)
        assert [i.to_dict() for i in first.items] == [i.to_dict() for i in second.items]

    def test_metadata_is_carried_onto_the_thread(self):
        thread, _, _ = project_thread(
            THREAD,
            [user("go")],
            user_id="u9",
            archived=True,
            created_at=5.0,
            updated_at=6.0,
        )
        assert thread.user_id == "u9"
        assert thread.status.value == "archived"
        assert thread.created_at == 5.0


class TestBackfill:
    def test_persists_the_projection(self, store):
        backfill_thread(
            store, THREAD, [user("go"), call("read_file", "c1"), output("c1", "x")]
        )
        assert store.get_thread(THREAD).source == "adapter"
        assert store.count_turns(THREAD) == 1
        assert store.count_items(THREAD) == 2

    def test_is_idempotent(self, store):
        history = [user("go"), assistant("a")]
        backfill_thread(store, THREAD, history)
        backfill_thread(store, THREAD, history)
        assert store.count_items(THREAD) == 2
        assert store.count_turns(THREAD) == 1

    def test_refuses_to_clobber_an_already_materialized_thread(self, store):
        """A materialized thread must never be overwritten by a late
        migration pass: re-projecting a live thread rewrote rows the
        recorder owned and reset the ordinal cursor, which destroyed data.

        The guard is the EXPLICIT ``projected_at`` marker, not an inference
        from the item count — see the next test."""
        store.upsert_thread(
            Thread(
                thread_id=THREAD,
                created_at=1.0,
                updated_at=1.0,
                last_seq=1,
                source="recorded",
                projected_at=1.0,
            )
        )
        store.upsert_item(
            Item(
                item_id="itm_live",
                thread_id=THREAD,
                turn_id="run1",
                seq=1,
                kind=ItemKind.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                created_at=1.0,
                updated_at=1.0,
                content={"text": "live row"},
            )
        )
        result = backfill_thread(store, THREAD, [user("go"), assistant("a")])
        assert result.source == "recorded"
        assert store.count_items(THREAD) == 1
        assert store.get_thread(THREAD).last_seq == 1

    def test_materializes_an_unmarked_thread_row(self, store):
        """A row that exists but was never materialized — the
        fork-of-a-legacy-session path — is still eligible for projection."""
        store.upsert_thread(
            Thread(
                thread_id=THREAD,
                created_at=1.0,
                updated_at=1.0,
                parent_thread_id="parent-1",
            )
        )
        backfill_thread(store, THREAD, [user("go"), assistant("a")])
        assert store.count_items(THREAD) == 2
        # Ancestry stamped before the projection must survive it.
        assert store.get_thread(THREAD).parent_thread_id == "parent-1"
        assert store.get_thread(THREAD).projected_at is not None

    def test_an_empty_but_materialized_thread_is_not_re_projected(self, store):
        """REGRESSION on the guard itself.

        Inferring "not yet materialized" from a zero item count meant a
        thread row created while its history was empty would re-project when
        history arrived later — silently rewriting a live thread. The
        explicit marker removes the inference: zero items and materialized
        is a legitimate state."""
        store.upsert_thread(
            Thread(
                thread_id=THREAD,
                created_at=1.0,
                updated_at=1.0,
                projected_at=1.0,
            )
        )
        assert store.count_items(THREAD) == 0
        backfill_thread(store, THREAD, [user("late arrival"), assistant("a")])
        assert store.count_items(THREAD) == 0

    def test_projection_stamps_the_marker(self, store):
        backfill_thread(store, THREAD, [user("go")])
        assert store.get_thread(THREAD).projected_at is not None

    def test_empty_history_writes_nothing(self, store):
        assert backfill_thread(store, THREAD, []) is None
        assert store.get_thread(THREAD) is None
