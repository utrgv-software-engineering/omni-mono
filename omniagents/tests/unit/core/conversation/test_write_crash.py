"""Real process-death checks using only disposable databases."""

import subprocess
import sys

import pytest

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore


def test_process_death_releases_runtime_ownership_once_the_lease_lapses(tmp_path):
    from omniagents.core.runtime.ownership import reserve_run
    from ..runtime.lease_support import expire_lease

    path = tmp_path / "ownership.db"
    store = SqliteConversationStore(path=str(path))
    ConversationRecorder(store).ensure_thread("t", project_legacy_history=False)
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.runtime.ownership import reserve_run
reserve_run(SqliteConversationStore(path=sys.argv[1]), 't')
os._exit(74)
""",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    # Within the TTL the dead process still looks alive: refused.
    with pytest.raises(RuntimeError, match="active runtime owner"):
        reserve_run(store, "t")
    # Once its heartbeat lapses the session is simply taken over.
    expire_lease(store, "t")
    replacement = reserve_run(store, "t")
    assert replacement.generation == 2 and replacement.took_over_from is not None
    replacement.release()


@pytest.mark.parametrize("boundary", ["claiming", "claimed", "completed"])
def test_tool_process_death_preserves_dispatch_boundary(tmp_path, boundary):
    path = tmp_path / "tool.db"
    store = SqliteConversationStore(path=str(path))
    rec = ConversationRecorder(store)
    rec.ensure_thread("t", project_legacy_history=False)
    if boundary == "claiming":
        store._connect().execute(
            "CREATE TRIGGER die_tool AFTER UPDATE ON items BEGIN SELECT die_now(); END"
        )
        store._connect().commit()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.conversation.recorder import ConversationRecorder
store = SqliteConversationStore(path=sys.argv[1])
store._connect().create_function('die_now', 0, lambda: os._exit(74))
rec = ConversationRecorder(store)
p = {'run_id':'run','call_id':'call','tool':'effect','input':'{}'}
rec.claim_tool_execution('t',p,'dead')
if sys.argv[2] == 'completed':
    rec.complete_tool_execution('t',dict(p,output='saved'),'dead')
os._exit(74)
""",
            str(path),
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    item = store.list_items("t", limit=10)[0]
    if boundary == "claiming":
        assert "execution_state" not in item.content
        store._connect().execute("DROP TRIGGER die_tool")
        store._connect().commit()
        rec.claim_tool_execution(
            "t",
            {"run_id": "run", "call_id": "call", "tool": "effect", "input": "{}"},
            "replacement",
        )
    else:
        assert item.content["execution_state"] == boundary
        with pytest.raises(RuntimeError, match="already claimed"):
            rec.claim_tool_execution(
                "t",
                {"run_id": "run", "call_id": "call", "tool": "effect", "input": "{}"},
                "replacement",
            )
    store.close()


@pytest.mark.parametrize("during_commit", [False, True])
def test_approval_process_death_never_exposes_a_partial_decision(
    tmp_path, during_commit
):
    from omniagents.core.conversation.model import derive_item_id

    path = tmp_path / "approval.db"
    store = SqliteConversationStore(path=str(path))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("t", project_legacy_history=False)
    recorder.record("t", "tool_approval_requested", {"call_id": "token"})
    if during_commit:
        store._connect().execute(
            "CREATE TRIGGER die_approval AFTER UPDATE ON items BEGIN SELECT die_now(); END"
        )
        store._connect().commit()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.conversation.recorder import ConversationRecorder
