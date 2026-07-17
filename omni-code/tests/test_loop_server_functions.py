import asyncio
import time

import pytest

from omniagents.core.autonomy import next_cron_fire, split_loop_interval_and_prompt
from server_functions import loop as loop_mod


class FakeSession:
    def __init__(self):
        self.id = "sess-loop"
        self.variables = {}
        self.pending_notifications = []
        self.current_task = None


class FakeService:
    def __init__(self):
        self.variables_updates = []
        self.client_updates = []
        self.notifications = []
        self.listeners = {}

    async def update_session_variables(self, session_id, updates, silent=False):
        self.variables_updates.append((session_id, updates, silent))
        session.variables.update(updates)

    async def request_client_function(
        self, name, args, session=None, expect_result=False
    ):
        self.client_updates.append(
            (name, args, getattr(session, "id", None), expect_result)
        )

    async def enqueue_notification(self, session_id, content, source):
        self.notifications.append(
            {"session_id": session_id, "content": content, "source": source}
        )
        session.pending_notifications.append({"content": content, "source": source})

    def add_run_end_listener(self, session_id, callback):
        self.listeners.setdefault(session_id, []).append(callback)

    def remove_run_end_listener(self, session_id, callback):
        self.listeners.get(session_id, []).remove(callback)


@pytest.fixture(autouse=True)
def clear_loop_state():
    loop_mod._tasks.clear()
    loop_mod._listeners.clear()
    yield
    loop_mod._tasks.clear()
    loop_mod._listeners.clear()


@pytest.fixture
def service_session():
    global session
    session = FakeSession()
    return FakeService(), session


def test_split_loop_interval_and_prompt_supports_leading_and_trailing_interval():
    assert split_loop_interval_and_prompt("5m check deploy")[:2] == (
        300.0,
        "check deploy",
    )
    assert split_loop_interval_and_prompt("check deploy every 2 hours")[:2] == (
        7200.0,
        "check deploy",
    )
    assert split_loop_interval_and_prompt("check deploy")[:2] == (None, "check deploy")


def test_next_cron_fire_accepts_standard_step_expression():
    after = time.mktime(time.strptime("2026-06-13 10:02", "%Y-%m-%d %H:%M"))
    result = next_cron_fire("*/5 * * * *", after)
    assert time.strftime("%H:%M", time.localtime(result)) == "10:05"


@pytest.mark.asyncio
async def test_loop_create_persists_broadcasts_and_registers_listener(service_session):
    service, sess = service_session

    result = await loop_mod.create_impl(
        service, sess, prompt="check deploy", interval_seconds=60
    )

    assert result["ok"] is True
    task = result["task"]
    assert task["prompt"] == "check deploy"
    assert task["mode"] == "fixed_interval"
    assert sess.variables[loop_mod.VAR_KEY][0]["id"] == task["id"]
    assert service.client_updates[-1][0] == "ui.loop.update"
    assert sess.id in service.listeners

    for task_state in list(loop_mod._tasks[sess.id].values()):
        await loop_mod._teardown_task(service, sess, task_state, status="cancelled")


@pytest.mark.asyncio
async def test_dynamic_loop_completes_on_run_end_without_continue(service_session):
    service, sess = service_session
    result = await loop_mod.create_impl(
        service, sess, prompt="check deploy", dynamic=True
    )
    task_id = result["task"]["id"]
    task = loop_mod._tasks[sess.id][task_id]
    task.last_fire_at = time.time()
    task.next_fire_at = None

    await service.listeners[sess.id][0]({"end_reason": "complete"})

    assert task_id not in loop_mod._tasks.get(sess.id, {})
    assert sess.variables[loop_mod.VAR_KEY] is None


@pytest.mark.asyncio
async def test_loop_continue_keeps_dynamic_loop_active(service_session):
    service, sess = service_session
    result = await loop_mod.create_impl(
        service, sess, prompt="check deploy", dynamic=True
    )
    task_id = result["task"]["id"]
    task = loop_mod._tasks[sess.id][task_id]
    task.last_fire_at = time.time()
    task.next_fire_at = None

    continued = await loop_mod.continue_impl(
        service,
        sess,
        task_id=task_id,
        delay_seconds=120,
        reason="CI still running",
    )
    await service.listeners[sess.id][0]({"end_reason": "complete"})

    assert continued["ok"] is True
    assert task_id in loop_mod._tasks[sess.id]
    assert loop_mod._tasks[sess.id][task_id].last_reason == "CI still running"

    await loop_mod._teardown_task(
        service, sess, loop_mod._tasks[sess.id][task_id], status="cancelled"
    )
