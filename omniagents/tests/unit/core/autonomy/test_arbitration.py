import asyncio

import pytest

from omniagents.core.autonomy import (
    active_tick_kinds,
    has_active_tick,
    register_active_tick,
    unregister_active_tick,
)
from omniagents.core.autonomy import wakeup as wakeup_mod
from omniagents.core.autonomy.arbitration import _active_ticks


class FakeSession:
    def __init__(self, sid="sess-arb"):
        self.id = sid
        self.variables = {}
        self.pending_notifications = []
        self.current_task = None


class FakeService:
    def __init__(self, session):
        self.session = session
        self.notifications = []

    async def update_session_variables(self, session_id, updates, silent=False):
        self.session.variables.update(updates)

    async def request_client_function(
        self, name, args, session=None, expect_result=False
    ):
        pass

    async def enqueue_notification(self, session_id, content, source):
        self.notifications.append(
            {"session_id": session_id, "content": content, "source": source}
        )


@pytest.fixture(autouse=True)
def clear_registry():
    _active_ticks.clear()
    wakeup_mod._schedules.clear()
    yield
    _active_ticks.clear()
    wakeup_mod._schedules.clear()


def test_register_unregister_roundtrip():
    assert not has_active_tick("s1", "goal")
    register_active_tick("s1", "goal")
    assert has_active_tick("s1", "goal")
    assert active_tick_kinds("s1") == frozenset({"goal"})
    # Idempotent register.
    register_active_tick("s1", "goal")
    register_active_tick("s1", "loop")
    assert active_tick_kinds("s1") == frozenset({"goal", "loop"})
    # Scoped per session.
    assert not has_active_tick("s2", "goal")

    unregister_active_tick("s1", "goal")
    assert not has_active_tick("s1", "goal")
    assert active_tick_kinds("s1") == frozenset({"loop"})
    # Idempotent unregister, including for unknown sessions/kinds.
    unregister_active_tick("s1", "goal")
    unregister_active_tick("s2", "goal")
    unregister_active_tick("s1", "loop")
    assert active_tick_kinds("s1") == frozenset()
    # Empty session bucket is dropped.
    assert "s1" not in _active_ticks


def test_wakeup_refuses_while_goal_tick_registered():
    session = FakeSession()
    service = FakeService(session)
    register_active_tick(session.id, "goal")

    result = asyncio.run(
        wakeup_mod.schedule_impl(
            service, session, interval_seconds=60, message="watch the thing"
        )
    )

    assert result["ok"] is False
    assert result["message"] == (
        "A /goal loop is active on this session. Use /goal.stop "
        "first or finish the goal before scheduling a wakeup."
    )
    assert session.id not in wakeup_mod._schedules


def test_wakeup_schedules_once_goal_tick_unregistered():
    session = FakeSession()
    service = FakeService(session)
    register_active_tick(session.id, "goal")
    unregister_active_tick(session.id, "goal")

    async def scenario():
        result = await wakeup_mod.schedule_impl(
            service, session, interval_seconds=60, message="watch the thing"
        )
        assert result["ok"] is True
        assert session.id in wakeup_mod._schedules
        # Clean up the tick task.
        cancelled = await wakeup_mod.cancel_impl(service, session)
        assert cancelled["ok"] is True

    asyncio.run(scenario())
