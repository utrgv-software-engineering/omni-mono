import asyncio
from types import SimpleNamespace

import pytest

from omniagents.core.runtime.release_gate import ReleaseGate, admission
from omniagents.integrations.events import IncomingEvent
from omniagents.integrations.store import EventStore


def test_idle_lease_blocks_admissions_and_expires():
    now = [10]
    busy = [1]
    gate = ReleaseGate(lambda: busy[0], clock=lambda: now[0])
    with pytest.raises(RuntimeError):
        gate.acquire()
    busy[0] = 0
    token = gate.acquire()
    with pytest.raises(ValueError):
        gate.release("wrong-owner")

    @admission
    async def run(service):
        return "started"

    service = SimpleNamespace(_release_gate=gate)
    with pytest.raises(RuntimeError, match="activating"):
        asyncio.run(run(service))
    now[0] += 121
    assert asyncio.run(run(service)) == "started"
    token = gate.acquire()
    gate.release(token)
    assert not gate.paused


def test_admission_reservation_closes_the_idle_check_race():
    gate = ReleaseGate(lambda: 0)

    @admission
    async def run(service):
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError):
            gate.acquire()
        raise ValueError("failed preparation")

    with pytest.raises(ValueError):
        asyncio.run(run(SimpleNamespace(_release_gate=gate)))
    assert gate.admissions == 0
    assert gate.acquire()


def test_received_event_survives_restart_without_replaying_dispatched_event(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    first = IncomingEvent("github", "pull_request_opened", "one", {"number": 1})
    second = IncomingEvent("github", "pull_request_opened", "two", {"number": 2})
    one = store.record(first)
    two = store.record(second)
    store.set_status(one, "dispatched")
    store.close()
    reopened = EventStore(path)
    assert reopened.received_events() == [(two, second)]
    reopened.close()
