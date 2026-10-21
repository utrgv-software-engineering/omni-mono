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

    async def update_thread(self, thread_id, status):
        self.agent.restored_thread = (thread_id, status)
        return SimpleNamespace(result={"thread_id": thread_id, "status": status})

    async def send_user_message(self, run_id, content):
        self.sent_messages.append((run_id, content))

    async def stop_run(self, run_id):
        self.stopped_runs.append(run_id)

    async def tool_approval_response(self, **kwargs):
        self.approvals.append(kwargs)

    async def mcp_approval_response(self, **kwargs):
        self.approvals.append(kwargs)

    async def client_response(self, request_id, ok, result):
        self.approvals.append((request_id, ok, result))


class FakeAgent:
    counter = 0

    def __init__(self, spec, host_resources=None):
        FakeAgent.counter += 1
        self.spec = spec
        self.host_resources = host_resources
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
        "worker_id": "worker-ctx",
        "parent_session_id": None,
    }
    await manager.close("worker-ctx")


@pytest.mark.asyncio
async def test_worker_manager_marks_worker_context_without_parent_context():
    """Worker identity persists even for targetless runs."""
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-noctx",
        task_description="do it",
        model=None,
    )

    assert handle.agent.started_context == {
        "worker_id": "worker-noctx",
        "parent_session_id": None,
    }
    await manager.close("worker-noctx")


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
    assert resumed.agent.started_context == {
        "workspace_root": "/resumed",
        "worker_id": "worker-resume",
        "parent_session_id": None,
    }
    assert resumed.agent.restored_thread == (handle.session_id, "active")
    await manager.close("worker-resume")


@pytest.mark.asyncio
async def test_worker_manager_selects_environment_from_context():
    """The spawner names the environment (parent's own, or a worktree
    environment registered on the shared AgentHost); the worker's run
    selects it explicitly. Without a lease the environment-scoped
    runtime rejects every file/process tool."""
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-env",
        task_description="do it",
        model=None,
        context={"environment_id": "environment-abc", "task_scope": "worker-env"},
    )
    assert handle.agent.started_kwargs["environment_selection"] == {
        "mode": "explicit",
        "environment_id": "environment-abc",
    }


@pytest.mark.asyncio
async def test_worker_manager_targetless_without_environment():
    """No environment named -> explicitly targetless (thinking-only
    worker). workspace_root alone is NOT routing — start_run strips it."""
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="worker-bare",
        task_description="do it",
        model=None,
        context={"workspace_root": "/ignored"},
    )
    assert handle.agent.started_kwargs["environment_selection"] == {"mode": "none"}


@pytest.mark.asyncio
async def test_worker_manager_threads_host_resources_to_factory():
    """Workers join the parent AgentHost's registries: the provider's
    bundle must reach the agent factory."""
    sentinel = object()

    def spec_loader(model):
        return SimpleNamespace(
            variables={}, project_slug="proj", agent_slug="worker"
        ), {"model": model}

    manager = WorkerManager(
        spec_loader=spec_loader,
        agent_factory=FakeAgent,
        host_resources_provider=lambda: sentinel,
    )
    handle = await manager.spawn(
        worker_id="worker-host", task_description="t", model=None
    )
    assert handle.agent.host_resources is sentinel


@pytest.mark.asyncio
async def test_worker_manager_resume_reselects_environment():
    """Resume re-runs environment selection from the fresh context."""
    manager = make_manager()
    await manager.spawn(
        worker_id="worker-re",
        task_description="first",
        model=None,
        context={"environment_id": "environment-1"},
    )
    await manager.close("worker-re")

    resumed = await manager.resume(
        "worker-re", "again", context={"environment_id": "environment-2"}
    )
    assert resumed is not None
    assert resumed.agent.started_kwargs["environment_selection"] == {
        "mode": "explicit",
        "environment_id": "environment-2",
    }


