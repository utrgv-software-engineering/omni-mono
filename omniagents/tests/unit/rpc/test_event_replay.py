"""End-to-end tests for durable sequenced event replay.

Covers the acceptance criteria of the replay protocol:
- disconnect/reconnect recovery from the last acknowledged event,
- duplicate deliveries never producing duplicate items,
- missing retained history producing an explicit resync-required error,
- server-restart recovery,
- multi-tab convergence.
"""

import asyncio

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.session.event_log import ResyncRequiredError
from omniagents.rpc import JsonRpcEndpoint, RpcMethodsBase
from omniagents.rpc.agents.remote import _ClientEventMethods
from omniagents.rpc.replay import ReplayCursor
from omniagents.rpc.server import JsonRpcChannel

from ..core.test_agent_builder import make_spec


def build_service() -> AgentService:
    return AgentService(make_spec(), default_workspace_root="/tmp")


class RecordingChannel:
    """Stands in for a JsonRpcChannel; optionally 'disconnected'."""

    def __init__(self):
        self.calls = []
        self.connected = True

    async def async_call(self, method: str, args: dict | None = None):
        if not self.connected:
            raise RuntimeError("disconnected")
        self.calls.append((method, args or {}))

    def sequenced(self):
        return [
            (method, args["seq"])
            for method, args in self.calls
            if isinstance(args.get("seq"), int)
        ]


# ---------------------------------------------------------------------------
# Server-side: stamping + resume + ack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_stamps_replay_envelope():
    service = build_service()
    sess = service.get_or_create_session("sess-stamp")
    channel = RecordingChannel()
    sess.channels.append(channel)

    await service._broadcast_session(
        sess, "run_started", {"run_id": "r1", "prompt": "hi"}
    )
    await service._broadcast_session(
        sess, "message_output", {"run_id": "r1", "content": "hello"}
    )

    assert channel.sequenced() == [("run_started", 1), ("message_output", 2)]
    method, args = channel.calls[0]
    assert args["session_id"] == sess.id
    assert args["stream_id"] == sess.event_log.stream_id


@pytest.mark.asyncio
async def test_disconnect_reconnect_recovers_from_last_acknowledged_event():
    service = build_service()
    sess = service.get_or_create_session("sess-reconnect")

    tab = RecordingChannel()
    service.channel = tab
    sess.channels.append(tab)

    await service._broadcast_session(sess, "run_started", {"run_id": "r1"})
    await service._broadcast_session(
        sess, "message_output", {"run_id": "r1", "content": "one"}
    )
    ack = await service.ack_events(sess.id, sess.event_log.stream_id, 2)
    assert ack["acknowledged_seq"] == 2

    # The tab drops; the run keeps producing events.
    tab.connected = False
    await service._broadcast_session(
        sess, "tool_called", {"run_id": "r1", "tool": "t", "input": "", "call_id": "c"}
    )
    await service._broadcast_session(
        sess,
        "tool_result",
        {"run_id": "r1", "tool": "t", "output": "ok", "call_id": "c"},
    )
    await service._broadcast_session(
        sess, "run_end", {"run_id": "r1", "end_reason": "completed"}
    )
    assert tab not in sess.channels  # dead channel culled

    # Reconnect (new channel) and resume from the last acknowledged seq.
    fresh = RecordingChannel()
    service.channel = fresh
    result = await service.resume_session(sess.id, sess.event_log.stream_id, 2)
    replayed = [(e["method"], e["params"]["seq"]) for e in result["events"]]
    assert replayed == [("tool_called", 3), ("tool_result", 4), ("run_end", 5)]
    assert result["last_seq"] == 5
    assert result["stream_id"] == sess.event_log.stream_id
    # The resuming channel is registered for live events again.
    assert fresh in sess.channels


