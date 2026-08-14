import asyncio

import pytest

from omniagents.execution import AgentHostResources
from omniagents.core.agents.service import AgentService, _safe_slug
from omniagents.core.agents.specs import AgentSpec


@pytest.mark.asyncio
async def test_archive_session_triggers_terminal_cleanup(temp_history_db):
    spec = AgentSpec(
        name="name",
        welcome_text="welcome",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )

    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )
    session = service._session_manager.get_or_create()
    calls = []

    class DummyTerminalManager:
        async def cleanup_session(self, session_id):
            calls.append(session_id)

    service.terminal_manager = DummyTerminalManager()

    result = await service.archive_session(session.id)

    assert result is True
    assert calls == [session.id]


@pytest.mark.asyncio
async def test_get_run_state_reports_active_run(temp_history_db):
    """The light presence probe: a supervising client (the launcher's
    resident watcher) asks this at connect instead of assuming idle when
    a run was already in flight before it attached."""
    spec = AgentSpec(
        name="name",
        welcome_text="welcome",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )
    session = service._session_manager.get_or_create("sess_probe")

    state = await service.get_run_state("sess_probe")
    assert state["active_run_id"] is None

    session.active_run_id = "run_42"
    state = await service.get_run_state("sess_probe")
    assert state == {"session_id": "sess_probe", "active_run_id": "run_42"}


@pytest.mark.parametrize(
    "value,fallback,expected",
    [
        ("Hello World", "fb", "hello_world"),
        (None, "backup", "backup"),
        ("", "fallback", "fallback"),
        ("Already_Safe", "fallback", "already_safe"),
    ],
)
def test_safe_slug_variants(value, fallback, expected):
    assert _safe_slug(value, fallback) == expected


def make_spec():
    return AgentSpec(
        name="name",
        welcome_text="welcome",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )


@pytest.mark.asyncio
async def test_request_client_function_returns_envelope():
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )

    class StubChannel:
        def __init__(self):
            self.calls = []

        async def async_call(self, method, args):
            self.calls.append((method, args))

            async def respond():
                await asyncio.sleep(0)
                await service.client_response(
                    request_id=args["request_id"],
                    ok=True,
                    result={"approved": True},
                    error=None,
                )

            asyncio.create_task(respond())
            return True

    channel = StubChannel()
    service.channel = channel
    result = await service.request_client_function(
        "fn",
        {"foo": "bar"},
        expect_result=True,
        timeout_ms=200,
    )
    assert channel.calls
    assert result["ok"] is True
    assert result["result"] == {"approved": True}


@pytest.mark.asyncio
async def test_request_client_function_timeout():
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )

    class SilentChannel:
        async def async_call(self, method, args):
            return True

    service.channel = SilentChannel()
    result = await service.request_client_function(
        "fn",
        {},
        expect_result=True,
        timeout_ms=1,
    )
    assert result is None


@pytest.mark.asyncio
async def test_request_tool_approval_queues_and_adds_safe():
    # SDK 0.16+: approval flows through tool_approval_requested events and
    # the tool_approval_response RPC. With no channel attached, the event
    # is queued on session.pending_tool_approvals for replay; "always
    # approve" mutates session.always_allow_tools.
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )
    session = service._session_manager.get_or_create()
    session.pending_events = []

    async def responder():
        # Wait until the request has been queued before responding.
        while "call_xyz" not in session.pending_tool_approvals:
            await asyncio.sleep(0)
        await service.tool_approval_response(
            call_id="call_xyz",
            decision="approve",
            always_approve=True,
        )

    task = asyncio.create_task(responder())
    response = await service.request_tool_approval(
        session=session,
        call_id="call_xyz",
        tool_name="tool_name",
        arguments=None,
    )
    await task
    assert response["decision"] == "approve"
    assert response["always_approve"] is True
    assert response["rejection_message"] is None
    assert "tool_name" in session.always_allow_tools
    # Pending tracking is cleared once resolved.
    assert "call_xyz" not in session.pending_tool_approvals
    assert "call_xyz" not in service._pending_tool_approvals


@pytest.mark.asyncio
async def test_start_run_applies_safe_mcp_tool_overrides(monkeypatch):
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )

    async def fail_build(*args, **kwargs):
        raise RuntimeError("stop before worker")

    monkeypatch.setattr(service, "_build_agent_for_session", fail_build)

    with pytest.raises(RuntimeError, match="stop before worker"):
        await service.start_run(
            "hello",
            {"mode": "none"},
            session_id="sess_mcp_safe",
            safe_tool_overrides={
                "safe_tool_names": ["read_file"],
                "safe_tool_patterns": ["^get_"],
                "safe_mcp_tools": [
                    {"server_label": "github", "tool_name": "search_issues"},
                    {"server_label": "github", "tool_name": ""},
                    {"tool_name": "missing_server"},
                    "invalid",
                ],
            },
        )

    session = service._session_manager.get_or_create("sess_mcp_safe")
    assert "read_file" in session.always_allow_tools
    assert "^get_" in session.always_allow_patterns
    assert session.always_allow_mcp_tools == {("github", "search_issues")}


