import asyncio
import contextlib

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec


class FakeChannel:
    def __init__(self, name):
        self.name = name
        self.events: list[tuple[str, dict]] = []

    async def async_call(self, method: str, args: dict | None = None):
        self.events.append((method, dict(args or {})))


class DummyAgent:
    def __init__(self, name: str = "dummy"):
        self.name = name
        self.tools = []
        self.instructions = ""
        self.model = "dummy"
        self.model_settings = {}
        self.approval_callback = None

    def set_approval_callback(self, callback):
        self.approval_callback = callback


@pytest.mark.asyncio
async def test_tool_approval_stays_with_run_channel():
    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )

    service = AgentService(spec, default_workspace_root="/tmp")

    ch1 = FakeChannel("ch1")
    ch2 = FakeChannel("ch2")

    sess1 = service._session_manager.get_or_create(None)
    sess2 = service._session_manager.get_or_create(None)

    run_id = "run_test"

    if ch1 not in sess1.channels:
        sess1.channels.append(ch1)
    if ch2 not in sess2.channels:
        sess2.channels.append(ch2)

    sess1.run_channels[run_id] = ch1

    approval_task = asyncio.create_task(
        service._request_tool_approval("tool_a", "{}", sess1, None, _run_id=run_id)
    )

    await asyncio.sleep(0)

    assert ch1.events, "run channel should receive approval request"
    method, payload = ch1.events[0]
    assert method == "client_request"
    assert payload.get("function") == "ui.request_tool_approval"
    assert payload.get("session_id") == sess1.id
    assert payload.get("run_id") == run_id
    request_id = payload.get("request_id")
    assert request_id
    assert not ch2.events, "other session must not receive approval request"

    await service.client_response(
        request_id=request_id,
        ok=True,
        result={"approved": True, "always_approve": False, "error_message": None},
        error=None,
    )

    result = await approval_task
    assert result == (True, False, None)


@pytest.mark.asyncio
async def test_tool_approval_does_not_fall_back_to_unrelated_channel():
    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )

    service = AgentService(spec, default_workspace_root="/tmp")

    primary_channel = FakeChannel("primary")
    unrelated_channel = FakeChannel("unrelated")

    session = service._session_manager.get_or_create(None)
    session.channels.append(primary_channel)
    run_id = "run_primary"
    session.run_channels[run_id] = primary_channel

    service.channel = unrelated_channel

    approval_task = asyncio.create_task(
        service._request_tool_approval("tool_b", "{}", session, None, _run_id=run_id)
    )

    await asyncio.sleep(0)

    assert primary_channel.events, "primary channel should receive approval"
    method, payload = primary_channel.events[0]
    assert method == "client_request"
    assert payload.get("run_id") == run_id
    assert payload.get("session_id") == session.id
    request_id = payload.get("request_id")
    assert request_id

    assert (
        not unrelated_channel.events
    ), "unrelated service channel must not receive approval"

    await service.client_response(
        request_id=request_id,
        ok=True,
        result={"approved": True, "always_approve": False, "error_message": None},
        error=None,
    )

    result = await approval_task
    assert result == (True, False, None)


@pytest.mark.asyncio
async def test_tool_approval_requires_session_context():
    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )

    service = AgentService(spec, default_workspace_root="/tmp")

    with pytest.raises(RuntimeError):
        await service._request_tool_approval("tool", "{}", None)


@pytest.mark.asyncio
async def test_tool_approval_queues_when_no_channels():
    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )

    service = AgentService(spec, default_workspace_root="/tmp")
    sess = service._session_manager.get_or_create(None)

    approval_task = asyncio.create_task(
        service._request_tool_approval("tool", "{}", sess, None, _run_id="run_id")
    )

    await asyncio.sleep(0)

    assert (
        sess.pending_events
    ), "Approval request should be queued when no channels are attached"
    method, payload = sess.pending_events[0]
    assert method == "client_request"
    assert payload["function"] == "ui.request_tool_approval"
    assert payload["run_id"] == "run_id"
    assert payload["session_id"] == sess.id

    # Since no client will respond, cancel task to avoid hang
    approval_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await approval_task