@pytest.mark.asyncio
async def test_resume_below_compaction_floor_raises_resync_required(monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_EVENT_LOG_MAX_EVENTS", "2")
    service = build_service()
    sess = service.get_or_create_session("sess-resync")
    for index in range(5):
        await service._broadcast_session(
            sess, "message_output", {"run_id": "r1", "content": str(index)}
        )
    with pytest.raises(ResyncRequiredError) as excinfo:
        await service.resume_session(sess.id, sess.event_log.stream_id, 1)
    assert excinfo.value.data["kind"] == "resync_required"
    assert excinfo.value.data["retained_last_seq"] == 5
    # An unknown stream epoch is also an explicit resync.
    with pytest.raises(ResyncRequiredError):
        await service.resume_session(sess.id, "stale-stream", 4)
    # A cursor at the floor still resumes.
    result = await service.resume_session(sess.id, sess.event_log.stream_id, 3)
    assert [e["params"]["seq"] for e in result["events"]] == [4, 5]


@pytest.mark.asyncio
async def test_multi_tab_clients_converge_and_gate_compaction():
    service = build_service()
    sess = service.get_or_create_session("sess-tabs")
    tab_a, tab_b = RecordingChannel(), RecordingChannel()
    sess.channels.extend([tab_a, tab_b])

    for index in range(3):
        await service._broadcast_session(
            sess, "message_output", {"run_id": "r1", "content": str(index)}
        )

    # Both tabs saw the identical sequenced stream.
    assert tab_a.sequenced() == tab_b.sequenced()

    # Tab A acks everything, tab B lags: nothing may compact past B.
    service.channel = tab_a
    await service.resume_session(sess.id, sess.event_log.stream_id, 3)
    await service.ack_events(sess.id, sess.event_log.stream_id, 3)
    service.channel = tab_b
    await service.resume_session(sess.id, sess.event_log.stream_id, 1)
    assert sess.event_log.first_retained_seq == 2

    # B catches up via resume + ack; the journal compacts fully.
    result = await service.resume_session(sess.id, sess.event_log.stream_id, 1)
    assert [e["params"]["seq"] for e in result["events"]] == [2, 3]
    await service.ack_events(sess.id, sess.event_log.stream_id, 3)
    assert sess.event_log.retained_events() == []


@pytest.mark.asyncio
async def test_server_restart_recovery_via_durable_journal():
    service = build_service()
    sess = service.get_or_create_session("sess-restart")
    await service._broadcast_session(sess, "run_started", {"run_id": "r1"})
    await service._broadcast_session(
        sess,
        "tool_approval_requested",
        {"call_id": "c1", "tool_name": "t", "arguments": ""},
    )
    stream_id = sess.event_log.stream_id

    # "Restart": a brand-new service (fresh SessionManager) over the same
    # durable stores. The session reloads its journal from disk.
    revived = build_service()
    result = await revived.resume_session("sess-restart", stream_id, 2)
    assert result["stream_id"] == stream_id
    replayed = [(e["method"], e["params"]) for e in result["events"]]
    methods = [m for m, _ in replayed]
    # Interrupted prompts and the run are closed out by synthetic events.
    assert "tool_approval_resolved" in methods
    assert "run_end" in methods
    synthetic = dict(replayed)
    assert synthetic["run_end"]["end_reason"] == "server_restart"
    assert synthetic["tool_approval_resolved"]["reason"] == "server_restart"
    # Sequencing continued monotonically across the restart.
    revived_sess = revived.get_or_create_session("sess-restart")
    assert revived_sess.event_log.last_seq == 4


@pytest.mark.asyncio
async def test_pending_approval_replay_carries_replay_envelope():
    """The legacy pending-approval replay path stays intact and its stored
    payloads carry seq/stream_id so sequence-aware clients dedupe them."""
    service = build_service()
    sess = service.get_or_create_session("sess-approval")
    channel = RecordingChannel()
    sess.channels.append(channel)

    task = asyncio.create_task(
        service.request_tool_approval(
            session=sess,
            call_id="call-1",
            tool_name="dangerous",
            arguments="{}",
            run_id="r1",
        )
    )
    await asyncio.sleep(0.05)
    stored = sess.pending_tool_approvals["call-1"]
    assert stored["seq"] == 1
    assert stored["stream_id"] == sess.event_log.stream_id
    # Replaying the stored payload through a cursor is a duplicate.
    cursor = ReplayCursor()
    live = channel.calls[0][1]
    assert cursor.observe(live) == "deliver"
    assert cursor.observe(stored) == "duplicate"

    assert await service.tool_approval_response("call-1", "reject")
    assert (await task)["decision"] == "reject"


@pytest.mark.asyncio
async def test_client_request_resolution_is_journaled():
    service = build_service()
    sess = service.get_or_create_session("sess-clientreq")
    channel = RecordingChannel()
    sess.channels.append(channel)

    task = asyncio.create_task(
        service.request_client_function(
            "ui.ask", {"q": "?"}, session=sess, expect_result=True
        )
    )
    await asyncio.sleep(0.05)
    request_id = channel.calls[0][1]["request_id"]
    assert sess.pending_client_requests[request_id]["seq"] == 1

    await service.client_response(request_id, True, result={"a": 1})
    assert (await task)["ok"] is True
    assert sess.pending_client_requests == {}
    methods = [m for _, m, _ in sess.event_log.retained_events()]
    assert methods == ["client_request", "client_request_resolved"]


@pytest.mark.asyncio
async def test_timed_out_client_request_is_resolved_in_journal():
    """Regression: a client_request that times out must journal a
    ``client_request_resolved`` so resumes within the retention window
    never replay a dangling prompt that no live future backs."""
    service = build_service()
    sess = service.get_or_create_session("sess-clientreq-timeout")
    channel = RecordingChannel()
    sess.channels.append(channel)

    result = await service.request_client_function(
        "ui.ask", {"q": "?"}, session=sess, expect_result=True, timeout_ms=20
    )
    assert result is None
    assert sess.pending_client_requests == {}
    events = [
        (method, payload) for _, method, payload in sess.event_log.retained_events()
    ]
    assert [method for method, _ in events] == [
        "client_request",
        "client_request_resolved",
    ]
    request_payload, resolved_payload = events[0][1], events[1][1]
    assert resolved_payload["reason"] == "timeout"
    assert resolved_payload["request_id"] == request_payload["request_id"]
    # Live channels received the dismissal too.
    assert ("client_request_resolved", 2) in channel.sequenced()


@pytest.mark.asyncio
async def test_bridge_cull_drops_replay_consumer_so_compaction_proceeds():
    """Regression: a channel culled on the bridge broadcast path must not
    pin the minimum-cursor floor and stall ack-driven compaction."""
    from omniagents.core.runtime.bridge import _broadcast

    service = build_service()
    sess = service.get_or_create_session("sess-bridge-cull")
    dead, live = RecordingChannel(), RecordingChannel()
    sess.channels.extend([dead, live])

    await service._broadcast_session(
        sess, "message_output", {"run_id": "r1", "content": "a"}
    )
    # Both tabs are replay consumers acknowledged through seq 1.
    service.channel = dead
    await service.ack_events(sess.id, sess.event_log.stream_id, 1)
    service.channel = live
    await service.ack_events(sess.id, sess.event_log.stream_id, 1)

    # The dead tab drops and is culled by the *bridge* broadcast path.
    dead.connected = False
    await _broadcast(sess, "message_output", {"run_id": "r1", "content": "b"})
    assert dead not in sess.channels

    # The remaining tab's ack now compacts everything: the dead tab's
    # stale cursor no longer gates the floor.
    await service.ack_events(sess.id, sess.event_log.stream_id, 2)
    assert sess.event_log.retained_events() == []


# ---------------------------------------------------------------------------
# Endpoint: structured -32030 error mapping
# ---------------------------------------------------------------------------


class _FakeTransport:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_endpoint_maps_resync_required_to_structured_error():
    class Methods(RpcMethodsBase):
        async def resume_session(self, session_id: str, after_seq: int = 0):
            raise ResyncRequiredError(
                session_id=session_id,
                stream_id="stream-1",
                retained_first_seq=10,
                retained_last_seq=20,
            )

    endpoint = JsonRpcEndpoint(Methods())
    transport = _FakeTransport()
    channel = JsonRpcChannel(transport)
    await endpoint._process_message(
        channel,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resume_session",
            "params": {"session_id": "s", "after_seq": 3},
        },
    )
    assert len(transport.sent) == 1
    error = transport.sent[0]["error"]
    assert error["code"] == -32030
    assert error["message"] == "Resync required"
    assert error["data"] == {
        "kind": "resync_required",
        "session_id": "s",
        "stream_id": "stream-1",
        "retained_first_seq": 10,
        "retained_last_seq": 20,
    }


