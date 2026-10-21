"""Unit tests for the structured user-elicitation lane.

Covers the acceptance criteria at the service level:
- first response wins, duplicates rejected with a typed error,
- pending prompts replay on reconnect (journal + live-state paths),
- pending prompts clear on run termination with journaled resolutions,
- expiry journals a terminal resolution,
- server-restart recovery closes orphaned prompts,
- responses are persisted into the journal only when the request's
  ``persist_response`` flag permits,
- response values are validated against the request contract.
"""

import asyncio

import pytest

from omniagents.execution import AgentHostResources
from omniagents.core.agents.elicitation import (
    ElicitationAlreadyResolved,
    ElicitationInvalidResponse,
    ElicitationNotFound,
    validate_response_value,
)
from omniagents.core.agents.service import AgentService

from ..core.test_agent_builder import make_spec


def build_service() -> AgentService:
    return AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-rpc-elicitation-host"),
    )


class RecordingChannel:
    def __init__(self):
        self.calls = []
        self.connected = True

    async def async_call(self, method: str, args: dict | None = None):
        if not self.connected:
            raise RuntimeError("disconnected")
        self.calls.append((method, args or {}))

    def methods(self):
        return [method for method, _ in self.calls]

    def last(self, method: str) -> dict:
        for meth, args in reversed(self.calls):
            if meth == method:
                return args
        raise AssertionError(f"no {method} delivered: {self.methods()}")


def journal_methods(sess):
    return [method for _, method, _ in sess.event_log.retained_events()]


@pytest.mark.asyncio
async def test_first_response_wins_and_duplicate_gets_typed_rejection():
    service = build_service()
    sess = service.get_or_create_session("sess-elicit-first")
    tab_a, tab_b = RecordingChannel(), RecordingChannel()
    sess.channels.extend([tab_a, tab_b])

    task = asyncio.create_task(
        service.request_elicitation(
            session=sess, kind="confirm", message="Proceed?", persist_response=True
        )
    )
    await asyncio.sleep(0.05)

    # Both tabs received the prompt with the replay envelope stamped.
    for tab in (tab_a, tab_b):
        prompt = tab.last("elicitation_requested")
        assert prompt["kind"] == "confirm"
        assert prompt["seq"] == 1
        assert prompt["stream_id"] == sess.event_log.stream_id
    elicitation_id = tab_a.last("elicitation_requested")["elicitation_id"]

    result = await service.elicitation_response(
        elicitation_id, "accept", value={"confirmed": True}
    )
    assert result == {"elicitation_id": elicitation_id, "status": "accepted"}
    outcome = await task
    assert outcome["action"] == "accept"
    assert outcome["value"] == {"confirmed": True}

    # Both tabs saw the resolution; the pending maps are clean.
    for tab in (tab_a, tab_b):
        resolved = tab.last("elicitation_resolved")
        assert resolved["status"] == "accepted"
    assert sess.pending_elicitations == {}

    # The second tab's late answer is rejected predictably.
    with pytest.raises(ElicitationAlreadyResolved) as excinfo:
        await service.elicitation_response(
            elicitation_id, "accept", value={"confirmed": False}
        )
    assert excinfo.value.code == -32051
    assert excinfo.value.data == {
        "kind": "elicitation_already_resolved",
        "elicitation_id": elicitation_id,
        "status": "accepted",
    }


@pytest.mark.asyncio
async def test_unknown_elicitation_gets_typed_not_found():
    service = build_service()
    service.get_or_create_session("sess-elicit-unknown")
    with pytest.raises(ElicitationNotFound) as excinfo:
        await service.elicitation_response("elc_missing", "accept", value={})
    assert excinfo.value.code == -32050
    assert excinfo.value.data["kind"] == "elicitation_not_found"


@pytest.mark.asyncio
async def test_invalid_response_value_is_typed_and_leaves_prompt_pending():
    service = build_service()
    sess = service.get_or_create_session("sess-elicit-invalid")
    tab = RecordingChannel()
    sess.channels.append(tab)

    task = asyncio.create_task(
        service.request_elicitation(
            session=sess,
            kind="select",
            message="Pick one",
            options=[{"value": "red"}, {"value": "blue"}],
        )
    )
    await asyncio.sleep(0.05)
    elicitation_id = tab.last("elicitation_requested")["elicitation_id"]

    with pytest.raises(ElicitationInvalidResponse) as excinfo:
        await service.elicitation_response(
            elicitation_id, "accept", value={"selected": ["green"]}
        )
    assert excinfo.value.code == -32052
    assert excinfo.value.data["kind"] == "elicitation_invalid_response"
    # The prompt is still pending: a valid response can follow.
    assert elicitation_id in sess.pending_elicitations
    await service.elicitation_response(
        elicitation_id, "accept", value={"selected": ["blue"]}
    )
    assert (await task)["value"] == {"selected": ["blue"]}


