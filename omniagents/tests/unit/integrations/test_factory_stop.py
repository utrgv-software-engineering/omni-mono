import asyncio
from types import SimpleNamespace

import pytest

from omniagents.core.runtime import factory_stop
from omniagents.core.runtime.release_gate import ReleaseGate, admission
from omniagents.core.runtime.tool_durability import DurableToolHooks
from omniagents.integrations.events import IncomingEvent
from omniagents.integrations.intake import EventIntake, RunResult
from omniagents.integrations.server import stop_all_work
from omniagents.integrations.store import EventStore

STOP = {"stopped_by": "eric", "stopped_at": 1.0, "reason": "drill", "scope": "factory"}


@pytest.fixture(autouse=True)
def running():
    factory_stop.set_state(None)
    yield
    factory_stop.set_state(None)


def test_stopped_factory_admits_no_turn():
    @admission
    async def run(service):
        return "started"

    service = SimpleNamespace(_release_gate=ReleaseGate(lambda: 0))
    factory_stop.set_state(STOP)
    with pytest.raises(factory_stop.FactoryStopped, match="stopped: drill"):
        asyncio.run(run(service))
    factory_stop.set_state(None)
    assert asyncio.run(run(service)) == "started"


def test_a_run_halts_at_its_next_model_or_tool_call():
    hooks = DurableToolHooks(SimpleNamespace(id="s"), "r")
    factory_stop.set_state(STOP)
    with pytest.raises(factory_stop.FactoryStopped):
        asyncio.run(hooks.on_llm_start(None, None, None, []))
    with pytest.raises(factory_stop.FactoryStopped):
        asyncio.run(
            hooks.on_tool_start(SimpleNamespace(), None, SimpleNamespace(name="bash"))
        )


def test_stop_state_survives_restart(tmp_path):
    store = EventStore(tmp_path / "events.db")
    assert store.stop_state() is None
    store.set_stop_state(STOP)
    store.close()
    reopened = EventStore(tmp_path / "events.db")
    assert reopened.stop_state() == STOP
    reopened.set_stop_state(None)
    assert reopened.stop_state() is None
    reopened.close()


@pytest.mark.asyncio
async def test_events_received_while_stopped_are_skipped_and_never_replayed(tmp_path):
    directory = tmp_path / "automations" / "mention"
    directory.mkdir(parents=True)
    (directory / "automation.md").write_text(
        "---\ntriggers:\n  - provider: slack\n    event: app_mention\n---\nHandle it.\n"
    )
    calls = []

    async def run(agent, session, prompt, progress):
        calls.append(prompt)
        return RunResult(output="done")

    store = EventStore(tmp_path / "events.db")
    intake = EventIntake(
        project_root=tmp_path, entrypoint="foreman", store=store, run=run
    )
    factory_stop.set_state(STOP)
    assert await intake.accept(
        IncomingEvent("slack", "app_mention", "one", {"text": "hi"}, thread_key="t")
    )
    await intake.drain()
    factory_stop.set_state(None)
    await intake.resume_received()
    await intake.drain()
    assert calls == []
    assert store.received_events() == []
    assert store.recent()[0]["status"] == "skipped"
    store.close()


class FakeService:
    def __init__(self, runs):
        self.runs = list(runs)
        self.cancelled = []

    def active_run_ids(self):
        return list(self.runs)

    async def stop_run(self, run_id):
        self.runs.remove(run_id)
        return True

    async def cancel_queued_message(self, thread_id, item_id):
        self.cancelled.append((thread_id, item_id))
        return {"ok": True}


class FakeSandbox:
    def __init__(self):
        self.terminated = False

    async def pty_terminate_all(self):
        self.terminated = True


@pytest.mark.asyncio
async def test_stopping_ends_runs_queued_work_events_and_sandbox_commands(tmp_path):
    store = EventStore(tmp_path / "events.db")
    primary, worker = FakeService(["run-1"]), FakeService(["run-2", "run-3"])
    runtime = SimpleNamespace(state=SimpleNamespace(agent_service=worker))

    async def app(agent):
        return runtime

    runtimes = SimpleNamespace(services=lambda: [primary, worker], app=app)

    async def queued():
        yield "builder", "thread-1", ["item-1", "item-2"]

    blocked = asyncio.Event()
    pending = asyncio.create_task(blocked.wait())
    intake = SimpleNamespace(cancel_all=lambda: (pending.cancel(), 1)[1])
    sandbox = FakeSandbox()
    factory_stop.track(sandbox)
    try:
        result = await stop_all_work(STOP, store, intake, runtimes, queued)
    finally:
        factory_stop.untrack(sandbox)
    assert result == {
        "stopped": STOP,
        "runs": 3,
        "queued": 2,
        "events": 1,
        "sandboxes": 1,
    }
    assert primary.runs == worker.runs == []
    assert worker.cancelled == [("thread-1", "item-1"), ("thread-1", "item-2")]
    assert sandbox.terminated
    assert factory_stop.current() == STOP
    assert store.stop_state() == STOP
    store.close()