# ---------------------------------------------------------------------------
# Reference client: dedup + gap detection
# ---------------------------------------------------------------------------


def _payload(seq, stream_id="stream-1", **extra):
    return {"session_id": "s", "seq": seq, "stream_id": stream_id, **extra}


def test_replay_cursor_dedupes_and_detects_gaps():
    cursor = ReplayCursor()
    assert cursor.observe(_payload(1)) == "deliver"
    assert cursor.observe(_payload(2)) == "deliver"
    assert cursor.observe(_payload(2)) == "duplicate"
    assert cursor.observe(_payload(1)) == "duplicate"
    assert cursor.observe(_payload(5)) == "gap"
    assert cursor.last_seq == 2  # gap does not advance the cursor
    assert cursor.gap_count == 1
    # Transient events (no seq) always deliver and never move the cursor.
    assert cursor.observe({"session_id": "s", "delta": {}}) == "deliver"
    assert cursor.last_seq == 2


def test_replay_cursor_adopts_stream_mid_flight_and_flags_epoch_change():
    cursor = ReplayCursor()
    # First observed event may start anywhere in the stream.
    assert cursor.observe(_payload(41)) == "deliver"
    assert cursor.observe(_payload(42)) == "deliver"
    # A new stream epoch means the journal was reset: full resync needed.
    assert cursor.observe(_payload(1, stream_id="stream-2")) == "stream_changed"
    assert cursor.stream_id == "stream-2"
    assert cursor.observe(_payload(2, stream_id="stream-2")) == "deliver"


