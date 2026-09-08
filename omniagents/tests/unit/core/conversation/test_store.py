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


class TestRunOwnership:
    def test_snapshot_reads_all_items_and_inbox_without_mutating(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder

        recorder = ConversationRecorder(store)
        recorder.ensure_thread("snapshot", project_legacy_history=False)
        for index in range(503):
            recorder.record(
                "snapshot",
                "message_output",
                {"run_id": "run", "message_id": str(index), "content": str(index)},
            )
        store.mutate_queue(
            "snapshot", lambda state, receipt: (state.update(marker="stable"), None)
        )
        first = store.read_snapshot("snapshot")
        second = store.read_snapshot("snapshot")
        assert first == second
        assert len(first["items"]) == 503
        assert first["inbox"]["marker"] == "stable"
        assert [item.seq for item in first["items"]] == sorted(
            item.seq for item in first["items"]
        )
        first["inbox"]["marker"] = "client mutation"
        assert store.read_snapshot("snapshot")["inbox"]["marker"] == "stable"

    def test_unconfirmed_execution_cannot_be_released_or_retried(self, store):
        from omniagents.core.runtime.ownership import reserve_run

        store.upsert_thread(make_thread())
        owner = reserve_run(store, "t1")
        owner.execution_uncertain = True
        for _ in range(2):
            owner.release()
            assert owner.closing and not owner.released
            with pytest.raises(RuntimeError, match="no longer current"):
                owner.assert_current()
            with pytest.raises(RuntimeError, match="unresolved runtime owner"):
                reserve_run(store, "t1")

    def test_closing_waits_for_admitted_operations_before_releasing(self, store):
        from omniagents.core.runtime.ownership import reserve_run

        store.upsert_thread(make_thread())
        owner = reserve_run(store, "t1")
        with owner.operation():
            owner.release()
            assert owner.closing and not owner.released
            with pytest.raises(RuntimeError, match="no longer current"):
                owner.assert_current()
            with pytest.raises(RuntimeError, match="unresolved runtime owner"):
                reserve_run(store, "t1")
        assert owner.released
        reserve_run(store, "t1").release()

    def test_release_can_retry_a_lost_commit_acknowledgment(self, store, monkeypatch):
        from omniagents.core.runtime.ownership import reserve_run

        store.upsert_thread(make_thread())
        owner = reserve_run(store, "t1")
        mutate = store.mutate_queue

        def lost_ack(*args, **kwargs):
            mutate(*args, **kwargs)
            raise RuntimeError("lost acknowledgment")

        monkeypatch.setattr(store, "mutate_queue", lost_ack)
        with pytest.raises(RuntimeError, match="lost acknowledgment"):
            owner.release()
        monkeypatch.setattr(store, "mutate_queue", mutate)
        owner.release()
        reserve_run(store, "t1").release()

    def test_run_and_queue_dispatch_cannot_overtake_each_other(self, store):
        from omniagents.core.runtime.ownership import reserve_run
        from omniagents.core.session import queue_inbox

        store.upsert_thread(make_thread())
        queue_inbox.accept(store, "t1", {"id": "q", "enqueued_at": 1})
        owner = reserve_run(store, "t1")
        assert not queue_inbox.claim(store, "t1", "q", "dispatcher")
        owner.release()
        assert queue_inbox.claim(store, "t1", "q", "dispatcher")
        with pytest.raises(RuntimeError, match="queue dispatcher"):
            reserve_run(store, "t1")
        token = queue_inbox.current_dispatch.set(("t1", "q", "dispatcher"))
        try:
            owner = reserve_run(store, "t1")
        finally:
            queue_inbox.current_dispatch.reset(token)
        owner.release()
        assert queue_inbox.finish(store, "t1", "q", "dispatcher")

    def test_one_runtime_wins_and_stale_generation_cannot_execute_or_release(
        self, store
    ):
        from dataclasses import replace
        from omniagents.core.runtime.ownership import reserve_run

        store.upsert_thread(make_thread())

        def claim(_):
            try:
                return reserve_run(store, "t1")
            except RuntimeError:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            owners = [owner for owner in pool.map(claim, range(8)) if owner]
        assert len(owners) == 1
        owner = owners[0]
        stale = replace(owner)
        owner.assert_current()
        owner.release()
        owner.release()
        next_owner = reserve_run(store, "t1")
        assert next_owner.generation == owner.generation + 1
        with pytest.raises(RuntimeError, match="no longer current"):
            stale.assert_current()
        with pytest.raises(RuntimeError, match="another runtime"):
            stale.release()
        next_owner.assert_current()
        next_owner.release()

    def test_archived_thread_cannot_be_reserved(self, store):
        from omniagents.core.runtime.ownership import reserve_run

        store.upsert_thread(make_thread(status=ThreadStatus.ARCHIVED))
        with pytest.raises(RuntimeError, match="archived"):
            reserve_run(store, "t1")


class TestToolExecution:
    @pytest.mark.parametrize("interruption", ["event", "turn_end"])
    def test_interruption_is_uncertain_and_late_result_does_not_reopen(
        self, store, interruption
    ):
        from omniagents.core.conversation.recorder import ConversationRecorder

        store.upsert_thread(make_thread())
        recorder = ConversationRecorder(store)
        recorder.record("t1", "run_started", {"run_id": "run", "prompt": "go"})
        payload = {"run_id": "run", "call_id": "call", "tool": "effect", "input": "{}"}
        item = recorder.claim_tool_execution("t1", payload, "owner")
        if interruption == "event":
            recorder.record(
                "t1",
                "tool_result",
                {
                    **payload,
                    "output": "[Cancelled by user]",
                    "metadata": {
                        "display_type": "error",
                        "execution_interrupted": True,
                    },
                },
            )
        recorder.record("t1", "run_end", {"run_id": "run", "end_reason": "cancelled"})
        interrupted = store.get_item("t1", item.item_id)
        assert interrupted.is_terminal
        assert interrupted.content["execution_state"] == "uncertain"
        assert "outcome unknown" in interrupted.content["output"]
        with pytest.raises(RuntimeError):
            recorder.claim_tool_execution("t1", payload, "replacement")
        completed = recorder.complete_tool_execution(
            "t1", {**payload, "output": "actual external result"}, "owner"
        )
        assert completed.status == interrupted.status
        assert completed.content["execution_state"] == "completed"
        assert completed.content["output"] == "actual external result"

    def test_concurrent_dispatch_has_one_owner_and_completion_is_idempotent(
        self, store
    ):
        from omniagents.core.conversation.recorder import ConversationRecorder

        store.upsert_thread(make_thread())
        recorder = ConversationRecorder(store)
        payload = {"run_id": "run", "call_id": "call", "tool": "effect", "input": "{}"}

        def claim(index):
            try:
                recorder.claim_tool_execution("t1", payload, str(index))
                return str(index)
            except RuntimeError:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            owners = [owner for owner in pool.map(claim, range(8)) if owner is not None]
        assert len(owners) == 1
        result = {**payload, "output": "saved"}
        with pytest.raises(RuntimeError, match="another dispatcher"):
            recorder.complete_tool_execution("t1", result, "foreign")
        item = recorder.complete_tool_execution("t1", result, owners[0])
        assert item.status == ItemStatus.COMPLETED
        assert recorder.complete_tool_execution("t1", result, owners[0]) == item
        with pytest.raises(ValueError, match="conflicts"):
            recorder.complete_tool_execution(
                "t1", {**result, "output": "different"}, owners[0]
            )
        recorder.claim_tool_execution("t1", {**payload, "run_id": "next"}, "next")
        assert len(store.list_items("t1", limit=10)) == 2


class TestApprovalDecisions:
    @pytest.mark.parametrize("kind", ["tool", "mcp"])
    def test_decision_is_first_writer_wins_and_survives_terminal_retry(
        self, store, kind
    ):
        from omniagents.core.conversation.recorder import ConversationRecorder

        store.upsert_thread(make_thread())
        recorder = ConversationRecorder(store)
        payload = {"call_id": "token", "request_id": "token", "tool_name": "tool"}
        recorder.record("t1", f"{kind}_approval_requested", payload)

        def commit(index):
            response = {"decision": "approve" if index % 2 else "reject"}
            try:
                recorder.commit_approval_decision(
                    "t1", approval_kind=kind, resource_id="token", response=response
                )
                return response
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(commit, range(8)))
        accepted = [result for result in results if result is not None]
        assert len(accepted) == 4 and all(value == accepted[0] for value in accepted)
        recorder.record("t1", f"{kind}_approval_resolved", payload)
        item = recorder.commit_approval_decision(
            "t1", approval_kind=kind, resource_id="token", response=accepted[0]
        )
        assert item.status == ItemStatus.COMPLETED
        assert item.content["approval_response"] == accepted[0]

    def test_missing_and_retired_prompt_cannot_authorize(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder

        store.upsert_thread(make_thread())
        recorder = ConversationRecorder(store)
        args = dict(
            approval_kind="tool", resource_id="token", response={"decision": "approve"}
        )
        with pytest.raises(RuntimeError, match="not durably recorded"):
            recorder.commit_approval_decision("t1", **args)
        recorder.record("t1", "tool_approval_requested", {"call_id": "token"})
        recorder.record("t1", "tool_approval_resolved", {"call_id": "token"})
        with pytest.raises(RuntimeError, match="no longer pending"):
            recorder.commit_approval_decision("t1", **args)


class TestSubmissionReceipts:
    def test_concurrent_claims_share_one_owner_and_completed_receipt(self, store):
        from threading import Barrier

        store.upsert_thread(make_thread("racing"))
        barrier = Barrier(8)

        def claim(index):
            barrier.wait(timeout=10)
            return store.submission_receipt(
                "racing",
                "same-submission",
                claim={"status": "pending", "owner": str(index)},
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(claim, range(8)))
        assert all(receipt == receipts[0] for receipt in receipts)
        completed = {"status": "completed", "result": {"run_id": "one-run"}}
        store.submission_receipt("racing", "same-submission", result=completed)
        assert (
            store.submission_receipt(
                "racing",
                "same-submission",
                claim={"status": "pending", "owner": "late"},
            )
            == completed
        )

    def test_claim_is_atomic_and_receipt_is_session_scoped(self, store):
        store.upsert_thread(make_thread("A"))
        store.upsert_thread(make_thread("B"))
        first = {"status": "pending", "owner": "first"}
        assert store.submission_receipt("A", "key", claim=first) == first
        assert store.submission_receipt("A", "key", claim={"owner": "second"}) == first
        assert store.submission_receipt("B", "key") is None
        done = {"status": "completed", "result": {"run_id": "one"}}
        assert store.submission_receipt("A", "key", result=done) == done
        store.delete_thread("A")
        assert store.submission_receipt("A", "key") is None


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


class TestQueueInbox:
    def item(self, identity="q"):
        return {
            "id": identity,
            "content": identity,
            "role": "user",
            "trigger_run": True,
            "enqueued_at": 1,
            "state": "pending",
            "input_content": [
                {"type": "input_image", "image_url": "data:image/png;base64,AA=="}
            ],
        }

    def test_acceptance_and_receipt_are_one_idempotent_transaction(self, store):
        from omniagents.core.session import queue_inbox as inbox

        store.upsert_thread(make_thread())
        item = self.item()
        result = inbox.accept(
            store, "t1", item, submission_id="send", payload={"content": "q"}
        )
        item["input_content"].clear()
        assert (
            inbox.accept(
                store,
                "t1",
                self.item("other"),
                submission_id="send",
                payload={"content": "q"},
            )
            == result
        )
        assert len(inbox.entries(store, "t1")) == 1
        assert inbox.entries(store, "t1")[0]["item"]["input_content"]
        assert store.submission_receipt("t1", "send")["result"] == result
        with pytest.raises(ValueError, match="different content"):
            inbox.accept(
                store,
                "t1",
                self.item(),
                submission_id="send",
                payload={"content": "different"},
            )

    def test_competing_acceptance_and_dispatch_have_one_winner(self, store):
        from omniagents.core.session import queue_inbox as inbox

        store.upsert_thread(make_thread())
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda i: inbox.accept(
                        store,
                        "t1",
                        self.item(str(i)),
                        submission_id="send",
                        payload={"same": True},
                    ),
                    range(8),
                )
            )
        assert all(result == results[0] for result in results)
        item_id = results[0]["id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(
                pool.map(lambda i: inbox.claim(store, "t1", item_id, str(i)), range(8))
            )
        assert sum(claims) == 1
        assert not inbox.finish(store, "t1", item_id, "foreign")
        assert inbox.cancel(store, "t1", item_id)["reason"] == "dispatch_uncertain"
        assert len(inbox.entries(store, "t1")) == 1

    def test_receipt_failure_rolls_back_inbox(self, store):
        store.upsert_thread(make_thread())

        def invalid(state, receipt):
            state["items"].append({"item": self.item(), "owner": None})
            return None, {"invalid": {1}}

        with pytest.raises(TypeError):
            store.mutate_queue("t1", invalid, submission_id="bad")
        from omniagents.core.session import queue_inbox as inbox

        assert inbox.entries(store, "t1") == []
        assert store.submission_receipt("t1", "bad") is None

    def test_capacity_cancel_and_archive_are_durable(self, store):
        from omniagents.core.session import queue_inbox as inbox

        store.upsert_thread(make_thread())
        inbox.accept(store, "t1", self.item(), limit=1)
        assert (
            inbox.accept(store, "t1", self.item("extra"), limit=1)["reason"]
            == "queue_full"
        )
        store.mutate_thread(
            "t1", lambda thread: setattr(thread, "status", ThreadStatus.ARCHIVED)
        )
        assert not inbox.claim(store, "t1", "q", "owner")
        assert inbox.cancel(store, "t1", "q")["ok"]
        assert inbox.entries(store, "t1") == []

    def test_buffer_promotion_rolls_back_and_never_drops_on_full_queue(self, store):
        from omniagents.core.session import queue_inbox as inbox

        store.upsert_thread(make_thread())
        inbox.buffer_notification(store, "t1", {"content": "keep", "source": "job"})

        def fail(items):
            raise OSError("failed conversion")

        with pytest.raises(OSError):
            inbox.promote_notifications(store, "t1", fail)
        assert len(inbox.notifications(store, "t1")) == 1
        inbox.accept(store, "t1", self.item())
        assert not inbox.promote_notifications(
            store, "t1", lambda items: self.item("batch"), limit=1
        )
        assert len(inbox.notifications(store, "t1")) == 1
        inbox.cancel(store, "t1", "q")
        assert inbox.promote_notifications(
            store, "t1", lambda items: self.item("batch")
        )
        assert inbox.notifications(store, "t1") == []
        assert inbox.entries(store, "t1")[0]["item"]["id"] == "batch"
        store.delete_thread("t1")
        store.upsert_thread(make_thread())
        assert inbox.entries(store, "t1") == []


