import asyncio

import pytest
from omniagents.core.autonomy import goal as goal_mod


class FakeSession:
    def __init__(self):
        self.id = "sess-goal"
        self.variables = {}
        self.pending_notifications = []
        self.current_task = None
        self.tick_calls = []

    def set_tick(
        self, interval_seconds, content, *, source="tick", safe_tool_overrides=None
    ):
        self.tick_calls.append(
            {
                "interval_seconds": interval_seconds,
                "content": content,
                "source": source,
                "safe_tool_overrides": safe_tool_overrides,
            }
        )

    def cancel_tick(self):
        pass


class FakeService:
    def __init__(self, session):
        self.session = session
        self.variables_updates = []
        self.client_updates = []
        self.enqueued_messages = []
        self.listeners = {}

    async def update_session_variables(self, session_id, updates, silent=False):
        self.variables_updates.append((session_id, updates, silent))
        self.session.variables.update(updates)

    async def request_client_function(
        self, name, args, session=None, expect_result=False
    ):
        self.client_updates.append(
            (name, args, getattr(session, "id", None), expect_result)
        )

    async def enqueue_message(
        self,
        session_id,
        content,
        role="user",
        source=None,
        safe_tool_overrides=None,
    ):
        self.enqueued_messages.append(
            {
                "session_id": session_id,
                "content": content,
                "role": role,
                "source": source,
                "safe_tool_overrides": safe_tool_overrides,
            }
        )
        return {"ok": True, "session_id": session_id}

    def add_run_end_listener(self, session_id, callback):
        self.listeners.setdefault(session_id, []).append(callback)

    def remove_run_end_listener(self, session_id, callback):
        self.listeners.get(session_id, []).remove(callback)


@pytest.fixture(autouse=True)
def clear_goal_state():
    goal_mod._goals.clear()
    yield
    goal_mod._goals.clear()


def test_goal_start_forwards_safe_tool_overrides_to_initial_run_and_tick():
    session = FakeSession()
    service = FakeService(session)
    safe_tool_overrides = {"safe_tool_patterns": [".*"]}

    result = asyncio.run(
        goal_mod.start_impl(
            service,
            session,
            {
                "goal": "complete the ticket",
                "max_turns": 3,
                "tick_interval": 1,
                "safe_tool_overrides": safe_tool_overrides,
            },
        )
    )

    assert result["ok"] is True
    assert service.enqueued_messages[0]["source"] == "goal.start"
    assert "trigger_run" not in service.enqueued_messages[0]
    assert service.enqueued_messages[0]["safe_tool_overrides"] == safe_tool_overrides
    assert session.tick_calls[0]["source"] == "goal.tick"
    assert session.tick_calls[0]["safe_tool_overrides"] == safe_tool_overrides