@pytest.mark.asyncio
async def test_queued_tool_approval_replays_on_start_run(monkeypatch):
    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )

    service = AgentService(spec, default_workspace_root="/tmp")
    session = service._session_manager.get_or_create("sess")

    approval_task = asyncio.create_task(
        service._request_tool_approval("tool", "{}", session, None, _run_id="run")
    )

    await asyncio.sleep(0)

    channel = FakeChannel("reconnect")
    service.channel = channel

    async def fake_stream_agent(agent, prompt, session_arg, channel_arg, **kwargs):
        return {}

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent",
        fake_stream_agent,
    )

    try:
        result = await service.start_run("resume", session_id=session.id)
        session_ref = service._session_manager.get_or_create(result["session_id"])
        run_task = session_ref.current_task

        async def wait_for_request():
            for _ in range(50):
                for method, payload in channel.events:
                    if (
                        method == "client_request"
                        and payload.get("function") == "ui.request_tool_approval"
                    ):
                        return payload
                await asyncio.sleep(0)
            raise AssertionError("approval request not delivered")

        payload = await wait_for_request()

        await service.client_response(
            request_id=payload["request_id"],
            ok=True,
            result={
                "approved": True,
                "always_approve": False,
                "error_message": None,
            },
            error=None,
        )

        result_tuple = await approval_task
        assert result_tuple == (True, False, None)
        assert session.pending_events == []
        await asyncio.wait_for(run_task, timeout=1)
    finally:
        if not approval_task.done():
            approval_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await approval_task


@pytest.mark.asyncio
async def test_agent_service_rebuilds_agent_every_run(monkeypatch):
    build_calls: list[DummyAgent] = []

    async def fake_stream_agent(agent, prompt, session, channel, **kwargs):
        return {}

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent",
        fake_stream_agent,
    )

    async def builder(settings, mcp, app):
        agent = DummyAgent()
        build_calls.append(agent)
        return agent

    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=builder,
    )

    service = AgentService(spec, default_workspace_root="/tmp")

    first = await service.start_run("prompt-1", session_id="sess")
    first_session = service._session_manager.get_or_create(first["session_id"])
    await asyncio.wait_for(first_session.current_task, timeout=1)

    second = await service.start_run("prompt-2", session_id=first_session.id)
    second_session = service._session_manager.get_or_create(second["session_id"])
    await asyncio.wait_for(second_session.current_task, timeout=1)

    assert len(build_calls) == 2
    assert build_calls[0] is not build_calls[1]


@pytest.mark.asyncio
async def test_tool_approval_requests_stay_with_origin_session(monkeypatch):
    shared_agent = DummyAgent("shared")

    async def builder(settings, mcp, app):
        return shared_agent

    run1_ready = asyncio.Event()
    run1_release = asyncio.Event()

    async def fake_stream_agent(agent, prompt, session, channel, **kwargs):
        if prompt == "first":
            run1_ready.set()
            await run1_release.wait()
            await agent.approval_callback("dangerous_tool", "{}", None)
        else:
            await agent.approval_callback("dangerous_tool", "{}", None)
        return {}

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent",
        fake_stream_agent,
    )

    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=builder,
    )

    service = AgentService(spec, default_workspace_root="/tmp")

    sess1 = service._session_manager.get_or_create("s1")
    sess2 = service._session_manager.get_or_create("s2")
    ch1 = FakeChannel("ch1")
    ch2 = FakeChannel("ch2")
    sess1.channels.append(ch1)
    sess2.channels.append(ch2)

    run1_task = asyncio.create_task(service.start_run("first", session_id=sess1.id))
    await run1_ready.wait()
    run1_run_task = sess1.current_task

    run2_result = await service.start_run("second", session_id=sess2.id)
    sess2_ref = service._session_manager.get_or_create(run2_result["session_id"])
    run2_run_task = sess2_ref.current_task
    run1_release.set()

    run1_result = await run1_task

    sess1_ref = service._session_manager.get_or_create(run1_result["session_id"])
    run1_run_task = run1_run_task or sess1_ref.current_task

    processed: set[str] = set()
    approvals: list[tuple[str, dict]] = []

    async def drain_requests():
        delivered = False
        for channel_name, channel in (("ch1", ch1), ("ch2", ch2)):
            for method, payload in list(channel.events):
                if method != "client_request":
                    continue
                if payload.get("function") != "ui.request_tool_approval":
                    continue
                req_id = payload["request_id"]
                if req_id in processed:
                    continue
                processed.add(req_id)
                approvals.append((channel_name, payload))
                await service.client_response(
                    request_id=req_id,
                    ok=True,
                    result={
                        "approved": True,
                        "always_approve": False,
                        "error_message": None,
                    },
                    error=None,
                )
                delivered = True
        return delivered

    async def wait_for_approvals():
        while len(approvals) < 2:
            await asyncio.sleep(0)
            await drain_requests()

    await asyncio.wait_for(wait_for_approvals(), timeout=1)

    await asyncio.wait_for(run1_run_task, timeout=1)
    await asyncio.wait_for(run2_run_task, timeout=1)

    total_ch1 = sum(1 for name, _ in approvals if name == "ch1")
    total_ch2 = sum(1 for name, _ in approvals if name == "ch2")

    assert total_ch1 == 1
    assert total_ch2 == 1


