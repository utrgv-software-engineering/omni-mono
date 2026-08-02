import asyncio
from dataclasses import dataclass, field
import types

import pytest

from omniagents.execution import AgentHostResources, register_host_environment
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

    async def capture(
        self, session, method, payload, *, queue_if_unseen=False, track_live=False
    ):
        events.append({"method": method, "payload": payload, "queue": queue_if_unseen})
        return True

    service._broadcast_session = types.MethodType(capture, service)
    return events


@pytest.mark.asyncio
async def test_start_run_retries_emit_status_and_error(monkeypatch):
    spec = make_spec()
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
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
    run_info = await service.start_run("prompt", {"mode": "none"})
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

    def fake_build_mcp(spec_arg, settings_arg, *, auth_resolver=None):
        # ``auth_resolver`` is threaded in by the mcp_* management surface
        # (tkt_kVK7F-iaL-OA) so OAuth credentials reach the built servers.
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    async def success_stream(*args, **kwargs):
        return {
            "usage": {"input_tokens": 3},
            "model": "gpt-success",
            "max_input_tokens": 4096,
            "max_output_tokens": 512,
        }

    monkeypatch.setattr(service_module, "stream_agent", success_stream)
    spec = make_spec()
    spec.mcp_servers = [object()]
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    events = attach_capture(service)
    run_info = await service.start_run("prompt", {"mode": "none"})
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
    assert payload["max_input_tokens"] == 4096
    assert payload["max_output_tokens"] == 512
    assert "error" not in payload
    assert end_event["queue"] is True


@pytest.mark.asyncio
async def test_start_run_failure_keeps_mcp_stack(monkeypatch):
    server = FakeAsyncServer()

    def fake_build_mcp(spec_arg, settings_arg, *, auth_resolver=None):
        # ``auth_resolver`` is threaded in by the mcp_* management surface
        # (tkt_kVK7F-iaL-OA) so OAuth credentials reach the built servers.
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    async def failing_stream(*args, **kwargs):
        raise ValueError("bad response")

    monkeypatch.setattr(service_module, "stream_agent", failing_stream)
    spec = make_spec()
    spec.mcp_servers = [object()]
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    events = attach_capture(service)
    run_info = await service.start_run("prompt", {"mode": "none"})
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

    def fake_build_mcp(spec_arg, settings_arg, *, auth_resolver=None):
        # ``auth_resolver`` is threaded in by the mcp_* management surface
        # (tkt_kVK7F-iaL-OA) so OAuth credentials reach the built servers.
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
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    events = attach_capture(service)
    run_info = await service.start_run("prompt", {"mode": "none"})
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

    def fake_build_mcp(spec_arg, settings_arg, *, auth_resolver=None):
        # ``auth_resolver`` is threaded in by the mcp_* management surface
        # (tkt_kVK7F-iaL-OA) so OAuth credentials reach the built servers.
        return [server]

    monkeypatch.setattr(service_module, "build_mcp_servers", fake_build_mcp)

    async def failing_builder(settings, primary_server, stub_app):
        raise RuntimeError("builder error")

    spec = make_spec(build_agent=failing_builder)
    spec.mcp_servers = [object()]
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    session = service._session_manager.get_or_create("session")
    with pytest.raises(RuntimeError):
        await service.start_run("prompt", {"mode": "none"}, session_id=session.id)
    # Give the lifecycle task a moment to fully exit
    await asyncio.sleep(0.05)
    assert server.entered is True
    assert server.exited is True
    # Check for new lifecycle attributes - should be cleaned up
    assert getattr(session, "_mcp_lifecycle_task") is None
    assert getattr(session, "_mcp_servers_active") == []


@pytest.mark.asyncio
async def test_start_run_merges_variables_with_existing(monkeypatch):
    """When a session already has variables, start_run should merge new variables
    into existing ones rather than replacing them entirely."""

    async def success_stream(*args, **kwargs):
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)

    spec = make_spec()
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    attach_capture(service)

    # Pre-set variables on a session via session.ensure
    result = await service.server_call(
        "session.ensure",
        {"variables": {"additional_instructions": "Do X", "context_key": "val"}},
    )
    session_id = result["session_id"]

    # New non-routing variables merge without letting workspace_root select an
    # execution substrate.
    run_info = await service.start_run(
        "hello",
        {"mode": "none"},
        session_id=session_id,
        variables={"workspace_root": "/workspace", "new_key": "new"},
    )
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)

    # Both old and new variables should be present
    assert sess.variables["additional_instructions"] == "Do X"
    assert sess.variables["context_key"] == "val"
    assert sess.variables["new_key"] == "new"
    assert "workspace_root" not in sess.variables