store = SqliteConversationStore(path=sys.argv[1])
store._connect().create_function('die_now', 0, lambda: os._exit(74))
ConversationRecorder(store).commit_approval_decision('t', approval_kind='tool', resource_id='token', response={'decision':'approve', 'always_approve':True})
os._exit(74)
""",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    item = store.get_item("t", derive_item_id("t", "approval:tool:token"))
    if during_commit:
        assert "approval_response" not in item.content
        store._connect().execute("DROP TRIGGER die_approval")
        store._connect().commit()
    else:
        assert item.content["approval_response"] == {
            "decision": "approve",
            "always_approve": True,
        }
    # An identical retry is safe whether the transaction committed or rolled back.
    recorder.commit_approval_decision(
        "t",
        approval_kind="tool",
        resource_id="token",
        response={"decision": "approve", "always_approve": True},
    )
    store.close()


@pytest.mark.parametrize("boundary", ["during_accept", "accepted", "claimed"])
def test_queue_process_death_preserves_receipt_and_dispatch_boundary(
    tmp_path, boundary
):
    from omniagents.core.session import queue_inbox as inbox

    path = tmp_path / "inbox.db"
    store = SqliteConversationStore(path=str(path))
    ConversationRecorder(store).ensure_thread("t", project_legacy_history=False)
    if boundary == "during_accept":
        conn = store._connect()
        conn.execute(
            "CREATE TRIGGER die_accept AFTER INSERT ON submission_receipts BEGIN SELECT die_now(); END"
        )
        conn.commit()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.session import queue_inbox as inbox
store = SqliteConversationStore(path=sys.argv[1])
store._connect().create_function('die_now', 0, lambda: os._exit(74))
inbox.accept(store, 't', {'id':'q','content':'accepted','role':'user','trigger_run':True,'enqueued_at':1,'state':'pending'}, submission_id='send', payload={'content':'accepted'})
if sys.argv[2] == 'claimed':
    assert inbox.claim(store, 't', 'q', 'dead-process')
os._exit(74)
""",
            str(path),
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    queued = inbox.entries(store, "t")
    receipt = store.submission_receipt("t", "send")
    if boundary == "during_accept":
        assert queued == [] and receipt is None
    else:
        assert len(queued) == 1
        assert receipt["status"] == "completed"
        assert receipt["result"]["id"] == queued[0]["item"]["id"] == "q"
        assert inbox.claim(store, "t", "q", "replacement-process") is (
            boundary == "accepted"
        )
    store.close()


@pytest.mark.parametrize("boundary", ["items", "turns", "threads"])
def test_turn_start_process_death_never_leaves_a_promptless_turn(tmp_path, boundary):
    path = tmp_path / "start.db"
    store = SqliteConversationStore(path=str(path))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("t", project_legacy_history=False)
    conn = store._connect()
    action = "UPDATE" if boundary == "threads" else "INSERT"
    condition = "WHEN new.turn_count = 1" if boundary == "threads" else ""
    conn.execute(
        f"CREATE TRIGGER die_start AFTER {action} ON {boundary} {condition} BEGIN SELECT die_now(); END"
    )
    conn.commit()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.conversation.recorder import ConversationRecorder
