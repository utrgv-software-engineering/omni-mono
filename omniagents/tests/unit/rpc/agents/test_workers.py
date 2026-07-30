import asyncio
from types import SimpleNamespace

import pytest

from omniagents.rpc.agents.workers import WorkerManager


class FakeOther:
    def __init__(self, agent):
        self.agent = agent
        self.sent_messages = []
        self.stopped_runs = []
        self.approvals = []

    async def start_run(self, prompt, session_id=None, context=None, **kwargs):
        self.agent.started_prompt = prompt
        self.agent.started_session_id = session_id
        # Capture the run context so tests can assert workspace_root / other
        # context keys propagated from the parent's spawn_worker call into
        # the worker's own start_run payload.
        self.agent.started_context = context
        self.agent.started_kwargs = kwargs
        return SimpleNamespace(
            result={"run_id": self.agent.run_id, "session_id": self.agent.session_id}
        )

    async def send_user_message(self, run_id, content):
        self.sent_messages.append((run_id, content))

    async def stop_run(self, run_id):
        self.stopped_runs.append(run_id)

    async def client_response(self, request_id, ok, result):
        self.approvals.append((request_id, ok, result))


class FakeAgent:
    counter = 0

    def __init__(self, spec):
        FakeAgent.counter += 1
        self.spec = spec
        self.run_id = f"run-{FakeAgent.counter}"
        self.session_id = f"session-{FakeAgent.counter}"
        self._event_queue = asyncio.Queue()
        self._client = SimpleNamespace(other=FakeOther(self))
        self.cleaned = False

    async def initialize(self):
        return None

    async def _ensure_client(self):
        return None

    async def cleanup(self):
        self.cleaned = True


def make_manager():
    def spec_loader(model):
        return SimpleNamespace(
            variables={}, project_slug="proj", agent_slug="worker"
        ), {"model": model}

    return WorkerManager(spec_loader=spec_loader, agent_factory=FakeAgent)


@pytest.mark.asyncio
async def test_worker_manager_spawn_wait_and_status():
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-a", task_description="do it", model=None
    )

    await handle.agent._event_queue.put(
        {
            "method": "message_output",
            "params": {"content": "done"},
        }
    )
    await handle.agent._event_queue.put(
        {
            "method": "run_end",
            "params": {"run_id": handle.run_id, "end_reason": "completed"},
        }
    )
    await asyncio.wait_for(handle.done_event.wait(), timeout=1)

    waited = await manager.wait(["worker-a"])
    status = await manager.get_status("worker-a")

    assert waited["completed"]["worker-a"] == "done"
    assert status["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_manager_auto_approves_client_request():
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-b", task_description="do it", model=None
    )

    await handle.agent._event_queue.put(
        {
            "method": "client_request",
            "params": {"request_id": "req-1"},
        }
    )
    await asyncio.sleep(0)

    assert handle.agent._client.other.approvals == [("req-1", True, {"ack": True})]


@pytest.mark.asyncio
async def test_worker_manager_send_close_and_resume():
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-c", task_description="first", model=None
    )

    sent = await manager.send_message("worker-c", "follow up")
    assert sent == "message_sent"
    assert handle.agent._client.other.sent_messages == [(handle.run_id, "follow up")]

    closed = await manager.close("worker-c")
    assert closed == "cancelled"
    assert handle.agent.cleaned is True

    resumed = await manager.resume("worker-c", "resume task")
    assert resumed is not None
    assert resumed.agent.started_session_id == handle.session_id


@pytest.mark.asyncio
async def test_worker_manager_get_status_uses_history_loader():
    manager = make_manager()
    await manager.spawn(worker_id="worker-d", task_description="task", model=None)

    async def history_loader(handle, tail):
        return [{"role": "user", "content": f"tail-{tail}"}]

    status = await manager.get_status("worker-d", tail=3, history_loader=history_loader)

    assert status["history"] == [{"role": "user", "content": "tail-3"}]


# ---------------------------------------------------------------------------
# Context propagation — the parent's spawn_worker tool reads
# workspace_root from its own ctx and passes it via context= so the
# worker's session.context inherits it. This is the regression test
# for the silent-cwd-fallback bug in server mode.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_manager_spawn_forwards_context_to_start_run():
    """spawn(context={...}) must reach the worker's start_run payload."""
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-ctx",
        task_description="do it",
        model=None,
        context={"workspace_root": "/tenant/x", "extra_key": "v"},
    )

    assert handle.agent.started_context == {
        "workspace_root": "/tenant/x",
        "extra_key": "v",
    }


@pytest.mark.asyncio
async def test_worker_manager_spawn_omits_context_when_none():
    """When the parent passes no context, workers should not receive a
    context= kwarg either — preserves backwards-compatible session creation."""
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-noctx",
        task_description="do it",
        model=None,
    )

    assert handle.agent.started_context is None


@pytest.mark.asyncio
async def test_worker_manager_resume_forwards_context_to_start_run():
    """Resume after close must also propagate context — otherwise a
    resumed worker would lose its workspace_root mid-flight."""
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-resume",
        task_description="first",
        model=None,
        context={"workspace_root": "/initial"},
    )
    await manager.close("worker-resume")

    resumed = await manager.resume(
        "worker-resume", "follow up", context={"workspace_root": "/resumed"}
    )
    assert resumed is not None
    assert resumed.agent.started_context == {"workspace_root": "/resumed"}