@pytest.mark.asyncio
async def test_always_approve_remains_session_local(monkeypatch):
    async def builder(settings, mcp, app):
        return DummyAgent()

    recorded: list[tuple[str, str]] = []

    async def fake_stream_agent(agent, prompt, session, channel, **kwargs):
        allowed = getattr(session, "always_allow_tools", set())
        if "dangerous_tool" in allowed:
            recorded.append(("auto", session.id))
            return {}
        recorded.append(("approval", session.id))
        await agent.approval_callback("dangerous_tool", "{}", None)
        return {}

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent",
        fake_stream_agent,
    )

    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=builder,
    )

    service = AgentService(spec, default_workspace_root="/tmp")

    sess1 = service._session_manager.get_or_create("s1")
    sess2 = service._session_manager.get_or_create("s2")
    ch1 = FakeChannel("ch1")
    ch2 = FakeChannel("ch2")
    sess1.channels.append(ch1)
    sess2.channels.append(ch2)

    processed: set[str] = set()
    requests: list[tuple[str, dict]] = []

    async def process_requests():
        updated = False
        for channel_name, channel in (("ch1", ch1), ("ch2", ch2)):
            for method, payload in list(channel.events):
                if method != "client_request":
                    continue
                if payload.get("function") != "ui.request_tool_approval":
                    continue
                req_id = payload["request_id"]
                if req_id in processed:
                    continue
                processed.add(req_id)
                requests.append((channel_name, payload))
                idx = len(requests)
                if idx == 1:
                    result = {
                        "approved": True,
                        "always_approve": True,
                        "error_message": None,
                    }
                else:
                    result = {
                        "approved": True,
                        "always_approve": False,
                        "error_message": None,
                    }
                await service.client_response(
                    request_id=req_id,
                    ok=True,
                    result=result,
                    error=None,
                )
                updated = True
        return updated

    async def wait_for_request_count(expected: int, timeout: float = 1.0):
        async def _wait():
            while len(requests) < expected:
                await asyncio.sleep(0)
                await process_requests()

        await asyncio.wait_for(_wait(), timeout=timeout)

    first = await service.start_run("first", session_id=sess1.id)
    sess1_ref = service._session_manager.get_or_create(first["session_id"])
    first_run_task = sess1_ref.current_task
    await wait_for_request_count(1)
    await asyncio.wait_for(first_run_task, timeout=1)

    second = await service.start_run("second", session_id=sess1_ref.id)
    sess1_ref = service._session_manager.get_or_create(second["session_id"])
    second_run_task = sess1_ref.current_task
    try:
        await wait_for_request_count(2, timeout=0.2)
        second_had_request = True
    except asyncio.TimeoutError:
        second_had_request = False

    if second_had_request:
        pytest.fail("Second run should not trigger a new approval request")

    await asyncio.wait_for(second_run_task, timeout=1)

    third = await service.start_run("third", session_id=sess2.id)
    sess2_ref = service._session_manager.get_or_create(third["session_id"])
    third_run_task = sess2_ref.current_task
    await wait_for_request_count(2)
    await asyncio.wait_for(third_run_task, timeout=1)

    assert recorded[0][0] == "approval"
    assert recorded[0][1] == sess1.id
    assert recorded[1][0] == "auto"
    assert recorded[1][1] == sess1.id
    assert recorded[2][0] == "approval"
    assert recorded[2][1] == sess2.id