store = SqliteConversationStore(path=sys.argv[1])
store._connect().create_function('die_now', 0, lambda: os._exit(74))
ConversationRecorder(store).open_turn('t', 'run', prompt='accepted prompt', prompt_role='user')
""",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    assert store.get_turn("t", "run") is None
    assert store.list_items("t", limit=10) == []
    assert store.get_thread("t").turn_count == 0
    conn.execute("DROP TRIGGER die_start")
    conn.commit()
    recorder.open_turn("t", "run", prompt="accepted prompt", prompt_role="user")
    recorder.open_turn("t", "run", prompt="accepted prompt", prompt_role="user")
    assert store.get_thread("t").turn_count == 1
    assert store.get_thread("t").item_count == 1
    assert store.get_turn("t", "run").item_count == 1
    assert store.list_items("t", limit=10)[0].content["text"] == "accepted prompt"
    store.close()


@pytest.mark.parametrize("boundary", ["history_commit", "domain_commit"])
@pytest.mark.parametrize("recovery", ["read", "next_write"])
def test_dual_write_recovers_after_process_death(boundary, recovery):
    from omniagents.core.session.manager import Session
    from omniagents.core.session.history_db import load_history, pending_projections
    from omniagents.core.conversation.recorder import recorder_for_session
    from omniagents.core.conversation.service import ConversationService

    session = Session("p", "a", "crash-session")
    recorder = recorder_for_session(session)
    recorder.ensure_thread(session.id)
    recorder.open_turn(session.id, "run", prompt="question", prompt_role="user")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
import omniagents.core.session.manager as manager
import omniagents.core.session.history_db as history_db
session = manager.Session('p', 'a', 'crash-session')
session.active_run_id = 'run'
if sys.argv[1] == 'history_commit':
    original = manager.db_append_message
    def append(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(74)
    manager.db_append_message = append
else:
    history_db.acknowledge_projection = lambda *args, **kwargs: os._exit(74)
session.append_message({'type':'message','role':'assistant','status':'completed',
    'id':'msg_crash','content':'durable answer'})
raise AssertionError('crash boundary not reached')
""",
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    assert (
        load_history(session.id, project_slug="p", agent_slug="a")[-1]["id"]
        == "msg_crash"
    )
    assert len(pending_projections(session.id, project_slug="p", agent_slug="a")) == 1
    if recovery == "read":
        ConversationService(project_slug="p", agent_slug="a").ensure_thread(session.id)
    else:
        recorder.record_compaction_checkpoint(
            session.id, run_id="run", summary="later", kind="context_summary", version=1
        )
    recorder.recover_pending_messages(session.id)
    items = recorder.store.list_items(session.id, limit=100)
    assert [item.kind.value for item in items] == ["user_message", "agent_message"] + (
        ["compaction"] if recovery == "next_write" else []
    )
    assert items[1].content["text"] == "durable answer"
    assert items[1].turn_id == "run"
    assert [item.seq for item in items] == list(range(1, len(items) + 1))
    assert recorder.store.get_thread(session.id).item_count == len(items)
    assert recorder.store.get_turn(session.id, "run").item_count == len(items)
    assert pending_projections(session.id, project_slug="p", agent_slug="a") == []


@pytest.mark.parametrize("boundary", ["turns", "threads"])
def test_turn_end_death_rolls_back_turn_and_usage_together(tmp_path, boundary):
    path = tmp_path / "end.db"
    store = SqliteConversationStore(path=str(path))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("t", project_legacy_history=False)
    recorder.open_turn("t", "run", prompt="p", prompt_role="user")
    before = store.get_turn("t", "run").to_dict()
    conn = store._connect()
    conn.execute(
        f"CREATE TRIGGER die_end AFTER UPDATE ON {boundary} BEGIN SELECT die_now(); END"
    )
    conn.commit()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.conversation.recorder import ConversationRecorder
store = SqliteConversationStore(path=sys.argv[1])
store._connect().create_function('die_now', 0, lambda: os._exit(74))
ConversationRecorder(store).close_turn('t', 'run', end_reason='completed', error=None,
    usage={'total_tokens': 10}, model=None, model_ref=None)
