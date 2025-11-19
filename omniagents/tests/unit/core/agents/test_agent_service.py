import asyncio

import pytest

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

    service = AgentService(spec)
    session = service._session_manager.get_or_create()
    calls = []

    class DummyTerminalManager:
        async def cleanup_session(self, session_id):
            calls.append(session_id)

    service.terminal_manager = DummyTerminalManager()

    result = await service.archive_session(session.id)

    assert result is True
    assert calls == [session.id]


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
    service = AgentService(make_spec())

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
    service = AgentService(make_spec())

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
    service = AgentService(make_spec())
    session = service._session_manager.get_or_create()
    session.pending_events = []

    async def responder():
        while not session.pending_events:
            await asyncio.sleep(0)
        _, payload = session.pending_events[0]
        await asyncio.sleep(0)
        await service.client_response(
            request_id=payload["request_id"],
            ok=True,
            result={"approved": True, "always_approve": True},
            error=None,
        )

    task = asyncio.create_task(responder())
    approved, always, error = await service._request_tool_approval(
        "tool_name",
        "{}",
        session,
    )
    await task
    assert approved is True
    assert always is True
    assert error is None
    assert session.pending_events[0][0] == "client_request"
    assert "tool_name" in session.always_allow_tools
    assert session.pending_client_requests == {}
    assert service._client_request_sessions == {}


@pytest.mark.asyncio
async def test_get_session_history_replays_live_state():
    service = AgentService(make_spec())
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