def test_replay_cursor_apply_resume_skips_applied_and_adopts_last_seq():
    cursor = ReplayCursor()
    cursor.observe(_payload(1))
    cursor.observe(_payload(2))
    result = {
        "stream_id": "stream-1",
        "last_seq": 6,
        "events": [
            {"method": "message_output", "params": _payload(2)},
            {"method": "tool_called", "params": _payload(3)},
            # Non-contiguous inside the window (compacted snapshot) is fine.
            {"method": "run_end", "params": _payload(5)},
        ],
    }
    delivered = cursor.apply_resume(result)
    assert [params["seq"] for _, params in delivered] == [3, 5]
    assert cursor.last_seq == 6


@pytest.mark.asyncio
async def test_remote_agent_event_methods_drop_duplicate_deliveries():
    queue = asyncio.Queue()
    methods = _ClientEventMethods(queue)
    event = {
        "run_id": "r1",
        "session_id": "s",
        "content": "hello",
        "seq": 1,
        "stream_id": "stream-1",
    }
    await methods.message_output(**event)
    await methods.message_output(**event)  # duplicate redelivery
    await methods.run_end(
        run_id="r1",
        session_id="s",
        end_reason="completed",
        seq=2,
        stream_id="stream-1",
    )
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    assert [item["method"] for item in items] == ["message_output", "run_end"]
    assert methods.cursor_for("s").last_seq == 2


@pytest.mark.asyncio
async def test_remote_agent_event_methods_count_gaps_but_deliver():
    queue = asyncio.Queue()
    methods = _ClientEventMethods(queue)
    await methods.message_output(
        run_id="r1", session_id="s", content="a", seq=1, stream_id="stream-1"
    )
    await methods.message_output(
        run_id="r1", session_id="s", content="d", seq=4, stream_id="stream-1"
    )
    cursor = methods.cursor_for("s")
    assert cursor.gap_count == 1
    assert cursor.last_seq == 4  # force-advanced after delivering anyway
    assert queue.qsize() == 2


def _drain_seqs(queue: asyncio.Queue) -> list[int]:
    seqs = []
    while not queue.empty():
        seqs.append(queue.get_nowait()["params"]["seq"])
    return seqs


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_remote_client_gap_backfills_missed_window_exactly_once():
    """Regression: a live event arriving after a disconnect window must not
    force the cursor past the missed events. With a resume callable wired,
    the coordinator backfills 6-8 via resume_session and delivers the
    gap-triggering event 9 exactly once."""
    queue = asyncio.Queue()
    resume_calls = []

    async def resume(session_id, stream_id, after_seq):
        resume_calls.append((session_id, stream_id, after_seq))
        return {
            "stream_id": "stream-1",
            "last_seq": 9,
            "events": [
                {
                    "method": "message_output",
                    "params": _payload(seq, run_id="r1", content=f"m{seq}"),
                }
                for seq in (6, 7, 8, 9)
            ],
        }

    methods = _ClientEventMethods(queue, resume=resume)
    for seq in range(1, 6):
        await methods.message_output(
            run_id="r1",
            session_id="s",
            content=f"m{seq}",
            seq=seq,
            stream_id="stream-1",
        )
    # The missed window is 6-8; live delivery resumes at 9.
    await methods.message_output(
        run_id="r1", session_id="s", content="m9", seq=9, stream_id="stream-1"
    )
    await _settle()

    assert _drain_seqs(queue) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert resume_calls == [("s", "stream-1", 5)]
    assert methods.cursor_for("s").last_seq == 9