""",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    assert store.get_turn("t", "run").to_dict() == before
    assert store.get_thread("t").usage == {}
    conn.execute("DROP TRIGGER die_end")
    conn.commit()
    for _ in range(2):
        recorder.close_turn(
            "t",
            "run",
            end_reason="completed",
            error=None,
            usage={"total_tokens": 10},
            model=None,
            model_ref=None,
        )
    assert store.get_thread("t").usage == {"total_tokens": 10}
    assert store.get_turn("t", "run").usage == {"total_tokens": 10}
    store.close()


def test_failed_history_commit_does_not_publish_phantom_memory(monkeypatch):
    import omniagents.core.session.manager as manager
    from omniagents.core.session.history_db import load_history

    session = manager.Session("p", "a")
    original = manager.db_append_message

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(manager, "db_append_message", fail)
    row = {
        "role": "assistant",
        "type": "message",
        "status": "completed",
        "id": "msg_retry",
        "content": "retry",
    }
    with pytest.raises(OSError):
        session.append_message(row)
    assert session.history == []
    assert load_history(session.id, project_slug="p", agent_slug="a") == []
    monkeypatch.setattr(manager, "db_append_message", original)
    session.append_message(row)
    assert session.history == [row]


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_context_replacement_is_old_or_new_after_process_death(boundary):
    import asyncio
    from omniagents.core.session.manager import Session
    from omniagents.core.session import history_db

    session = Session("p", "a", "context-crash")
    original = [{"role": "user", "content": "original context"}]
    replacement = [{"role": "user", "content": "compacted context"}]
    asyncio.run(session.add_items(original))
    asyncio.run(session.replace_sdk_items(original))
    conn, lock = history_db._get_store("p", "a")
    if boundary == "before_commit":
        with lock, conn:
            conn.execute(
                "CREATE TRIGGER context_crash AFTER UPDATE OF sdk_context_json ON sessions BEGIN SELECT die_now(); END"
            )
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, os, sys
from omniagents.core.session.manager import Session
from omniagents.core.session.history_db import _get_store
session = Session('p', 'a', 'context-crash')
_get_store('p', 'a')[0].create_function('die_now', 0, lambda: os._exit(74))
asyncio.run(session.replace_sdk_items([{'role':'user','content':'compacted context'}]))
os._exit(74)
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 74, child.stderr
    if boundary == "before_commit":
        with lock, conn:
            conn.execute("DROP TRIGGER context_crash")
    reloaded = Session("p", "a", session.id)
    assert asyncio.run(reloaded.get_items()) == (
        original if boundary == "before_commit" else replacement
    )
    assert reloaded.history == original


@pytest.mark.parametrize("boundary", ["sequence", "item", "turn"])
def test_process_death_rolls_back_the_entire_canonical_append(tmp_path, boundary):
    path = tmp_path / "domain.db"
    store = SqliteConversationStore(path=str(path))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("t", project_legacy_history=False)
    recorder.open_turn("t", "run", prompt="question", prompt_role="user")
    before = (
        store.get_thread("t"),
        store.get_turn("t", "run"),
        store.list_items("t", limit=100),
    )
    table, action, predicate = {
        "sequence": ("threads", "UPDATE", "new.item_count = 2"),
        "item": ("items", "INSERT", "new.kind = 'agent_message'"),
        "turn": ("turns", "UPDATE", "new.item_count = 2"),
    }[boundary]
    conn = store._connect()
    conn.execute(
        f"CREATE TRIGGER crash_boundary AFTER {action} ON {table} WHEN {predicate} BEGIN SELECT die_now(); END"
    )
    conn.commit()
    store.close()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore
store = SqliteConversationStore(path=sys.argv[1])
store._connect().create_function('die_now', 0, lambda: os._exit(73))
ConversationRecorder(store).record('t', 'message_output', {
    'run_id': 'run', 'message_id': 'msg_crash', 'content': 'answer'
})
raise AssertionError('crash boundary not reached')
""",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 73, child.stderr
    store = SqliteConversationStore(path=str(path))
    try:
        assert (
            store.get_thread("t"),
            store.get_turn("t", "run"),
            store.list_items("t", limit=100),
        ) == before
        store._connect().execute("DROP TRIGGER crash_boundary")
        store._connect().commit()
        recorder = ConversationRecorder(store)
        payload = {"run_id": "run", "message_id": "msg_crash", "content": "answer"}
        recorder.record("t", "message_output", payload)
        recorder.record("t", "message_output", payload)
        assert [item.seq for item in store.list_items("t", limit=100)] == [1, 2]
        assert store.get_thread("t").item_count == 2
        assert store.get_turn("t", "run").item_count == 2
    finally:
        store.close()