@pytest.mark.asyncio
async def test_worker_manager_relays_narrative_events_to_observer():
    """The monitor streams the narrative subset (tool/message/run events)
    to on_event, tags nothing itself (handle carries identity), leaves
    approvals pending without answering them, and still relays run_end before
    finalizing."""
    relayed = []

    async def on_event(handle, method, params):
        relayed.append((handle.worker_id, method, params))

    def spec_loader(model):
        return SimpleNamespace(
            variables={}, project_slug="proj", agent_slug="worker"
        ), {"model": model}

    manager = WorkerManager(
        spec_loader=spec_loader, agent_factory=FakeAgent, on_event=on_event
    )
    handle = await manager.spawn(
        worker_id="worker-obs", task_description="do it", model=None
    )

    events = [
        {"method": "run_started", "params": {"run_id": handle.run_id}},
        {"method": "tool_called", "params": {"tool": "write_file"}},
        {"method": "token", "params": {"text": "chatty"}},
        {"method": "tool_approval_requested", "params": {"call_id": "c1"}},
        {"method": "mcp_approval_requested", "params": {"request_id": "m1"}},
        {"method": "tool_result", "params": {"tool": "write_file"}},
        {"method": "message_output", "params": {"content": "done"}},
        {
            "method": "run_end",
            "params": {"run_id": handle.run_id, "end_reason": "completed"},
        },
    ]
    for event in events:
        await handle.agent._event_queue.put(event)
    await asyncio.wait_for(handle.done_event.wait(), timeout=1)

    assert [
        (m, p.get("tool") or p.get("content") or p.get("run_id")) for _, m, p in relayed
    ] == [
        ("run_started", handle.run_id),
        ("tool_called", "write_file"),
        ("tool_result", "write_file"),
        ("message_output", "done"),
        ("run_end", handle.run_id),
    ]
    assert all(worker_id == "worker-obs" for worker_id, _, _ in relayed)
    # Approval was answered, not observed.
    assert handle.agent._client.other.approvals == []


@pytest.mark.asyncio
async def test_worker_manager_relay_failure_does_not_poison_lifecycle():
    async def on_event(handle, method, params):
        raise RuntimeError("relay down")

    def spec_loader(model):
        return SimpleNamespace(
            variables={}, project_slug="proj", agent_slug="worker"
        ), {"model": model}

    manager = WorkerManager(
        spec_loader=spec_loader, agent_factory=FakeAgent, on_event=on_event
    )
    handle = await manager.spawn(
        worker_id="worker-boom", task_description="do it", model=None
    )
    await handle.agent._event_queue.put(
        {"method": "message_output", "params": {"content": "ok"}}
    )
    await handle.agent._event_queue.put(
        {
            "method": "run_end",
            "params": {"run_id": handle.run_id, "end_reason": "completed"},
        }
    )
    await asyncio.wait_for(handle.done_event.wait(), timeout=1)
    assert handle.status == "completed"
    assert handle.result == "ok"


@pytest.mark.asyncio
async def test_worker_spec_configuration_is_opt_in_and_survives_resume():
    cached = SimpleNamespace(
        variables={}, available_tools=[], project_slug="proj", agent_slug="worker"
    )
    manager = WorkerManager(
        spec_loader=lambda model: (cached, {}), agent_factory=FakeAgent
    )

    def configure(spec):
        spec.available_tools = list(spec.available_tools) + ["bound-member-tool"]

    handle = await manager.spawn(
        worker_id="member", task_description="first", configure_spec=configure
    )
    assert handle.agent.spec.available_tools == ["bound-member-tool"]
    assert cached.available_tools == []
    await handle.agent._event_queue.put(
        {
            "method": "run_end",
            "params": {"run_id": handle.run_id, "end_reason": "completed"},
        }
    )
    await asyncio.wait_for(handle.done_event.wait(), timeout=1)
    resumed = await manager.resume("member", "follow up")
    assert resumed.configure_spec is configure
    assert resumed.agent.spec.available_tools == ["bound-member-tool"]
    ordinary = await manager.spawn(worker_id="ordinary", task_description="baseline")
    assert ordinary.agent.spec.available_tools == []
    assert ordinary.configure_spec is None
    for item in (resumed, ordinary):
        await item.agent._event_queue.put(
            {
                "method": "run_end",
                "params": {"run_id": item.run_id, "end_reason": "completed"},
            }
        )
        await asyncio.wait_for(item.done_event.wait(), timeout=1)