@pytest.mark.asyncio
async def test_expiry_journals_terminal_resolution():
    service = build_service()
    sess = service.get_or_create_session("sess-elicit-expiry")
    tab = RecordingChannel()
    sess.channels.append(tab)

    outcome = await service.request_elicitation(
        session=sess, kind="question", message="Quick!", timeout_ms=20
    )
    assert outcome["action"] == "expire"
    assert outcome["status"] == "expired"
    assert sess.pending_elicitations == {}
    assert journal_methods(sess) == ["elicitation_requested", "elicitation_resolved"]
    resolved = tab.last("elicitation_resolved")
    assert resolved["status"] == "expired"
    # Late answers after expiry are duplicates, not resurrections.
    elicitation_id = resolved["elicitation_id"]
    with pytest.raises(ElicitationAlreadyResolved):
        await service.elicitation_response(
            elicitation_id, "accept", value={"text": "too late"}
        )


@pytest.mark.asyncio
async def test_pending_elicitation_replays_on_reconnect():
    service = build_service()
    sess = service.get_or_create_session("sess-elicit-replay")
    tab = RecordingChannel()
    sess.channels.append(tab)

    task = asyncio.create_task(
        service.request_elicitation(session=sess, kind="question", message="Name?")
    )
    await asyncio.sleep(0.05)
    stored = sess.pending_elicitations[
        tab.last("elicitation_requested")["elicitation_id"]
    ]
    assert stored["seq"] == 1  # stored copy carries the replay envelope

    # Journal replay (resume_session): the unresolved prompt is retained.
    fresh = RecordingChannel()
    service.channel = fresh
    result = await service.resume_session(sess.id, sess.event_log.stream_id, 0)
    replayed = [e["method"] for e in result["events"]]
    assert replayed == ["elicitation_requested"]

    # Live-state replay (get_session_history path) re-sends the prompt.
    live = RecordingChannel()
    await service._replay_live_state(sess, live)
    assert live.methods() == ["elicitation_requested"]

    await service.elicitation_response(
        stored["elicitation_id"], "decline", reason="not now"
    )
    outcome = await task
    assert outcome["action"] == "decline"
    assert outcome["reason"] == "not now"


@pytest.mark.asyncio
async def test_run_end_clears_pending_elicitations_with_journaled_resolution():
    service = build_service()
    sess = service.get_or_create_session("sess-elicit-runend")
    tab = RecordingChannel()
    sess.channels.append(tab)

    task = asyncio.create_task(
        service.request_elicitation(session=sess, kind="confirm", message="Sure?")
    )
    await asyncio.sleep(0.05)
    assert len(sess.pending_elicitations) == 1

    # The run-end chokepoint terminal-resolves every pending prompt.
    await service._resolve_session_elicitations(sess, status="cancelled")
    outcome = await task
    assert outcome["action"] == "cancel"
    assert outcome["status"] == "cancelled"
    assert sess.pending_elicitations == {}
    assert service._pending_elicitations == {}
    assert journal_methods(sess) == ["elicitation_requested", "elicitation_resolved"]
    assert tab.last("elicitation_resolved")["status"] == "cancelled"


@pytest.mark.asyncio
async def test_server_restart_closes_orphaned_elicitations():
    service = build_service()
    sess = service.get_or_create_session("sess-elicit-restart")
    tab = RecordingChannel()
    sess.channels.append(tab)
    asyncio.create_task(
        service.request_elicitation(session=sess, kind="question", message="Left open")
    )
    await asyncio.sleep(0.05)
    stream_id = sess.event_log.stream_id

    # "Restart": a brand-new service over the same durable journal.
    revived = build_service()
    result = await revived.resume_session("sess-elicit-restart", stream_id, 1)
    synthetic = {e["method"]: e["params"] for e in result["events"]}
    assert synthetic["elicitation_resolved"]["status"] == "server_restart"


@pytest.mark.asyncio
async def test_response_value_journaled_only_when_persistence_permitted():
    service = build_service()

    # persist_response=False (default): the journaled resolution carries
    # status/action but never the submitted value.
    sess = service.get_or_create_session("sess-elicit-sensitive")
    tab = RecordingChannel()
    sess.channels.append(tab)
    task = asyncio.create_task(
        service.request_elicitation(session=sess, kind="question", message="Token?")
    )
    await asyncio.sleep(0.05)
    elicitation_id = tab.last("elicitation_requested")["elicitation_id"]
    await service.elicitation_response(
        elicitation_id, "accept", value={"text": "s3cret"}
    )
    outcome = await task
    assert outcome["value"] == {"text": "s3cret"}  # in-process caller gets it
    events = {m: p for _, m, p in sess.event_log.retained_events()}
    assert "value" not in events["elicitation_resolved"]
    assert "s3cret" not in str(events)
    assert "value" not in tab.last("elicitation_resolved")

    # persist_response=True: the resolution (journal + broadcast) carries
    # the value for replaying tabs.
    sess2 = service.get_or_create_session("sess-elicit-persist")
    tab2 = RecordingChannel()
    sess2.channels.append(tab2)
    task2 = asyncio.create_task(
        service.request_elicitation(
            session=sess2, kind="question", message="Name?", persist_response=True
        )
    )
    await asyncio.sleep(0.05)
    await service.elicitation_response(
        tab2.last("elicitation_requested")["elicitation_id"],
        "accept",
        value={"text": "Ada"},
    )
    await task2
    events2 = {m: p for _, m, p in sess2.event_log.retained_events()}
    assert events2["elicitation_resolved"]["value"] == {"text": "Ada"}
    assert tab2.last("elicitation_resolved")["value"] == {"text": "Ada"}


