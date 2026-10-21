import asyncio

import pytest

from omniagents.core.autonomy import goal as goal_mod
from omniagents.core.autonomy import loop as loop_mod
from omniagents.core.autonomy.arbitration import _active_ticks


class FakeSession:
    def __init__(self, sid="sess-prompts"):
        self.id = sid
        self.variables = {}
        self.pending_notifications = []
        self.current_task = None
        self.tick_calls = []

    def set_tick(
        self, interval_seconds, content, *, source="tick", safe_tool_overrides=None
    ):
        self.tick_calls.append({"content": content, "source": source})

    def cancel_tick(self):
        pass


class FakeService:
    def __init__(self, session):
        self.session = session
        self.enqueued_messages = []
        self.listeners = {}

    async def update_session_variables(self, session_id, updates, silent=False):
        self.session.variables.update(updates)

    async def request_client_function(
        self, name, args, session=None, expect_result=False
    ):
        pass

    async def enqueue_message(self, session_id, content, **kwargs):
        self.enqueued_messages.append({"session_id": session_id, "content": content})
        return {"ok": True}

    def add_run_end_listener(self, session_id, callback):
        self.listeners.setdefault(session_id, []).append(callback)

    def remove_run_end_listener(self, session_id, callback):
        self.listeners.get(session_id, []).remove(callback)


@pytest.fixture(autouse=True)
def reset_builders_and_state():
    yield
    # None restores the generic defaults.
    goal_mod.set_goal_prompt_builders(initial=None, tick=None)
    loop_mod.set_loop_prompt_builders(tick=None, concluded=None)
    goal_mod._goals.clear()
    _active_ticks.clear()


def test_goal_generic_defaults_used_when_nothing_installed():
    initial = goal_mod.build_initial_prompt("stabilize the pipeline", 7)
    assert "stabilize the pipeline" in initial
    assert "goal_complete" in initial
    assert "7 turns" in initial

    tick = goal_mod.build_tick_prompt(goal="stabilize the pipeline", max_turns=7)
    assert tick.startswith("[goal] Tick.")
    assert "stabilize the pipeline" in tick
    assert "goal_complete" in tick


def test_goal_product_builders_win_when_installed():
    goal_mod.set_goal_prompt_builders(
        initial=lambda goal, max_turns: f"PRODUCT-INITIAL {goal} {max_turns}",
        tick=lambda *, goal, max_turns: f"PRODUCT-TICK {goal} {max_turns}",
    )

    session = FakeSession()
    service = FakeService(session)
    result = asyncio.run(
        goal_mod.start_impl(service, session, {"goal": "do the thing", "max_turns": 3})
    )
    assert result["ok"] is True
    assert service.enqueued_messages[0]["content"] == "PRODUCT-INITIAL do the thing 3"
    assert session.tick_calls[0]["content"] == "PRODUCT-TICK do the thing 3"

    # Restoring the default (None) brings the generic wording back.
    goal_mod.set_goal_prompt_builders(initial=None, tick=None)
    assert "PRODUCT" not in goal_mod.build_initial_prompt("do the thing", 3)


def test_loop_generic_defaults_and_product_override():
    default_tick = loop_mod.build_tick_prompt(
        task_id="abc123",
        prompt="check the queue",
        fires=0,
        expires_at=None,
        mode="dynamic",
    )
    assert default_tick.startswith("[loop:abc123]")
    assert "loop_continue" in default_tick

    default_concluded = loop_mod.build_concluded_prompt(
        task_id="abc123", prompt="check the queue", fires=4
    )
    assert "concluded after 4 fires" in default_concluded

    loop_mod.set_loop_prompt_builders(
        tick=lambda *, task_id, prompt, fires, expires_at, mode: f"PRODUCT-TICK {task_id}",
        concluded=lambda *, task_id, prompt, fires: f"PRODUCT-DONE {task_id}",
    )
    assert (
        loop_mod.build_tick_prompt(
            task_id="abc123", prompt="p", fires=0, expires_at=None, mode="dynamic"
        )
        == "PRODUCT-TICK abc123"
    )
    assert (
        loop_mod.build_concluded_prompt(task_id="abc123", prompt="p", fires=1)
        == "PRODUCT-DONE abc123"
    )
