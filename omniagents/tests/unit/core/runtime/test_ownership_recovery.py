"""The run lease: a dead holder is displaced, a live one is not, nothing waits."""

import asyncio

import pytest

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.runtime import ownership
from omniagents.core.runtime.ownership import reserve_run
from omniagents.core.session import queue_inbox

from .lease_support import expire_lease


@pytest.fixture
def reserved(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "owner.db"))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("thread", project_legacy_history=False)
    owner = reserve_run(store, "thread")
    yield store, recorder, owner
    store.close()


def test_live_lease_refuses_a_second_runtime(reserved):
    store, _, owner = reserved
    with pytest.raises(RuntimeError, match="active runtime owner"):
        reserve_run(store, "thread")
    owner.assert_current()
    owner.release()
    assert owner.released


def test_lapsed_lease_is_taken_over_without_ceremony(reserved):
    store, _, owner = reserved
    expire_lease(store, "thread")
    replacement = reserve_run(store, "thread")
    assert replacement.generation == owner.generation + 1
    assert replacement.took_over_from == {
        "run_id": owner.run_id,
        "owner": owner.owner,
        "generation": owner.generation,
    }
    with pytest.raises(RuntimeError, match="no longer current"):
        owner.assert_current()
    # The displaced holder's late release is a no-op, never an error.
    owner.release()
    replacement.assert_current()
    replacement.release()


def test_heartbeat_keeps_the_lease_alive(reserved, monkeypatch):
    store, _, owner = reserved
    monkeypatch.setattr(ownership, "LEASE_TTL", 0.05)
    assert owner.heartbeat()
    with pytest.raises(RuntimeError, match="active runtime owner"):
        reserve_run(store, "thread")
    import time

    time.sleep(0.1)
    replacement = reserve_run(store, "thread")
    assert replacement.took_over_from is not None
    assert not owner.heartbeat()
    replacement.release()


@pytest.mark.asyncio
async def test_heartbeat_task_refreshes_until_release(reserved, monkeypatch):
    store, _, owner = reserved
    monkeypatch.setattr(ownership, "HEARTBEAT_INTERVAL", 0.01)
    before = queue_inbox.runtime_owner(store, "thread")["heartbeat"]
    owner.start_heartbeat()
    await asyncio.sleep(0.05)
    assert queue_inbox.runtime_owner(store, "thread")["heartbeat"] > before
    owner.release()
    assert owner._heartbeat_task is None or owner._heartbeat_task.cancelled()


def test_uncertain_execution_no_longer_pins_the_session(reserved):
    store, _, owner = reserved
    owner.execution_uncertain = True
    owner.release()
    assert owner.released
    reserve_run(store, "thread").release()


def test_takeover_returns_the_dead_dispatchers_claims_to_pending(reserved):
    store, recorder, owner = reserved
    tool = recorder.claim_tool_execution(
        "thread",
        {
            "run_id": owner.run_id,
            "call_id": "call",
            "tool": "execute_bash",
            "input": "{}",
        },
        "tool-owner",
    )

    def claimed(state, receipt):
        state["items"] = [
            {
                "owner": "dead-dispatcher",
                "claimed_at": 0,
                "item": {"id": "queued", "state": "pending", "trigger_run": True},
            }
        ]
        return None, None

    store.mutate_queue("thread", claimed)
    expire_lease(store, "thread")
    replacement = reserve_run(store, "thread")
    state = store.mutate_queue("thread", lambda state, receipt: (state, None))
    assert state["items"][0]["owner"] is None
    assert state["items"][0]["item"]["state"] == "pending"
    # The tool claim itself is untouched: history, not the lease, says what
    # happened to it.
    assert (
        store.get_item("thread", tool.item_id).content["execution_state"] == "claimed"
    )
    replacement.release()


def test_stale_queue_claim_lapses_and_cancel_always_removes(reserved):
    store, _, owner = reserved
    owner.release()
    queue_inbox.accept(
        store,
        "thread",
        {
            "id": "q",
            "content": "x",
            "role": "user",
            "trigger_run": True,
            "enqueued_at": 1,
            "state": "pending",
        },
    )
    assert queue_inbox.claim(store, "thread", "q", "dead")
    assert not queue_inbox.claim(store, "thread", "q", "next")
    expire_lease(store, "thread", claims=True)
    assert queue_inbox.claim(store, "thread", "q", "next")
    assert queue_inbox.cancel(store, "thread", "q")["ok"]
    assert queue_inbox.entries(store, "thread") == []


def test_record_written_without_a_heartbeat_counts_as_dead(reserved):
    store, _, owner = reserved

    def legacy(state, receipt):
        state["runtime_owner"].pop("heartbeat")
        return None, None

    store.mutate_queue("thread", legacy)
    replacement = reserve_run(store, "thread")
    assert replacement.generation == 2
    replacement.release()