@pytest.mark.asyncio
async def test_remote_client_buffers_live_events_during_backfill():
    """Live events arriving while a backfill is in flight are held and
    flushed (deduplicated) after the resume result is applied."""
    queue = asyncio.Queue()
    release = asyncio.Event()

    async def resume(session_id, stream_id, after_seq):
        await release.wait()
        return {
            "stream_id": "stream-1",
            "last_seq": 7,
            "events": [
                {
                    "method": "message_output",
                    "params": _payload(seq, run_id="r1", content=f"m{seq}"),
                }
                for seq in (6, 7)
            ],
        }

    methods = _ClientEventMethods(queue, resume=resume)
    for seq in range(1, 6):
        await methods.message_output(
            run_id="r1",
            session_id="s",
            content=f"m{seq}",
            seq=seq,
            stream_id="stream-1",
        )
    # Gap: 6 was missed; 7 triggers a (held) backfill.
    await methods.message_output(
        run_id="r1", session_id="s", content="m7", seq=7, stream_id="stream-1"
    )
    await asyncio.sleep(0)
    # A live event lands mid-backfill: held, not delivered out of order.
    await methods.message_output(
        run_id="r1", session_id="s", content="m8", seq=8, stream_id="stream-1"
    )
    assert _drain_seqs(queue) == [1, 2, 3, 4, 5]

    release.set()
    await _settle()
    assert _drain_seqs(queue) == [6, 7, 8]
    assert methods.cursor_for("s").last_seq == 8


@pytest.mark.asyncio
async def test_remote_client_degrades_to_deliver_anyway_when_backfill_fails():
    """A failed resume surfaces resync-required and flushes held events
    with the cursor force-advanced so they cannot re-trigger a loop."""
    queue = asyncio.Queue()
    resyncs = []

    async def resume(session_id, stream_id, after_seq):
        raise RuntimeError("retention window gone")

    methods = _ClientEventMethods(queue, resume=resume)
    methods.coordinator.on_resync_required = resyncs.append
    for seq in (1, 2):
        await methods.message_output(
            run_id="r1",
            session_id="s",
            content=f"m{seq}",
            seq=seq,
            stream_id="stream-1",
        )
    await methods.message_output(
        run_id="r1", session_id="s", content="m9", seq=9, stream_id="stream-1"
    )
    await _settle()

    assert _drain_seqs(queue) == [1, 2, 9]
    assert resyncs == ["s"]
    assert methods.cursor_for("s").last_seq == 9


@pytest.mark.asyncio
async def test_remote_client_reconnect_backfill_requests_missed_tail():
    """``request_backfill`` (the lifecycle reconnect hook) proactively
    fetches events emitted while the client was away — no live-event gap
    trigger is needed — and is a no-op without an established cursor or
    while a backfill is already in flight."""
    queue = asyncio.Queue()

    async def resume(session_id, stream_id, after_seq):
        return {
            "stream_id": "stream-1",
            "last_seq": 5,
            "events": [
                {
                    "method": "message_output",
                    "params": _payload(seq, run_id="r1", content=f"m{seq}"),
                }
                for seq in (4, 5)
            ],
        }

    methods = _ClientEventMethods(queue, resume=resume)
    # No cursor yet: nothing to backfill.
    assert methods.coordinator.request_backfill("s") is False
    for seq in (1, 2, 3):
        await methods.message_output(
            run_id="r1",
            session_id="s",
            content=f"m{seq}",
            seq=seq,
            stream_id="stream-1",
        )
    # Reconnect: events 4-5 fired while we were away.
    assert methods.coordinator.request_backfill("s") is True
    # Single-flight: a second request while one is in flight is refused.
    assert methods.coordinator.request_backfill("s") is False
    await _settle()

    assert _drain_seqs(queue) == [1, 2, 3, 4, 5]
    assert methods.cursor_for("s").last_seq == 5
