import asyncio
from dataclasses import dataclass, field
import types

import pytest

from omniagents.core.agents import service as service_module
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec


@dataclass
class DummyAgent:
    name: str = "agent"
    tools: list = field(default_factory=list)
    instructions: str = ""
    model: str = "model"
    model_settings: dict | None = None
    mcp_servers: list = field(default_factory=list)

    def set_approval_callback(self, callback):
        self.approval_callback = callback


async def default_build_agent(settings, primary_server, stub_app):
    return DummyAgent()


def make_spec(**overrides):
    base = {
        "name": "Agent",
        "welcome_text": "Welcome",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: ""),
        "build_agent": overrides.pop("build_agent", default_build_agent),
    }
    base.update(overrides)
    return AgentSpec(**base)


class FakeAsyncServer:
    def __init__(self):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True


def attach_capture(service):
    events = []

    async def capture(self, session, method, payload, *, queue_if_unseen=False):
        events.append({"method": method, "payload": payload, "queue": queue_if_unseen})
        return True

    service._broadcast_session = types.MethodType(capture, service)
    return events


@pytest.mark.asyncio
async def test_start_run_retries_emit_status_and_error(monkeypatch):
    spec = make_spec()
    service = AgentService(spec)
    service.channel = object()
    events = attach_capture(service)
    monkeypatch.setenv("OMNIAGENTS_RETRY_MAX", "2")
    monkeypatch.setenv("OMNIAGENTS_RETRY_BASE", "0.25")
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    attempts = {"count": 0}

    async def failing_stream(*args, **kwargs):
        attempts["count"] += 1
        raise RuntimeError("timeout while calling provider")

    monkeypatch.setattr(service_module, "stream_agent", failing_stream)
    run_info = await service.start_run("prompt")
    sess = service._session_manager.get_or_create(run_info["session_id"])
    run_task = sess.current_task
    await asyncio.wait_for(run_task, timeout=1)
    assert attempts["count"] == 3
    assert len(delays) == 2
    assert delays[0] == pytest.approx(0.25)
    assert delays[1] == pytest.approx(0.5)
    status_events = [e for e in events if e["method"] == "run_status"]
    assert len(status_events) == 2
    assert status_events[0]["payload"]["attempt"] == 1
    assert status_events[0]["payload"]["total"] == 2
    assert status_events[0]["payload"]["next_delay"] == pytest.approx(0.25)
    assert status_events[1]["payload"]["attempt"] == 2
    assert status_events[1]["payload"]["next_delay"] == pytest.approx(0.5)
    end_event = next(e for e in events if e["method"] == "run_end")
    payload = end_event["payload"]
    assert payload["end_reason"] == "error"
    assert payload["error"]["type"] == "RuntimeError"
    assert payload["error"]["message"] == "timeout while calling provider"
    assert end_event["queue"] is True
    assert run_task.done()


@pytest.mark.asyncio
async def test_start_run_success_keeps_mcp_stack(monkeypatch):
    server = FakeAsyncServer()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    async def success_stream(*args, **kwargs):
        return {
            "usage": {"input_tokens": 3},
            "model": "gpt-success",
            "context_window": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", success_stream)
    spec = make_spec()
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()
    events = attach_capture(service)
    run_info = await service.start_run("prompt")
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)
    assert server.entered is True
    assert server.exited is False
    # Check for new lifecycle attributes
    assert getattr(sess, "_mcp_lifecycle_task") is not None
    assert not getattr(sess, "_mcp_lifecycle_task").done()
    assert getattr(sess, "_mcp_servers_active") == [server]
    status_events = [e for e in events if e["method"] == "run_status"]
    assert status_events == []
    end_event = next(e for e in events if e["method"] == "run_end")
    payload = end_event["payload"]
    assert payload["end_reason"] == "completed"
    assert payload["usage"] == {"input_tokens": 3}
    assert payload["model"] == "gpt-success"
    assert payload["context_window"] == 4096
    assert payload["max_output_tokens"] == 512
    assert "error" not in payload
    assert end_event["queue"] is True


@pytest.mark.asyncio
async def test_start_run_failure_keeps_mcp_stack(monkeypatch):
    server = FakeAsyncServer()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    async def failing_stream(*args, **kwargs):
        raise ValueError("bad response")

    monkeypatch.setattr(service_module, "stream_agent", failing_stream)
    spec = make_spec()
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()
    events = attach_capture(service)
    run_info = await service.start_run("prompt")
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)
    assert server.entered is True
    assert server.exited is False
    # Check for new lifecycle attributes
    assert getattr(sess, "_mcp_lifecycle_task") is not None
    assert not getattr(sess, "_mcp_lifecycle_task").done()
    assert getattr(sess, "_mcp_servers_active") == [server]
    status_events = [e for e in events if e["method"] == "run_status"]
    assert status_events == []
    end_event = next(e for e in events if e["method"] == "run_end")
    payload = end_event["payload"]
    assert payload["end_reason"] == "error"
    assert payload["error"]["type"] == "ValueError"
    assert payload["error"]["message"] == "bad response"
    assert end_event["queue"] is True


@pytest.mark.asyncio
async def test_stop_run_keeps_mcp_stack(monkeypatch):
    server = FakeAsyncServer()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)
    started = asyncio.Event()

    async def blocking_stream(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(service_module, "stream_agent", blocking_stream)
    spec = make_spec()
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()
    events = attach_capture(service)
    run_info = await service.start_run("prompt")
    sess = service._session_manager.get_or_create(run_info["session_id"])
    task = sess.current_task
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelled = await service.stop_run(run_info["run_id"])
    assert cancelled is True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert server.entered is True
    assert server.exited is False
    sess_after = service._session_manager.get_or_create(run_info["session_id"])
    # Check for new lifecycle attributes
    assert getattr(sess_after, "_mcp_lifecycle_task") is not None
    assert not getattr(sess_after, "_mcp_lifecycle_task").done()
    assert getattr(sess_after, "_mcp_servers_active") == [server]
    end_event = next(e for e in events if e["method"] == "run_end")
    payload = end_event["payload"]
    assert payload["end_reason"] == "cancelled"
    assert "error" not in payload
    assert end_event["queue"] is True


@pytest.mark.asyncio
async def test_build_agent_failure_closes_mcp_stack(monkeypatch):
    server = FakeAsyncServer()

    def fake_build_mcp(spec_arg, settings_arg):
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    async def failing_builder(settings, primary_server, stub_app):
        raise RuntimeError("builder error")

    spec = make_spec(build_agent=failing_builder)
    spec.mcp_servers = [object()]
    service = AgentService(spec)
    service.channel = object()
    session = service._session_manager.get_or_create("session")
    with pytest.raises(RuntimeError):
        await service.start_run("prompt", session_id=session.id)
    # Give the lifecycle task a moment to fully exit
    await asyncio.sleep(0.05)
    assert server.entered is True
    assert server.exited is True
    # Check for new lifecycle attributes - should be cleaned up
    assert getattr(session, "_mcp_lifecycle_task") is None
    assert getattr(session, "_mcp_servers_active") == []