@pytest.mark.asyncio
async def test_resolution_survives_waiter_side_cleanup_race(monkeypatch):
    """The waiter's ``finally`` cleanup must never swallow the resolution.

    ``_set_future_result`` may wake the requesting coroutine cross-loop
    (``call_soon_threadsafe``); on another thread its ``finally`` block can
    pop the pending maps before ``_resolve_elicitation`` runs. Simulate the
    waiter winning that race deterministically: the moment the future is
    resolved, both pending maps are already cleaned up. The resolution must
    still be journaled, broadcast, and registered for typed duplicate
    rejection — which holds only if ``elicitation_response`` resolves
    BEFORE it wakes the waiter.
    """
    from omniagents.core.agents import service as service_mod

    service = build_service()
    sess = service.get_or_create_session("sess-elicit-cleanup-race")
    tab = RecordingChannel()
    sess.channels.append(tab)

    task = asyncio.create_task(
        service.request_elicitation(session=sess, kind="question", message="Race?")
    )
    await asyncio.sleep(0.05)
    elicitation_id = tab.last("elicitation_requested")["elicitation_id"]

    real_set = service_mod._set_future_result

    def racing_set(fut, result):
        real_set(fut, result)
        # Emulate request_elicitation's ``finally`` having already run on
        # the waiter's loop by the next bytecode boundary.
        service._pending_elicitations.pop(elicitation_id, None)
        sess.pending_elicitations.pop(elicitation_id, None)

    monkeypatch.setattr(service_mod, "_set_future_result", racing_set)

    await service.elicitation_response(
        elicitation_id, "accept", value={"text": "still journaled"}
    )
    outcome = await asyncio.wait_for(task, timeout=2)
    assert outcome["action"] == "accept"

    # The terminal resolution reached the journal and every tab.
    assert journal_methods(sess) == ["elicitation_requested", "elicitation_resolved"]
    assert tab.last("elicitation_resolved")["status"] == "accepted"
    # Late duplicates still get the typed -32051, not not-found.
    with pytest.raises(ElicitationAlreadyResolved) as excinfo:
        await service.elicitation_response(
            elicitation_id, "accept", value={"text": "late"}
        )
    assert excinfo.value.data["status"] == "accepted"


def test_validate_response_value_per_kind_contracts():
    assert validate_response_value({"kind": "question"}, "decline", None) == []
    assert validate_response_value({"kind": "question"}, "cancel", None) == []
    assert validate_response_value({"kind": "bogus"}, "reply", None)  # bad action

    assert validate_response_value({"kind": "question"}, "accept", {"text": "hi"}) == []
    assert validate_response_value({"kind": "question"}, "accept", {"text": "  "})
    assert validate_response_value({"kind": "question"}, "accept", None)

    assert (
        validate_response_value({"kind": "confirm"}, "accept", {"confirmed": False})
        == []
    )
    assert validate_response_value({"kind": "confirm"}, "accept", {"confirmed": "y"})

    select = {"kind": "select", "options": ["a", {"value": "b", "label": "B"}]}
    assert validate_response_value(select, "accept", {"selected": ["a", "b"]}) == []
    assert validate_response_value(select, "accept", {"selected": ["c"]})
    assert validate_response_value(select, "accept", {"selected": []})
    # Open-record options (no comparable value) skip the subset check.
    open_select = {"kind": "select", "options": [{"label": "opaque"}]}
    assert (
        validate_response_value(open_select, "accept", {"selected": ["anything"]}) == []
    )

    form = {
        "kind": "form",
        "input_schema": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        },
    }
    assert validate_response_value(form, "accept", {"name": "Ada", "age": 36}) == []
    assert validate_response_value(form, "accept", {"age": "not-a-number"})
    assert validate_response_value(form, "accept", None)

    assert validate_response_value({"kind": "url"}, "accept", None) == []
    assert validate_response_value({"kind": "url"}, "accept", {"completed": True}) == []