@pytest.mark.asyncio
async def test_swarm_runs_through_real_worker_adapter_and_manager(monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from agents.run_context import RunContextWrapper
    from omniagents.builtin.tools import swarm_tools as sw
    from omniagents.builtin.tools import worker_tools as wt
    from omniagents.core.workers.config import WorkerDefaults

    completion = AsyncMock()
    manager = WorkerManager(
        spec_loader=lambda model: (
            SimpleNamespace(variables={}, available_tools=[]),
            {},
        ),
        agent_factory=FakeAgent,
        on_complete=completion,
    )
    monkeypatch.setattr(wt, "_MANAGER", manager)
    monkeypatch.setattr(wt, "worker_defaults", lambda: WorkerDefaults())
    monkeypatch.setattr(wt, "_WORKER_META", {})
    monkeypatch.setattr(sw, "_SWARMS", {})
    context = RunContextWrapper(
        context={"session_id": "resident-day", "workspace_root": "/tmp"}
    )

    async def invoke(tool, *args):
        return json.loads(await tool._original_func(*args))

    group = await invoke(sw.create_swarm, context, "Compare implementations")
    sid = group["swarm_id"]
    for name in ("builder", "reviewer"):
        await invoke(sw.add_swarm_member, context, sid, name, name)
        result = await invoke(
            sw.dispatch_swarm_member, context, sid, name, "Inspect code"
        )
        assert result["status"] == "spawned"
    members = sw._SWARMS[sid].members
    handles = {
        name: manager.workers[member.worker_id] for name, member in members.items()
    }
    builder = handles["builder"]
    assert builder.parent_session_id == "resident-day"
    tools = {tool.name: tool for tool in builder.agent.spec.available_tools}
    message = await invoke(
        tools["swarm_member_message"], "reviewer", "Check backward compatibility"
    )
    assert message["delivery"] == "message_sent"
    assert "from builder" in handles["reviewer"].agent._client.other.sent_messages[0][1]
    for handle in handles.values():
        await handle.agent._event_queue.put(
            {"method": "message_output", "params": {"content": "verified"}}
        )
        await handle.agent._event_queue.put(
            {
                "method": "run_end",
                "params": {"run_id": handle.run_id, "end_reason": "completed"},
            }
        )
        await asyncio.wait_for(handle.done_event.wait(), timeout=1)
    snapshot = await invoke(sw.get_swarm, context, sid)
    assert all(m["state"]["result"] == "verified" for m in snapshot["members"])
    assert completion.await_count == 2
    await invoke(sw.close_swarm, context, sid, "Both verified")


@pytest.mark.asyncio
async def test_resume_inherits_parent_model_after_selection_changes(
    tmp_path, monkeypatch
):
    from omniagents.core import models

    store = models.ModelConfigStore(config_dir=lambda: tmp_path, single_provider=True)
    store.save_config(
        {
            "default": "company/a",
            "providers": {
                "company": {
                    "type": "openai-compatible",
                    "base_url": "http://localhost:9999/v1",
                    "models": {"a": {"model": "a"}, "b": {"model": "b"}},
                }
            },
        }
    )
    monkeypatch.setattr(models, "model_store", lambda: store)
    parent_session = SimpleNamespace(active_model="company/a", model_config=None)
    parent = SimpleNamespace(
        get_session=lambda sid: parent_session, spec=SimpleNamespace()
    )
    manager = make_manager()
    handle = await manager.spawn(
        worker_id="inherit",
        task_description="first",
        model="company/a",
        parent_service=parent,
        parent_session_id="parent",
    )
    await manager.close("inherit")
    parent_session.active_model = "company/b"
    resumed = await manager.resume("inherit", "continue")
    assert resumed.model == "company/b"
    await manager.close("inherit")
