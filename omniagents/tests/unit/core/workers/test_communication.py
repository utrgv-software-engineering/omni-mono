"""Optional communication uses durable worker identity, not a second team lifecycle."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.run_context import RunContextWrapper
from omniagents.core.conversation.model import Thread
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.workers import communication as mail
from omniagents.core.workers.durable import WorkerJournal
from omniagents.core.workers.recipes import reconstruct
from omniagents.core.workers.config import worker_defaults


@pytest.fixture
def journal(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "mail.db"))
    store.upsert_thread(Thread(thread_id="parent", user_id="alice"))
    yield WorkerJournal(store, "parent", "alice")
    store.close()


def admit(journal, wid, *, communication=True, name=None, operation=None):
    return journal.admit(
        wid,
        operation or wid,
        "assignment",
        {
            "kind": "project",
            "version": 1,
            "communication": communication,
            "messaging": "explicit",
            "name": name,
            "role": "review",
            "definition": worker_defaults().recipe_id,
        },
        {"kind": "none", "prepared": True},
        "runtime",
    )


def service_for(journal):
    parent = SimpleNamespace(id=journal.parent_id, user_id="alice")
    return SimpleNamespace(
        get_session=lambda sid: parent if sid == parent.id else None,
        _queue_store=lambda session: journal.store,
        _refresh_queue=lambda session: None,
    )


def test_discovery_and_messages_enforce_parent_and_opt_in(journal):
    admit(journal, "one")
    admit(journal, "two")
    admit(journal, "private", communication=False)
    journal.store.upsert_thread(Thread(thread_id="other", user_id="alice"))
    other = WorkerJournal(journal.store, "other", "alice")
    admit(other, "foreign")
    assert {p["worker_id"] for p in mail.context(journal, "one")["peers"]} == {
        "one",
        "two",
    }
    for recipient in ("private", "foreign", "one", "missing"):
        with pytest.raises(ValueError, match="Recipient"):
            mail.accept_message(journal, "one", recipient, "hello", "call")
    assert mail.context(journal, "private")["peers"] == []
    mail.accept_message(journal, "one", "two", "contract", "call")
    assert not mail.context(other, "foreign")["messages"]
    row = journal.get("two")
    journal.change("two", row["generation"], row["owner"], cleaned=True)
    assert len(mail.context(journal, "one")["peers"]) == 1
    with pytest.raises(ValueError, match="Recipient"):
        mail.accept_message(journal, "one", "two", "hello", "next")


def test_mail_survives_reopen_and_deduplicates(journal, tmp_path):
    admit(journal, "one")
    admit(journal, "two")
    first = mail.accept_message(journal, "one", "two", "contract", "call")
    store = SqliteConversationStore(path=str(tmp_path / "mail.db"))
    try:
        recovered = WorkerJournal(store, "parent", "alice")
        assert mail.context(recovered, "two")["messages"] == [first]
        assert mail.accept_message(recovered, "one", "two", "contract", "call") == first
        with pytest.raises(ValueError, match="different input"):
            mail.accept_message(recovered, "one", "owner", "contract", "call")
        assert mail.inspect_worker(recovered, "two")["messages"] == [first]
    finally:
        store.close()


def test_names_are_atomic_but_replayed_spawn_is_idempotent(journal):
    def start(i):
        try:
            return admit(journal, str(i), name="reviewer")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, (1, 2)))
    assert sum(row is not None for row in results) == 1
    row = next(row for row in results if row)
    assert (
        admit(journal, "new-generated-id", name="reviewer", operation=row["worker_id"])
        == row
    )


@pytest.mark.asyncio
async def test_reconstructed_tools_bind_child_identity(journal):
    one = admit(journal, "one")
    admit(journal, "two")
    configure = reconstruct(
        one["recipe"], service_for(journal), "parent", worker_id="one"
    )
    spec = SimpleNamespace(available_tools=[])
    configure(spec)
    tools = {t.name: t for t in spec.available_tools}
    assert set(tools) == {"worker_context", "worker_message"}
    ctx = RunContextWrapper(context={"session_id": one["session_id"]})
    result = json.loads(await tools["worker_context"]._original_func(ctx))
    assert result["worker_id"] == "one"
    result = json.loads(
        await tools["worker_message"]._original_func(ctx, "owner", "findings")
    )
    assert result["to"] == "owner"
    wrong = RunContextWrapper(context={"session_id": "parent"})
    for name, args in (("worker_context", ()), ("worker_message", ("owner", "spoof"))):
        with pytest.raises(PermissionError):
            await tools[name]._original_func(wrong, *args)
    with pytest.raises(ValueError, match="recruitment"):
        configure(
            SimpleNamespace(available_tools=[SimpleNamespace(name="spawn_worker")])
        )


@pytest.mark.asyncio
async def test_failed_live_delivery_keeps_mail_and_retry_uses_same_queue_key(
    journal, monkeypatch
):
    from omniagents.builtin.tools import worker_tools

    admit(journal, "one")
    admit(journal, "two")
    delivery = AsyncMock(side_effect=[RuntimeError("offline"), "message_queued"])
    monkeypatch.setattr(worker_tools, "_restore_workers", lambda sid: None)
    monkeypatch.setattr(
        worker_tools, "manager", lambda: SimpleNamespace(send_message=delivery)
    )
    service = service_for(journal)
    first = await mail.send(service, "parent", "one", "two", "contract", "call")
    assert first["delivery"] == "mailbox"
    second = await mail.send(service, "parent", "one", "two", "contract", "call")
    assert second["delivery"] == "message_queued"
    assert delivery.call_args_list[0] == delivery.call_args_list[1]
    await mail.send(service, "parent", "one", "two", "contract", "call")
    assert delivery.await_count == 2
    assert len(mail.context(journal, "two")["messages"]) == 1


def test_legacy_mail_is_visible_without_changing_membership(journal):
    admit(journal, "old", communication=False)
    journal.save_group(
        "old-team",
        {
            "kind": "swarm",
            "objective": "old objective",
            "closed": False,
            "members": {
                "builder": {"name": "builder", "worker_id": "old", "role": "build"}
            },
            "messages": [{"from": "owner", "to": "builder", "message": "retained"}],
        },
        0,
    )
    view = mail.inspect_worker(journal, "old")["legacy_swarms"][0]
    assert view["messages"][0]["message"] == "retained"
    assert not mail.enabled(journal.get("old"))


def test_closed_workers_cannot_receive_or_send_messages(journal):
    admit(journal, "one")
    admit(journal, "two")
    journal.close("two")
    assert not mail.available(journal.get("two"))
    with pytest.raises(ValueError, match="Recipient"):
        mail.accept_message(journal, "one", "two", "too late", "call")
    with pytest.raises(ValueError, match="unavailable"):
        mail.accept_message(journal, "two", "owner", "too late", "call")


def test_mailbox_limits_are_explicit_and_preserve_accepted_messages(journal):
    admit(journal, "one")
    for invalid in (" ", "x" * 16001):
        with pytest.raises(ValueError, match="characters"):
            mail.accept_message(journal, "one", "owner", invalid, "invalid")
    first = mail.accept_message(journal, "one", "owner", "accepted", "first")
    journal.mutate(
        lambda data, state: data["messages"].extend(
            {**first, "id": str(i), "operation_id": str(i)} for i in range(499)
        )
    )
    assert mail.accept_message(journal, "one", "owner", "accepted", "first") == first
    with pytest.raises(ValueError, match="limit"):
        mail.accept_message(journal, "one", "owner", "overflow", "next")
    assert len(mail.context(journal, "one")["messages"]) == 500


def test_normal_turn_end_is_silent_but_failure_notifies_parent(journal):
    from omniagents.core.session import queue_inbox

    row = admit(journal, "one", communication=False)
    journal.finish("one", row["generation"], row["owner"], "completed", "ordinary text")
    assert queue_inbox.notifications(journal.store, "parent") == []
    assert journal.get("one")["result"] == "ordinary text"
    resumed = journal.resume("one", "follow-up", "continue", "runtime")
    journal.finish(
        "one",
        resumed["generation"],
        resumed["owner"],
        "error",
        error="Provider rejected the request",
    )
    notices = queue_inbox.notifications(journal.store, "parent")
    assert len(notices) == 1
    assert "Provider rejected the request" in notices[0]["content"]


@pytest.mark.asyncio
async def test_nonpeer_worker_can_explicitly_message_parent_once(journal):
    from omniagents.core.session import queue_inbox

    row = admit(journal, "private", communication=False)
    spec = SimpleNamespace(available_tools=[])
    reconstruct(row["recipe"], service_for(journal), "parent", worker_id="private")(
        spec
    )
    assert {tool.name for tool in spec.available_tools} == {
        "worker_context",
        "worker_message",
    }
    for _ in range(2):
        result = await mail.send(
            service_for(journal), "parent", "private", "owner", "Please review", "call"
        )
        assert result["delivery"] == "message_queued"
    notices = queue_inbox.notifications(journal.store, "parent")
    assert len(notices) == 1
    assert notices[0]["source"] == "worker.message"
    assert "Please review" in notices[0]["content"]
    assert len(mail.inspect_worker(journal)["messages"]) == 1
    admit(journal, "peer")
    with pytest.raises(ValueError, match="Recipient"):
        mail.accept_message(journal, "private", "peer", "not permitted", "next")


def test_owner_message_and_notification_roll_back_together(journal, monkeypatch):
    from omniagents.core.session import queue_inbox

    admit(journal, "one")
    original = journal.store.mutate_queue

    def fail(thread_id, update):
        def interrupted(state, receipt):
            update(state, receipt)
            raise RuntimeError("before commit")

        return original(thread_id, interrupted)

    monkeypatch.setattr(journal.store, "mutate_queue", fail)
    with pytest.raises(RuntimeError, match="before commit"):
        mail.accept_message(journal, "one", "owner", "finding", "call")
    monkeypatch.setattr(journal.store, "mutate_queue", original)
    assert mail.inspect_worker(journal)["messages"] == []
    assert queue_inbox.notifications(journal.store, "parent") == []
