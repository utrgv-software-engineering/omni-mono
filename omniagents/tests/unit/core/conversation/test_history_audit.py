import json
import sqlite3

import pytest

from omniagents.core.conversation.history_audit import audit, repair_copy
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore


@pytest.fixture
def databases(tmp_path):
    history = tmp_path / "history.db"
    domain = tmp_path / "domain.db"
    with sqlite3.connect(history) as connection:
        connection.execute(
            "CREATE TABLE history (id INTEGER PRIMARY KEY, session_id TEXT, msg_json TEXT)"
        )
        connection.executemany(
            "INSERT INTO history(id,session_id,msg_json) VALUES (?, 't', ?)",
            [
                (index + 1, json.dumps({"role": "user", "content": text}))
                for index, text in enumerate(["first", "repeated", "repeated"])
            ],
        )
    store = SqliteConversationStore(path=str(domain))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("t", project_legacy_history=False)
    recorder.record_user_message("t", run_id=None, content="first")
    store.close()
    return history, domain


def test_audit_and_repair_copy_are_read_only_for_originals_and_idempotent(
    databases, tmp_path
):
    history, domain = databases
    originals = (history.read_bytes(), domain.read_bytes())
    report = audit(history, domain)[0]
    assert report["status"] == "repairable_suffix"
    assert report["missing_row_ids"] == [2, 3]
    assert "repeated" not in json.dumps(report)
    output = tmp_path / "repaired.db"
    assert repair_copy(history, domain, output, "t")["repaired_items"] == 2
    assert (history.read_bytes(), domain.read_bytes()) == originals
    assert audit(history, output)[0]["status"] == "matched"
    second = tmp_path / "second.db"
    assert repair_copy(history, output, second, "t")["copied"] is False
    assert not second.exists()
    store = SqliteConversationStore(path=str(output))
    items = store.list_items("t", limit=100)
    assert len({item.item_id for item in items}) == 3
    assert [item.seq for item in items] == [1, 2, 3]
    assert items[-1].source == "adapter"
    assert items[-1].source_ref == {"event": "history_repair", "history_row_id": 3}
    assert store.get_thread("t").item_count == 3
    assert store.allocate_seq("t") == 4
    store.close()


@pytest.mark.parametrize(
    "replacement",
    [
        {"role": "assistant", "content": "repeated"},
        {"role": "user", "content": [{"type": "input_image", "image_url": "private"}]},
        {"type": "function_call", "name": "tool"},
        "encrypted",
    ],
)
def test_ambiguous_suffix_is_not_repaired(databases, tmp_path, replacement):
    history, domain = databases
    with sqlite3.connect(history) as connection:
        connection.execute(
            "UPDATE history SET msg_json=? WHERE id=3", (json.dumps(replacement),)
        )
    assert audit(history, domain)[0]["status"] == "review_required"
    output = tmp_path / "refused.db"
    with pytest.raises(ValueError):
        repair_copy(history, domain, output, "t")
    assert not output.exists()


def test_missing_middle_message_is_not_appended_at_end(databases, tmp_path):
    history, domain = databases
    store = SqliteConversationStore(path=str(domain))
    ConversationRecorder(store).record_user_message("t", run_id=None, content="later")
    store.close()
    assert audit(history, domain)[0]["status"] == "review_required"


def test_running_turn_and_existing_output_are_refused(databases, tmp_path):
    history, domain = databases
    with pytest.raises(FileExistsError):
        repair_copy(history, domain, domain, "t")
    store = SqliteConversationStore(path=str(domain))
    ConversationRecorder(store).open_turn(
        "t", "active", prompt=None, prompt_role="user"
    )
    store.close()
    with pytest.raises(ValueError, match="running turn"):
        repair_copy(history, domain, tmp_path / "refused.db", "t")


def test_missing_input_is_not_created(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        audit(missing, missing)
    assert not missing.exists()


@pytest.mark.parametrize(
    "difference", [None, "missing_response", "missing_checkpoint", "moved", "version"]
)
def test_checkpoints_are_compared_in_order_not_counted_as_messages(
    databases, tmp_path, difference
):
    history, domain = databases
    summary = {
        "role": "assistant",
        "content": "private summary",
        "omniagents": {"kind": "context_summary", "version": 1},
    }
    rows = [{"role": "user", "content": "first"}]
    if difference == "missing_response":
        rows.append({"role": "assistant", "content": "lost response"})
    rows.append(summary)
    if difference == "moved":
        rows.reverse()
    with sqlite3.connect(history) as connection:
        connection.execute("DELETE FROM history")
        connection.executemany(
            "INSERT INTO history(id,session_id,msg_json) VALUES (?, 't', ?)",
            [(index + 1, json.dumps(row)) for index, row in enumerate(rows)],
        )
    store = SqliteConversationStore(path=str(domain))
    if difference != "missing_checkpoint":
        ConversationRecorder(store).record_compaction_checkpoint(
            "t",
            summary="private summary",
            kind="context_summary",
            version=2 if difference == "version" else 1,
        )
    store.close()
    originals = (history.read_bytes(), domain.read_bytes())
    report = audit(history, domain)[0]
    assert report["history_messages"] == (2 if difference == "missing_response" else 1)
    assert report["canonical_messages"] == 1
    assert report["history_checkpoints"] == 1
    assert report["canonical_checkpoints"] == (
        0 if difference == "missing_checkpoint" else 1
    )
    assert "private summary" not in json.dumps(report)
    assert report["status"] == ("review_required" if difference else "matched")
    assert report["message_sequences_match"] is (difference != "missing_response")
    assert report["checkpoint_sequences_match"] is (
        difference not in ("missing_checkpoint", "version")
    )
    if difference == "moved":
        assert (
            report["reason"]
            == "Messages and checkpoints match but their relative ordering differs"
        )
    output = tmp_path / "refused.db"
    if difference:
        with pytest.raises(ValueError):
            repair_copy(history, domain, output, "t")
    else:
        assert repair_copy(history, domain, output, "t")["copied"] is False
    assert not output.exists()
    assert (history.read_bytes(), domain.read_bytes()) == originals


def test_repair_preserves_other_threads_and_existing_revisions(databases, tmp_path):
    history, domain = databases
    store = SqliteConversationStore(path=str(domain))
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("other", project_legacy_history=False)
    recorder.record_user_message("other", run_id=None, content="unrelated")
    original = store.list_items("t", limit=1)[0]
    original.revision = 7
    store.upsert_item(original)
    other = store.list_items("other", limit=1)[0]
    thread = store.get_thread("t")
    thread.pinned = True
    thread.metadata = {"keep": "original"}
    store.upsert_thread(thread)
    store.close()
    output = tmp_path / "copy.db"
    repair_copy(history, domain, output, "t")
    assert output.stat().st_mode & 0o777 == 0o600
    repaired = SqliteConversationStore(path=str(output))
    assert repaired.get_item("t", original.item_id) == original
    assert repaired.get_item("other", other.item_id) == other
    assert repaired.get_thread("t").metadata == thread.metadata
    assert repaired.get_thread("t").pinned
    assert repaired.get_thread("t").updated_at == thread.updated_at
    repaired.close()
