"""Unit tests for the durable sequenced session event journal."""

import pytest

from omniagents.core.session.event_log import (
    MemoryEventLogStore,
    REPLAYABLE_METHODS,
    ResyncRequiredError,
    SessionEventLog,
    SqliteEventLogStore,
    stamp_session_event,
)


def make_log(session_id="sess", store=None, max_events=100, **kwargs):
    return SessionEventLog(
        session_id,
        store=store or MemoryEventLogStore(),
        max_events=max_events,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Sequencing + stamping
# ---------------------------------------------------------------------------


def test_record_stamps_monotonic_seq_stream_and_session():
    log = make_log()
    first = log.record("message_output", {"run_id": "r", "content": "a"})
    second = log.record("tool_called", {"run_id": "r", "tool": "t", "input": ""})
    assert first["seq"] == 1
    assert second["seq"] == 2
    assert first["stream_id"] == second["stream_id"] == log.stream_id
    assert first["session_id"] == "sess"


def test_record_is_idempotent_for_already_stamped_payloads():
    log = make_log()
    payload = log.record("message_output", {"content": "a"})
    again = log.record("message_output", payload)
    assert again is payload
    assert again["seq"] == 1
    assert log.last_seq == 1


def test_stamp_session_event_only_touches_replayable_methods():
    class FakeSession:
        pass

    session = FakeSession()
    session.event_log = make_log()
    token_payload = {"delta": {}, "totals": {}}
    assert stamp_session_event(session, "token", token_payload) is token_payload
    assert "seq" not in token_payload
    stamped = stamp_session_event(session, "run_started", {"run_id": "r"})
    assert stamped["seq"] == 1
    assert "token" not in REPLAYABLE_METHODS
    # Sessions without a journal pass through untouched.
    bare = FakeSession()
    payload = {"run_id": "r"}
    assert stamp_session_event(bare, "run_started", payload) is payload


# ---------------------------------------------------------------------------
# Replay cursors
# ---------------------------------------------------------------------------


def test_events_after_returns_ordered_tail():
    log = make_log()
    for index in range(5):
        log.record("message_output", {"content": str(index)})
    events = log.events_after(2)
    assert [payload["seq"] for _, payload in events] == [3, 4, 5]
    assert log.events_after(5) == []


def test_events_after_rejects_wrong_stream_and_future_cursor():
    log = make_log()
    log.record("message_output", {"content": "a"})
    with pytest.raises(ResyncRequiredError):
        log.events_after(0, stream_id="not-the-stream")
    with pytest.raises(ResyncRequiredError):
        log.events_after(99)


# ---------------------------------------------------------------------------
# Retention + compaction
# ---------------------------------------------------------------------------


def test_retention_trim_raises_floor_and_forces_resync():
    log = make_log(max_events=3)
    for index in range(6):
        log.record("message_output", {"content": str(index)})
    assert log.first_retained_seq == 4
    assert log.compacted_below == 3
    # A cursor at/above the floor still works…
    assert [p["seq"] for _, p in log.events_after(3)] == [4, 5, 6]
    # …but a pre-compaction cursor gets an explicit resync error.
    with pytest.raises(ResyncRequiredError) as excinfo:
        log.events_after(2)
    data = excinfo.value.data
    assert data["kind"] == "resync_required"
    assert data["retained_first_seq"] == 4
    assert data["retained_last_seq"] == 6


def test_queue_changed_snapshots_coalesce_without_raising_floor():
    log = make_log()
    log.record("message_output", {"content": "a"})
    log.record("queue_changed", {"depth": 1, "items": []})
    log.record("message_output", {"content": "b"})
    log.record("queue_changed", {"depth": 2, "items": []})
    snapshots = [p for m, p in log.events_after(0) if m == "queue_changed"]
    assert len(snapshots) == 1
    assert snapshots[0]["depth"] == 2
    # Supersession is not information loss: old cursors still resume.
    assert log.compacted_below == 0
    assert [p["seq"] for _, p in log.events_after(0)] == [1, 3, 4]


def test_acknowledge_compacts_only_below_minimum_consumer_cursor():
    log = make_log()
    for index in range(4):
        log.record("message_output", {"content": str(index)})
    log.register_consumer("tab-a", acked_seq=0)
    log.register_consumer("tab-b", acked_seq=0)
    log.acknowledge("tab-a", 4)
    # tab-b has not acked yet: nothing may be compacted.
    assert log.first_retained_seq == 1
    log.acknowledge("tab-b", 2)
    assert log.compacted_below == 2
    assert [p["seq"] for _, p in log.events_after(2)] == [3, 4]
    # Dropping the slow consumer lets the next ack compact fully.
    log.drop_consumer("tab-b")
    log.acknowledge("tab-a", 4)
    assert log.retained_events() == []


def test_acknowledge_clamps_to_stream_head_and_checks_stream():
    log = make_log()
    log.record("message_output", {"content": "a"})
    assert log.acknowledge("tab", 99, stream_id=log.stream_id) == 1
    with pytest.raises(ResyncRequiredError):
        log.acknowledge("tab", 1, stream_id="other-stream")


# ---------------------------------------------------------------------------
# Durability + server-restart recovery
# ---------------------------------------------------------------------------


def _sqlite_store(tmp_path):
    return SqliteEventLogStore(str(tmp_path / "events.db"))


def test_sqlite_journal_survives_process_restart(tmp_path):
    store = _sqlite_store(tmp_path)
    log = make_log(store=store)
    log.record("message_output", {"content": "a"})
    log.record("message_output", {"content": "b"})

    # Simulate a restart: a fresh store instance over the same file.
    reloaded = make_log(store=_sqlite_store(tmp_path))
    assert reloaded.stream_id == log.stream_id
    assert reloaded.last_seq == 2
    assert [p["content"] for _, p in reloaded.events_after(0)] == ["a", "b"]
    # Sequencing continues monotonically after the restart.
    assert reloaded.record("message_output", {"content": "c"})["seq"] == 3


def test_restart_reconciliation_closes_interrupted_run_and_prompts(tmp_path):
    store = _sqlite_store(tmp_path)
    log = make_log(store=store)
    log.record("run_started", {"run_id": "run-1"})
    log.record(
        "tool_approval_requested",
        {"call_id": "call-1", "tool_name": "t", "arguments": ""},
    )
    log.record(
        "client_request",
        {"request_id": "req-1", "function": "ui.ask", "args": {}},
    )

    reloaded = make_log(store=_sqlite_store(tmp_path))
    methods = [m for _, m, _ in reloaded.retained_events()]
    assert methods.count("tool_approval_resolved") == 1
    assert methods.count("client_request_resolved") == 1
    assert methods.count("run_end") == 1
    synthetic = {m: p for _, m, p in reloaded.retained_events()}
    assert synthetic["run_end"]["end_reason"] == "server_restart"
    assert synthetic["tool_approval_resolved"]["reason"] == "server_restart"
    assert synthetic["tool_approval_resolved"]["call_id"] == "call-1"
    assert synthetic["client_request_resolved"]["request_id"] == "req-1"
    # A clean journal (run ended before restart) is left untouched.
    reloaded.record("run_end", {"run_id": "run-99", "end_reason": "completed"})


def test_restart_reconciliation_skips_completed_runs(tmp_path):
    store = _sqlite_store(tmp_path)
    log = make_log(store=store)
    log.record("run_started", {"run_id": "run-1"})
    log.record("run_end", {"run_id": "run-1", "end_reason": "completed"})
    reloaded = make_log(store=_sqlite_store(tmp_path))
    assert reloaded.last_seq == 2
    assert [m for _, m, _ in reloaded.retained_events()] == [
        "run_started",
        "run_end",
    ]


def test_destroy_resets_stream_epoch(tmp_path):
    store = _sqlite_store(tmp_path)
    log = make_log(store=store)
    log.record("message_output", {"content": "a"})
    log.destroy()
    fresh = make_log(store=_sqlite_store(tmp_path))
    assert fresh.stream_id != log.stream_id
    assert fresh.last_seq == 0