class TestAtomicMutations:
    def test_in_place_position_mutation_is_rejected_and_rolled_back(self, store):
        store.upsert_thread(make_thread())
        original = store.append_item(make_item(0, turn_id=None))

        def move(item):
            item.seq += 10
            return item

        with pytest.raises(ValueError, match="position"):
            store.mutate_item("t1", original.item_id, move)
        assert store.get_item("t1", original.item_id).to_dict() == original.to_dict()

    def test_competing_item_completion_keeps_first_terminal_result(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder
        from omniagents.core.conversation.model import derive_item_id

        store.upsert_thread(make_thread())
        item = make_item(
            0,
            item_id=derive_item_id("t1", "tool:call"),
            status=ItemStatus.STARTED,
            turn_id=None,
        )
        store.append_item(item)

        def finish(index):
            return ConversationRecorder(store)._update_item(
                "t1",
                "tool:call",
                status=ItemStatus.COMPLETED if index % 2 else ItemStatus.FAILED,
                content={"output": str(index)},
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(finish, range(32)))
        assert all(item.to_dict() == results[0].to_dict() for item in results)
        assert results[0].revision == 1

    def test_concurrent_appends_and_turn_updates_keep_counts(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder

        recorder = ConversationRecorder(store)
        recorder.ensure_thread("t1", project_legacy_history=False)
        recorder.open_turn("t1", "run1", prompt="p", prompt_role="user")

        def write(index):
            if index % 2:
                store.append_item(make_item(index))
            else:
                recorder.record(
                    "t1", "run_status", {"run_id": "run1", "attempt": index}
                )
                store.mutate_thread(
                    "t1", lambda thread: setattr(thread, "pinned", True)
                )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(32)))
        thread = store.get_thread("t1")
        turn = store.get_turn("t1", "run1")
        assert thread.item_count == turn.item_count == 17
        assert (thread.last_seq, turn.first_seq, turn.last_seq) == (17, 1, 17)
        assert turn.attempts == 30
        assert thread.pinned

    def test_concurrent_thread_patches_preserve_all_fields(self, store):
        store.upsert_thread(make_thread())

        def patch(index):
            def update(thread):
                thread.metadata[str(index)] = index

            store.mutate_thread("t1", update)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(patch, range(32)))
        assert store.get_thread("t1").metadata == {str(i): i for i in range(32)}

    def test_concurrent_item_revisions_are_not_lost(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder
        from omniagents.core.conversation.model import derive_item_id

        store.upsert_thread(make_thread())
        item = make_item(0, item_id=derive_item_id("t1", "artifact"), turn_id=None)
        store.append_item(item)

        def revise(index):
            ConversationRecorder(store)._revise_item(
                "t1", "artifact", content={str(index): index}
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(revise, range(32)))
        result = store.get_item("t1", item.item_id)
        assert result.revision == 32
        assert all(result.content[str(i)] == i for i in range(32))

    def test_turn_claims_and_prompts_commit_once(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder

        store.upsert_thread(make_thread())

        def start(index):
            return ConversationRecorder(store).open_turn(
                "t1", f"run{index % 4}", prompt=f"p{index % 4}", prompt_role="user"
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(start, range(32)))
        turns = store.list_turns("t1", limit=10)
        assert [turn.ordinal for turn in turns] == [1, 2, 3, 4]
        assert all(turn.item_count == 1 for turn in turns)
        assert store.get_thread("t1").turn_count == 4
        assert store.get_thread("t1").item_count == 4
        assert len(store.list_items("t1", limit=10)) == 4

    def test_late_token_and_status_do_not_rewrite_settled_turn(self, store):
        from omniagents.core.conversation.recorder import ConversationRecorder

        recorder = ConversationRecorder(store)
        recorder.ensure_thread("t1", project_legacy_history=False)
        recorder.open_turn("t1", "run", prompt="p", prompt_role="user")
        end = dict(
            end_reason="completed",
            error=None,
            usage={"total_tokens": 10},
            model="final",
            model_ref=None,
        )

        def finish(_):
            recorder.close_turn("t1", "run", **end)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(finish, range(8)))
        before = store.get_turn("t1", "run").to_dict()
        recorder.record(
            "t1",
            "token",
            {"run_id": "run", "totals": {"total_tokens": 2}, "model": "stale"},
        )
        recorder.record("t1", "run_status", {"run_id": "run", "attempt": 9})
        assert store.get_turn("t1", "run").to_dict() == before
        assert store.get_thread("t1").usage == {"total_tokens": 10}

    def test_mutation_failure_rolls_back(self, store):
        store.upsert_thread(make_thread())

        def fail(thread):
            thread.title = "not committed"
            raise OSError("write failure")

        with pytest.raises(OSError):
            store.mutate_thread("t1", fail)
        assert store.get_thread("t1").title is None


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


class TestAtomicAppend:
    @pytest.mark.parametrize("same_identity", [False, True])
    def test_concurrent_append_keeps_items_cursors_and_counts_together(
        self, store, same_identity
    ):
        from threading import Barrier

        store.upsert_thread(make_thread())
        store.upsert_turn(make_turn())
        barrier = Barrier(8)

        def append(index):
            barrier.wait(timeout=10)
            return store.append_item(
                make_item(999, item_id="same" if same_identity else str(index))
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(append, range(8)))
        count = 1 if same_identity else 8
        assert sorted({item.seq for item in results}) == list(range(1, count + 1))
        thread = store.get_thread("t1")
        turn = store.get_turn("t1", "run1")
        assert thread.last_seq == thread.item_count == store.count_items("t1") == count
        assert turn.item_count == count
        assert (turn.first_seq, turn.last_seq) == (1, count)

    def test_redelivery_preserves_existing_content_and_all_counts(self, store):
        store.upsert_thread(make_thread())
        store.upsert_turn(make_turn())
        first = store.append_item(make_item(999, content={"text": "original"}))
        assert store.append_item(make_item(999, content={"text": "changed"})) == first
        assert store.get_thread("t1").item_count == 1
        assert store.get_turn("t1", "run1").item_count == 1
        assert store.allocate_seq("t1") == 2

    def test_missing_thread_does_not_leave_an_orphan(self, store):
        with pytest.raises(KeyError):
            store.append_item(make_item(999))
        assert store.count_items("t1") == 0


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


class TestBulkThreadReads:
    """``get_threads`` — the page fetch the ancestry read is assembled from.

    The descendant walk returns ids (see :class:`TestDescendantWalk`); the
    read turns a page of them into rows with this, in one statement rather
    than N.
    """

    def _tree(self, store):
        store.upsert_thread(make_thread(thread_id="root", user_id="u1"))
        for child in ("c1", "c2"):
            store.upsert_thread(
                make_thread(thread_id=child, user_id="u1", parent_thread_id="root")
            )

    def test_a_page_of_ids_comes_back_keyed_by_id(self, store):
        self._tree(store)
        found = store.get_threads(["root", "c1", "c2"])
        assert set(found) == {"root", "c1", "c2"}
        assert found["c1"].parent_thread_id == "root"

    def test_missing_ids_are_absent_rather_than_an_error(self, store):
        """A concurrent delete between the walk and the fetch is normal."""
        self._tree(store)
        assert set(store.get_threads(["c1", "gone"])) == {"c1"}

    def test_an_empty_request_touches_the_database_not_at_all(self, store):
        assert store.get_threads([]) == {}

    def test_duplicate_ids_collapse(self, store):
        self._tree(store)
        assert set(store.get_threads(["c1", "c1", "c1"])) == {"c1"}


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
