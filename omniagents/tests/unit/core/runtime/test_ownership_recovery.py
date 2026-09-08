import json

import pytest

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.runtime.ownership import reserve_run
from omniagents.core.runtime import ownership_recovery as recovery


@pytest.fixture
def reserved(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "owner.db"))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("thread", project_legacy_history=False)
    owner = reserve_run(store, "thread")
    yield store, recorder, owner
    store.close()


def test_live_owner_cannot_be_recovered_even_with_abandonment(reserved):
    store, _, owner = reserved
    owner.record_recovery_scope("host")
    result = recovery.recover_runtime_owner(store, "thread", abandon_unknown=True)
    assert not result["recovered"]
    owner.assert_current()


@pytest.mark.parametrize("scope", ["remote", "host"])
def test_uncertain_execution_not_automatically_recovered(
    reserved, tmp_path, monkeypatch, scope
):
    store, _, owner = reserved
    owner.record_recovery_scope(scope, journal=tmp_path / "missing.json")
    monkeypatch.setattr(recovery, "process_is_dead", lambda identity: True)
    assert (
        recovery.recover_runtime_owner(store, "thread")["reason"]
        == "cleanup_unconfirmed"
    )
    owner.assert_current()


def test_late_cleanup_evidence_releases_a_cancelled_local_owner(reserved, tmp_path):
    store, _, owner = reserved
    journal = tmp_path / "state.json"
    owner.record_recovery_scope("host", journal=journal)
    journal.write_text(json.dumps({"state": "running"}))
    owner.execution_uncertain = True
    owner.release()
    assert not owner.released
    journal.write_text(
        json.dumps(
            {
                "state": "interrupted",
                "descendants_reaped": True,
                "run_owner": {
                    key: getattr(owner, key)
                    for key in ("thread_id", "run_id", "owner", "generation")
                },
            }
        )
    )
    owner.release()
    assert owner.released
    reserve_run(store, "thread").release()


def test_legacy_abandonment_preserves_claims_and_fails_uncertain_queue(reserved):
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

    def legacy(state, receipt):
        state.pop("runtime_recovery")
        state["items"] = [
            {"owner": "old-dispatcher", "item": {"id": "queued", "state": "pending"}}
        ]
        return None, None

    store.mutate_queue("thread", legacy)
    assert (
        recovery.recover_runtime_owner(store, "thread")["reason"]
        == "legacy_owner_unverifiable"
    )
    assert (
        recovery.recover_runtime_owner(store, "thread", abandon_unknown=True)["mode"]
        == "operator_abandonment"
    )
    assert (
        store.get_item("thread", tool.item_id).content["execution_state"] == "claimed"
    )
    state = store.mutate_queue("thread", lambda state, receipt: (state, None))
    assert state["items"][0]["owner"] is None
    assert state["items"][0]["item"]["state"] == "failed"
    next_owner = reserve_run(store, "thread")
    assert next_owner.generation == 2
    next_owner.release()


def test_recovery_cannot_retire_a_replacement_owner(reserved, monkeypatch):
    store, _, owner = reserved
    monkeypatch.setattr(recovery, "process_is_dead", lambda identity: True)
    replacement = None

    def changed(*args):
        nonlocal replacement
        owner.release()
        replacement = reserve_run(store, "thread")
        return True

    monkeypatch.setattr(recovery, "cleanup_confirmed", changed)
    assert recovery.recover_runtime_owner(store, "thread")["reason"] == "owner_changed"
    replacement.assert_current()
    replacement.release()


def test_process_identity_checks_machine_boot_and_pid_reuse():
    identity = recovery.process_identity()
    if identity is None:
        pytest.skip("Linux process identity unavailable")
    assert not recovery.process_is_dead(identity)
    assert not recovery.process_is_dead({**identity, "machine": "other"})
    assert not recovery.process_is_dead({**identity, "pid_namespace": "other"})
    assert recovery.process_is_dead({**identity, "boot": "previous-boot"})
    assert recovery.process_is_dead({**identity, "start": "previous-process"})


@pytest.mark.parametrize("scope", ["host", "remote"])
def test_reboot_retires_only_local_executors(reserved, tmp_path, monkeypatch, scope):
    store, _, owner = reserved
    identity = recovery.process_identity()
    if identity is None:
        pytest.skip("Linux process identity unavailable")
    owner.record_recovery_scope(scope, journal=tmp_path / "missing-final-journal")
    monkeypatch.setattr(
        recovery, "process_identity", lambda: {**identity, "boot": "new-boot"}
    )
    result = recovery.recover_runtime_owner(store, "thread")
    assert result["recovered"] is (scope == "host")
