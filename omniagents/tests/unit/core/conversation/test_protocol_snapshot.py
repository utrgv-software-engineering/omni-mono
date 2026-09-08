"""Full hydration must represent one durable database state, not page timings."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore


def test_snapshot_does_not_mix_items_and_inbox_from_different_commits(tmp_path):
    path = str(tmp_path / "snapshot.db")
    reader = SqliteConversationStore(path=path)
    writer = SqliteConversationStore(path=path)
    recorder = ConversationRecorder(reader)
    recorder.ensure_thread("thread", project_legacy_history=False)
    recorder.record(
        "thread",
        "message_output",
        {"run_id": "run", "message_id": "message", "content": "before"},
    )
    reader.mutate_queue(
        "thread", lambda state, receipt: (state.update(marker="before"), None)
    )
    writer.get_thread("thread")  # initialize before holding the read transaction
    attempted = Event()
    submitted = []

    def update():
        attempted.set()
        with writer._lock, writer._connect() as conn:
            conn.execute(
                "UPDATE items SET content_json=? WHERE thread_id=?",
                (json.dumps({"text": "after"}), "thread"),
            )
            conn.execute(
                "UPDATE command_inboxes SET state=? WHERE thread_id=?",
                (json.dumps({"version": 1, "items": [], "marker": "after"}), "thread"),
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:

            def interleave(statement):
                if (
                    statement.startswith("SELECT state FROM command_inboxes")
                    and not submitted
                ):
                    submitted.append(executor.submit(update))
                    assert attempted.wait(2)
                    # Without a read transaction this writer commits between
                    # the item and inbox SELECTs, producing a torn snapshot.
                    time.sleep(0.05)

            reader._connect().set_trace_callback(interleave)
            snapshot = reader.read_snapshot("thread")
            reader._connect().set_trace_callback(None)
            submitted[0].result(timeout=5)
        assert snapshot["items"][0].content["text"] == "before"
        assert snapshot["inbox"]["marker"] == "before"
        after = reader.read_snapshot("thread")
        assert after["items"][0].content["text"] == after["inbox"]["marker"] == "after"
    finally:
        reader.close()
        writer.close()