@pytest.mark.asyncio
async def test_start_run_new_variables_override_existing_keys(monkeypatch):
    """When start_run passes a variable that already exists on the session,
    the new value should win (dict.update semantics)."""

    async def success_stream(*args, **kwargs):
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)

    spec = make_spec()
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    attach_capture(service)

    result = await service.server_call(
        "session.ensure",
        {"variables": {"override_me": "old", "keep_me": "yes"}},
    )
    session_id = result["session_id"]

    run_info = await service.start_run(
        "hello",
        {"mode": "none"},
        session_id=session_id,
        variables={"override_me": "new"},
    )
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)

    assert sess.variables["override_me"] == "new"
    assert sess.variables["keep_me"] == "yes"


@pytest.mark.asyncio
async def test_start_run_replaces_variables_when_existing_not_dict(monkeypatch):
    """When existing session variables are not a dict, start_run should replace them."""

    async def success_stream(*args, **kwargs):
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)

    spec = make_spec()
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    attach_capture(service)

    # Create session and manually set non-dict variables
    sess = service._session_manager.get_or_create()
    sess.variables = "not-a-dict"

    run_info = await service.start_run(
        "hello",
        {"mode": "none"},
        session_id=sess.id,
        variables={"new_key": "new"},
    )
    await asyncio.wait_for(sess.current_task, timeout=1)

    assert sess.variables == {"new_key": "new"}


@pytest.mark.asyncio
async def test_start_run_no_variables_preserves_existing(monkeypatch):
    """When start_run is called without variables, existing session variables
    should remain untouched."""

    async def success_stream(*args, **kwargs):
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)

    spec = make_spec()
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    attach_capture(service)

    result = await service.server_call(
        "session.ensure",
        {"variables": {"additional_instructions": "Do X"}},
    )
    session_id = result["session_id"]

    run_info = await service.start_run("hello", {"mode": "none"}, session_id=session_id)
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)

    # Existing variables stay put. workspace_root may also be injected
    # from the AgentService default — that's expected under strict mode
    # and is a separate concern from preservation.
    assert sess.variables["additional_instructions"] == "Do X"


@pytest.mark.asyncio
async def test_start_run_merges_context_with_existing(monkeypatch):
    """When a session already has context, start_run should merge new context
    into existing context rather than replacing it."""

    async def success_stream(*args, **kwargs):
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)

    spec = make_spec()
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    attach_capture(service)

    result = await service.server_call(
        "session.ensure",
        {"context": {"workspace_root": "/ws", "custom_key": "abc"}},
    )
    session_id = result["session_id"]

    run_info = await service.start_run(
        "hello",
        {"mode": "none"},
        session_id=session_id,
        context={"new_key": "new_val"},
    )
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)

    assert "workspace_root" not in sess.context
    assert sess.context["custom_key"] == "abc"
    assert sess.context["new_key"] == "new_val"


# ---------------------------------------------------------------------------
# Explicit Run environment selection.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_targetless_run_binds_no_workspace(monkeypatch):
    captured = {}

    async def success_stream(*args, **kwargs):
        captured.update(kwargs)
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    attach_capture(service)

    run_info = await service.start_run("prompt", {"mode": "none"})
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)

    assert captured["environment_lease"] is None
    assert "workspace_root" not in (sess.context or {})


@pytest.mark.asyncio
async def test_explicit_host_environment_supplies_run_lease(monkeypatch, tmp_path):
    captured = {}

    async def success_stream(*args, **kwargs):
        captured.update(kwargs)
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)
    resources = AgentHostResources.create(
        "test-unit-core-agents-service_start_run-host"
    )
    descriptor = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    service = AgentService(make_spec(), resources)
    service.channel = object()
    attach_capture(service)

    run_info = await service.start_run(
        "prompt",
        {
            "mode": "explicit",
            "environment_id": descriptor.environment_id,
            "environment_generation": descriptor.generation,
        },
    )
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)

    lease = captured["environment_lease"]
    assert lease.environment_id == descriptor.environment_id
    assert lease.generation == descriptor.generation
    assert sess.context["workspace_root"] == str(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_field", ["context", "variables"])
async def test_workspace_path_cannot_select_execution(monkeypatch, metadata_field):
    async def success_stream(*args, **kwargs):
        return {"usage": {"input_tokens": 1}, "model": "m"}

    monkeypatch.setattr(service_module, "stream_agent", success_stream)
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-service_start_run-host"),
    )
    service.channel = object()
    attach_capture(service)

    run_info = await service.start_run(
        "prompt",
        {"mode": "none"},
        **{metadata_field: {"workspace_root": "/attempted/target"}},
    )
    sess = service._session_manager.get_or_create(run_info["session_id"])
    await asyncio.wait_for(sess.current_task, timeout=1)

    assert "workspace_root" not in (getattr(sess, metadata_field) or {})
