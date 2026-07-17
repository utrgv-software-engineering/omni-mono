"""Tests for the idle auto-recap scheduler's three rules:
  1. idle >= IDLE_THRESHOLD_S since last completed turn
  2. >= MIN_TURNS completed
  3. never twice in a row (re-arm only on fresh activity)

The thresholds are fixed module constants (not configurable), so tests
monkeypatch them rather than injecting via the constructor. We call the
private `_should_fire` predicate directly for the rule logic, and exercise
the run_end listener + watcher wiring for the end-to-end re-arm behavior.
No real agent / LLM.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import omni_code.recap_scheduler as recap_scheduler
from omni_code.recap_scheduler import RecapScheduler, _RecapState


class _FakeSession:
    def __init__(self, sid: str):
        self.id = sid
        self.current_task = None
        self.pending_notifications = []


class _FakeSessionManager:
    def __init__(self):
        self._by_id = {}


class _FakeService:
    def __init__(self):
        self._session_manager = _FakeSessionManager()
        self._listeners = {}

    def active_sessions(self):
        return list(self._session_manager._by_id.values())

    def add_run_end_listener(self, session_id, cb):
        self._listeners.setdefault(session_id, []).append(cb)

    def remove_run_end_listener(self, session_id, cb):
        lst = self._listeners.get(session_id)
        if lst and cb in lst:
            lst.remove(cb)

    async def fire_run_end(self, session_id, payload=None):
        for cb in list(self._listeners.get(session_id, [])):
            await cb(payload or {})


def _add_session(svc: _FakeService, sid: str) -> _FakeSession:
    sess = _FakeSession(sid)
    svc._session_manager._by_id[sid] = sess
    return sess


# ---------------------------------------------------------------------------
# Rule predicate: _should_fire
# ---------------------------------------------------------------------------


def _ready_state() -> _RecapState:
    """A state that satisfies all three rules against the fixed constants."""
    st = _RecapState()
    st.completed_turns = recap_scheduler.MIN_TURNS
    st.last_turn_end_at = time.monotonic() - (recap_scheduler.IDLE_THRESHOLD_S + 5)
    st.armed = True
    return st


def test_should_fire_when_all_rules_met():
    sched = RecapScheduler(_FakeService())
    assert sched._should_fire(_FakeSession("s1"), _ready_state()) is True


def test_rule1_not_idle_long_enough():
    sched = RecapScheduler(_FakeService())
    st = _ready_state()
    st.last_turn_end_at = time.monotonic() - 10  # only 10s idle
    assert sched._should_fire(_FakeSession("s1"), st) is False


def test_rule2_too_few_turns():
    sched = RecapScheduler(_FakeService())
    st = _ready_state()
    st.completed_turns = recap_scheduler.MIN_TURNS - 1  # below min
    assert sched._should_fire(_FakeSession("s1"), st) is False


def test_rule3_not_armed():
    sched = RecapScheduler(_FakeService())
    st = _ready_state()
    st.armed = False  # already recapped, no fresh activity
    assert sched._should_fire(_FakeSession("s1"), st) is False


def test_skips_when_run_active():
    sched = RecapScheduler(_FakeService())
    sess = _FakeSession("s1")

    async def _check():
        sess.current_task = asyncio.create_task(asyncio.sleep(60))
        try:
            return sched._should_fire(sess, _ready_state())
        finally:
            sess.current_task.cancel()

    assert asyncio.run(_check()) is False


def test_skips_when_notifications_pending():
    sched = RecapScheduler(_FakeService())
    sess = _FakeSession("s1")
    sess.pending_notifications = [{"x": 1}]
    assert sched._should_fire(sess, _ready_state()) is False


def test_firing_flag_blocks_double_fire():
    sched = RecapScheduler(_FakeService())
    st = _ready_state()
    st.firing = True
    assert sched._should_fire(_FakeSession("s1"), st) is False


# ---------------------------------------------------------------------------
# run_end listener: turn counting + re-arm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_end_increments_turns_and_arms():
    svc = _FakeService()
    sched = RecapScheduler(svc)
    _add_session(svc, "s1")
    sched._instrument("s1")

    st = sched._state["s1"]
    assert st.completed_turns == 0
    assert st.armed is False

    await svc.fire_run_end("s1")
    await svc.fire_run_end("s1")
    assert st.completed_turns == 2
    assert st.armed is True


@pytest.mark.asyncio
async def test_rearm_after_fire_requires_fresh_turn():
    """Rule 3 end-to-end: after a recap disarms the session, only a new
    run_end re-arms it."""
    svc = _FakeService()
    sched = RecapScheduler(svc)
    _add_session(svc, "s1")
    sched._instrument("s1")
    sess = svc._session_manager._by_id["s1"]
    backdate = recap_scheduler.IDLE_THRESHOLD_S + 60

    # MIN_TURNS turns → armed, enough turns. Backdate so the idle threshold
    # is satisfied (run_end stamps "now", not in the past).
    for _ in range(recap_scheduler.MIN_TURNS):
        await svc.fire_run_end("s1")
    st = sched._state["s1"]
    st.last_turn_end_at = time.monotonic() - backdate
    assert st.armed is True
    assert sched._should_fire(sess, st) is True

    # Simulate a fire: disarm.
    st.armed = False
    assert sched._should_fire(sess, st) is False

    # Fresh activity re-arms — but the new run_end stamps "now", so the
    # session is no longer idle; re-backdate to isolate the re-arm check.
    await svc.fire_run_end("s1")
    st.last_turn_end_at = time.monotonic() - backdate
    assert st.armed is True
    assert sched._should_fire(sess, st) is True


@pytest.mark.asyncio
async def test_instrument_is_idempotent():
    svc = _FakeService()
    sched = RecapScheduler(svc)
    _add_session(svc, "s1")
    sched._instrument("s1")
    sched._instrument("s1")
    # Only one listener registered despite two instrument calls.
    assert len(svc._listeners["s1"]) == 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_detaches_listeners():
    svc = _FakeService()
    sched = RecapScheduler(svc)
    _add_session(svc, "s1")
    sched._instrument("s1")
    assert svc._listeners.get("s1")
    await sched.stop()
    assert not svc._listeners.get("s1")
    assert sched._state == {}


@pytest.mark.asyncio
async def test_always_starts_a_watcher():
    """No disable knob — start() always arms the watcher."""
    sched = RecapScheduler(_FakeService())
    await sched.start()
    assert sched._watcher is not None
    await sched.stop()


@pytest.mark.asyncio
async def test_watcher_fires_recap_end_to_end(monkeypatch):
    """Full path: instrument on first tick, run_end arms, threshold elapses,
    watcher fires generate_and_broadcast_recap exactly once. Constants are
    monkeypatched down since they're fixed (not constructor-injectable)."""
    monkeypatch.setattr("omni_code.recap_scheduler._WATCHER_INTERVAL_S", 0.02)
    monkeypatch.setattr("omni_code.recap_scheduler.IDLE_THRESHOLD_S", 0.05)
    monkeypatch.setattr("omni_code.recap_scheduler.MIN_TURNS", 1)
    fired: list[str] = []

    async def _fake_recap(service, session):
        fired.append(session.id)
        return "recap text"

    import server_functions.recap as recap_mod

    monkeypatch.setattr(recap_mod, "generate_and_broadcast_recap", _fake_recap)

    svc = _FakeService()
    sched = RecapScheduler(svc)
    _add_session(svc, "s1")
    await sched.start()

    # First tick instruments; then fire a run_end to arm + count a turn.
    await asyncio.sleep(0.05)
    await svc.fire_run_end("s1")
    # Backdate so the idle threshold is satisfied immediately.
    sched._state["s1"].last_turn_end_at = time.monotonic() - 1.0

    for _ in range(40):
        await asyncio.sleep(0.01)
        if fired:
            break
    await sched.stop()
    assert fired == ["s1"]