@pytest.mark.asyncio
async def test_request_mcp_approval_auto_approves_allowlisted_tool():
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )
    session = service._session_manager.get_or_create()
    session.always_allow_mcp_tools.add(("github", "search_issues"))

    class Recorder:
        def __init__(self):
            self.calls = []

        async def async_call(self, method, args):
            self.calls.append((method, args))
            return True

    recorder = Recorder()
    session.channels.append(recorder)

    response = await service.request_mcp_approval(
        session=session,
        request_id="mcp_req_allowed",
        server_label="github",
        tool_name="search_issues",
        arguments="{}",
        run_id="run1",
    )

    assert response == {"decision": "approve", "rejection_message": None}
    assert recorder.calls == []
    assert "mcp_req_allowed" not in service._pending_mcp_approvals
    assert "mcp_req_allowed" not in session.pending_mcp_approvals


@pytest.mark.asyncio
async def test_request_mcp_approval_still_broadcasts_for_non_allowlisted_tool():
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )
    session = service._session_manager.get_or_create()
    session.always_allow_mcp_tools.add(("github", "search_issues"))

    class Recorder:
        def __init__(self):
            self.calls = []

        async def async_call(self, method, args):
            self.calls.append((method, args))
            return True

    recorder = Recorder()
    session.channels.append(recorder)

    approval_task = asyncio.create_task(
        service.request_mcp_approval(
            session=session,
            request_id="mcp_req_pending",
            server_label="github",
            tool_name="create_issue",
            arguments='{"title":"Bug"}',
            run_id="run1",
        )
    )

    async def wait_for_broadcast():
        while not recorder.calls:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_broadcast(), timeout=1)

    method, payload = recorder.calls[0]
    assert method == "mcp_approval_requested"
    assert payload["request_id"] == "mcp_req_pending"
    assert payload["server_label"] == "github"
    assert payload["tool_name"] == "create_issue"
    assert "mcp_req_pending" in session.pending_mcp_approvals

    assert await service.mcp_approval_response("mcp_req_pending", "approve") is True
    response = await approval_task

    assert response["decision"] == "approve"
    assert "mcp_req_pending" not in service._pending_mcp_approvals
    assert "mcp_req_pending" not in session.pending_mcp_approvals
    assert [method for method, _ in recorder.calls] == [
        "mcp_approval_requested",
        "mcp_approval_resolved",
    ]


@pytest.mark.asyncio
async def test_get_session_history_replays_live_state():
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-agent_service-host"),
    )
    session = service._session_manager.get_or_create()
    session.history = []
    session.active_run_started_payload = {
        "run_id": "run1",
        "session_id": session.id,
    }
    session.active_run_status_payload = {
        "run_id": "run1",
        "status": "retrying",
    }
    session.active_run_set_status_payload = {
        "request_id": "status_req",
        "function": "ui.set_status",
        "args": {"text": "Working", "show_spinner": True},
        "session_id": session.id,
    }
    session.pending_client_requests["pending_req"] = {
        "request_id": "pending_req",
        "function": "ui.request_tool_approval",
        "args": {"tool": "shell@run"},
        "session_id": session.id,
    }

    class Recorder:
        def __init__(self):
            self.calls = []

        async def async_call(self, method, args):
            self.calls.append((method, args))
            return True

    channel = Recorder()
    service.channel = channel
    result = await service.get_session_history(session.id)
    assert isinstance(result, list)
    methods = [m for m, _ in channel.calls]
    assert "run_started" in methods
    assert "run_status" in methods
    client_requests = [
        args for method, args in channel.calls if method == "client_request"
    ]
    assert len(client_requests) >= 2


@pytest.mark.asyncio
async def test_acquire_environment_for_session_default_fallback(
    temp_history_db, tmp_path
):
    """A session with no thread binding inherits nothing; the server-default
    environment (the one the GUI handshake advertises) is applied as an
    explicit fallback — the voice-before-first-text-run path."""
    from pathlib import Path

    from omniagents.execution import register_host_environment

    spec = AgentSpec(
        name="name",
        welcome_text="welcome",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )
    resources = AgentHostResources.create("test-acquire-env-host")
    default_env = register_host_environment(
        resources, str(tmp_path), owner_user_id=None
    )
    service = AgentService(spec, resources)

    # Unbound session, no default: inherit resolves to no environment.
    assert await service.acquire_environment_for_session("voice-sess-1") is None

    # Unbound session with the server default: explicit fallback lease.
    lease = await service.acquire_environment_for_session(
        "voice-sess-1", default_environment_id=default_env.environment_id
    )
    assert lease is not None
    assert lease.environment_id == default_env.environment_id
    assert Path(lease.workspace.root).resolve() == tmp_path.resolve()
