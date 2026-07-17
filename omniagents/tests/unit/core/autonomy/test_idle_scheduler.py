"""Tests for the idle-trigger scheduler's three rules:
  1. idle >= trigger.idle_seconds since last completed turn
  2. >= trigger.min_turns completed turns
  3. never twice in a row (re-arm only on fresh activity)

We call the private `_should_fire` predicate directly for the rule logic,
and exercise the run_end listener + watcher wiring for the end-to-end
re-arm behavior. No real agent / LLM.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omniagents.core.autonomy.idle import (
    IdleTrigger,
    IdleTriggerScheduler,
    _SessionState,
)

IDLE_S = 180.0
MIN_TURNS = 3


async def _noop_action(service, session):
    return None


def _trigger(**overrides) -> IdleTrigger:
    kwargs = dict(
        name="t", action=_noop_action, idle_seconds=IDLE_S, min_turns=MIN_TURNS
    )
    kwargs.update(overrides)
    return IdleTrigger(**kwargs)


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


def _sched(*triggers: IdleTrigger, svc=None, **kwargs) -> IdleTriggerScheduler:
    return IdleTriggerScheduler(
        svc or _FakeService(), list(triggers) or [_trigger()], **kwargs
    )


# ---------------------------------------------------------------------------
# Rule predicate: _should_fire
# ---------------------------------------------------------------------------


def _ready_state(trigger: IdleTrigger) -> _SessionState:
    """A state that satisfies all three rules for *trigger*."""
    st = _SessionState()
    st.completed_turns = trigger.min_turns
    st.last_turn_end_at = time.monotonic() - (trigger.idle_seconds + 5)
    st.trigger_state(trigger.name).armed = True
    return st


def test_should_fire_when_all_rules_met():
    t = _trigger()
    sched = _sched(t)
    st = _ready_state(t)
    assert (
        sched._should_fire(_FakeSession("s1"), st, t, st.trigger_state(t.name)) is True
    )


def test_rule1_not_idle_long_enough():
    t = _trigger()
    sched = _sched(t)
    st = _ready_state(t)
    st.last_turn_end_at = time.monotonic() - 10  # only 10s idle
    assert (
        sched._should_fire(_FakeSession("s1"), st, t, st.trigger_state(t.name)) is False
    )


def test_rule2_too_few_turns():
    t = _trigger()
    sched = _sched(t)
    st = _ready_state(t)
    st.completed_turns = t.min_turns - 1  # below min
    assert (
        sched._should_fire(_FakeSession("s1"), st, t, st.trigger_state(t.name)) is False
    )


def test_rule3_not_armed():
    t = _trigger()
    sched = _sched(t)
    st = _ready_state(t)
    st.trigger_state(t.name).armed = False  # already fired, no fresh activity
    assert (
        sched._should_fire(_FakeSession("s1"), st, t, st.trigger_state(t.name)) is False
    )


def test_skips_when_run_active():
    t = _trigger()
    sched = _sched(t)
    sess = _FakeSession("s1")

    async def _check():
        sess.current_task = asyncio.create_task(asyncio.sleep(60))
        try:
            st = _ready_state(t)
            return sched._should_fire(sess, st, t, st.trigger_state(t.name))
        finally:
            sess.current_task.cancel()

    assert asyncio.run(_check()) is False


def test_skips_when_notifications_pending():
    t = _trigger()
    sched = _sched(t)
    sess = _FakeSession("s1")
    sess.pending_notifications = [{"x": 1}]
    st = _ready_state(t)
    assert sched._should_fire(sess, st, t, st.trigger_state(t.name)) is False


def test_firing_flag_blocks_double_fire():
    t = _trigger()
    sched = _sched(t)
    st = _ready_state(t)
    st.trigger_state(t.name).firing = True
    assert (
        sched._should_fire(_FakeSession("s1"), st, t, st.trigger_state(t.name)) is False
    )


def test_triggers_have_independent_thresholds():
    fast = _trigger(name="fast", idle_seconds=5.0, min_turns=1)
    slow = _trigger(name="slow", idle_seconds=600.0, min_turns=1)
    sched = _sched(fast, slow)
    st = _SessionState()
    st.completed_turns = 1
    st.last_turn_end_at = time.monotonic() - 30
    st.trigger_state("fast").armed = True
    st.trigger_state("slow").armed = True
    sess = _FakeSession("s1")
    assert sched._should_fire(sess, st, fast, st.trigger_state("fast")) is True
    assert sched._should_fire(sess, st, slow, st.trigger_state("slow")) is False


def test_register_replaces_same_name():
    sched = _sched(_trigger(idle_seconds=100.0))
    sched.register(_trigger(idle_seconds=7.0))
    assert len(sched._triggers) == 1
    assert sched._triggers[0].idle_seconds == 7.0


# ---------------------------------------------------------------------------
# run_end listener: turn counting + re-arm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_end_increments_turns_and_arms():
    svc = _FakeService()
    t = _trigger()
    sched = _sched(t, svc=svc)
    _add_session(svc, "s1")
    sched._instrument("s1")

    st = sched._state["s1"]
    assert st.completed_turns == 0
    assert st.trigger_state(t.name).armed is False

    await svc.fire_run_end("s1")
    await svc.fire_run_end("s1")
    assert st.completed_turns == 2
    assert st.trigger_state(t.name).armed is True


@pytest.mark.asyncio
async def test_rearm_after_fire_requires_fresh_turn():
    """Rule 3 end-to-end: after a fire disarms the trigger, only a new
    run_end re-arms it."""
    svc = _FakeService()
    t = _trigger()
    sched = _sched(t, svc=svc)
    _add_session(svc, "s1")
    sched._instrument("s1")
    sess = svc._session_manager._by_id["s1"]
    backdate = t.idle_seconds + 60

    # min_turns turns → armed, enough turns. Backdate so the idle threshold
    # is satisfied (run_end stamps "now", not in the past).
    for _ in range(t.min_turns):
        await svc.fire_run_end("s1")
    st = sched._state["s1"]
    st.last_turn_end_at = time.monotonic() - backdate
    ts = st.trigger_state(t.name)
    assert ts.armed is True
    assert sched._should_fire(sess, st, t, ts) is True

    # Simulate a fire: disarm.
    ts.armed = False
    assert sched._should_fire(sess, st, t, ts) is False

    # Fresh activity re-arms — but the new run_end stamps "now", so the
    # session is no longer idle; re-backdate to isolate the re-arm check.
    await svc.fire_run_end("s1")
    st.last_turn_end_at = time.monotonic() - backdate
    assert ts.armed is True
    assert sched._should_fire(sess, st, t, ts) is True


@pytest.mark.asyncio
async def test_instrument_is_idempotent():
    svc = _FakeService()
    sched = _sched(svc=svc)
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
    sched = _sched(svc=svc)
    _add_session(svc, "s1")
    sched._instrument("s1")
    assert svc._listeners.get("s1")
    await sched.stop()
    assert not svc._listeners.get("s1")
    assert sched._state == {}


@pytest.mark.asyncio
async def test_start_arms_the_watcher():
    sched = _sched()
    await sched.start()
    assert sched._watcher is not None
    await sched.stop()


@pytest.mark.asyncio
async def test_watcher_fires_trigger_end_to_end():
    """Full path: instrument on first tick, run_end arms, threshold elapses,
    watcher fires the action exactly once."""
    fired: list[str] = []

    async def _action(service, session):
        fired.append(session.id)
        return "ok"

    svc = _FakeService()
    t = _trigger(name="recap", action=_action, idle_seconds=0.05, min_turns=1)
    sched = IdleTriggerScheduler(svc, [t], watcher_interval_s=0.02)
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
