import asyncio
import types

import pytest

from omniagents.core.agents.service import AgentService

from .test_agent_builder import make_spec


def build_service() -> AgentService:
    return AgentService(make_spec(), default_workspace_root="/tmp")


@pytest.mark.asyncio
async def test_stop_run_delegates_to_session_manager():
    service = build_service()

    class StubManager:
        def __init__(self):
            self.cancel_called_with = None

        async def cancel(self, run_id: str) -> bool:
            self.cancel_called_with = run_id
            return True

    manager = StubManager()
    service._session_manager = manager

    result = await service.stop_run("run-123")

    assert result is True
    assert manager.cancel_called_with == "run-123"


@pytest.mark.asyncio
async def test_stop_run_cancels_active_run(monkeypatch):
    service = build_service()

    class RecordingChannel:
        def __init__(self):
            self.calls = []

        async def async_call(self, method: str, args: dict | None = None):
            self.calls.append((method, args or {}))

    channel = RecordingChannel()
    service.channel = channel

    async def fake_builder(self, session_arg, approval_callback):
        return object()

    service._build_agent_for_session = types.MethodType(fake_builder, service)

    started = asyncio.Event()

    async def fake_stream(agent, prompt, session_arg, channel_arg, **kwargs):
        started.set()
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent",
        fake_stream,
    )

    response = await service.start_run("prompt")
    run_id = response["run_id"]
    session = service._session_manager.get_by_run_id(run_id)
    task = session.current_task

    await asyncio.wait_for(started.wait(), timeout=1)
    assert run_id in session.run_channels

    stopped = await service.stop_run(run_id)

    assert stopped is True

    [result] = await asyncio.gather(task, return_exceptions=True)

    for _ in range(10):
        if run_id not in session.run_channels:
            break
        await asyncio.sleep(0.05)

    assert isinstance(result, asyncio.CancelledError)
    assert run_id not in session.run_channels
    assert session.current_task is None

    end_events = [call for call in channel.calls if call[0] == "run_end"]
    assert end_events
    payload = end_events[-1][1]
    assert payload["run_id"] == run_id
    assert payload["end_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_send_user_message_appends_history():
    service = build_service()

    class StubSession:
        def __init__(self):
            self.messages = []

        def append_message(self, message: dict) -> None:
            self.messages.append(message)

    class StubManager:
        def __init__(self):
            self.session = StubSession()

        def get_by_run_id(self, run_id: str):
            assert run_id == "run-123"
            return self.session

    manager = StubManager()
    service._session_manager = manager

    await service.send_user_message("run-123", "hello")

    assert manager.session.messages == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_send_user_message_missing_session_raises():
    service = build_service()

    class StubManager:
        def get_by_run_id(self, run_id: str):
            assert run_id == "missing"
            return None

    service._session_manager = StubManager()

    with pytest.raises(RuntimeError):
        await service.send_user_message("missing", "hello")


@pytest.mark.asyncio
async def test_client_functions_records_capabilities():
    service = build_service()
    channel = object()
    service.channel = channel

    result = await service.client_functions(
        version=2,
        functions=[{"name": "ping"}],
    )

    assert result is True
    assert service._client_capabilities[channel] == {
        "version": 2,
        "functions": [{"name": "ping"}],
    }


class RespondingChannel:
    def __init__(self, service: AgentService, response: dict):
        self.service = service
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def async_call(self, method: str, args: dict | None = None):
        self.calls.append((method, args or {}))
        if method == "client_request" and args is not None:
            loop = asyncio.get_running_loop()
            loop.call_soon(
                loop.create_task,
                self.service.client_response(
                    args["request_id"],
                    self.response.get("ok", True),
                    self.response.get("result"),
                    self.response.get("error"),
                ),
            )


class SilentChannel:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def async_call(self, method: str, args: dict | None = None):
        self.calls.append((method, args or {}))


class FailingChannel:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def async_call(self, method: str, args: dict | None = None):
        self.calls.append((method, args or {}))
        raise RuntimeError("fail")


@pytest.mark.asyncio
async def test_request_client_function_waits_for_response():
    service = build_service()
    session = types.SimpleNamespace(id="sess-1", channels=[], run_channels={})
    channel = RespondingChannel(
        service,
        {"ok": True, "result": {"value": 42}, "error": None},
    )
    session.channels.append(channel)

    result = await service.request_client_function(
        "demo",
        {"x": 1},
        session=session,
        expect_result=True,
    )

    assert result == {"ok": True, "result": {"value": 42}, "error": None}
    assert len(channel.calls) == 1
    method, payload = channel.calls[0]
    assert method == "client_request"
    assert payload["function"] == "demo"
    assert payload["args"] == {"x": 1}
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_request_client_function_timeout_returns_none():
    service = build_service()
    session = types.SimpleNamespace(id="sess-2", channels=[], run_channels={})
    channel = SilentChannel()
    session.channels.append(channel)

    result = await service.request_client_function(
        "demo",
        {"x": 1},
        session=session,
        expect_result=True,
        timeout_ms=1,
    )

    assert result is None
    assert channel.calls[0][0] == "client_request"


@pytest.mark.asyncio
async def test_request_client_function_reports_delivery_without_result():
    service = build_service()
    session = types.SimpleNamespace(id="sess-3", channels=[], run_channels={})
    channel = SilentChannel()
    session.channels.append(channel)

    delivered = await service.request_client_function(
        "ping",
        {"y": 2},
        session=session,
    )

    assert delivered is True
    assert channel.calls[0][0] == "client_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("delivered", [True, False])
async def test_set_client_status_reflects_delivery(delivered):
    service = build_service()
    captured = {}

    async def fake_request(
        self,
        function: str,
        args: dict | None = None,
        *,
        session=None,
        run_id=None,
        timeout_ms=None,
        idempotency_key=None,
        expect_result=False,
    ):
        captured["function"] = function
        captured["args"] = args
        captured["session"] = session
        captured["run_id"] = run_id
        return delivered

    service.request_client_function = types.MethodType(fake_request, service)
    session = types.SimpleNamespace(id="sess", channels=[], run_channels={})

    result = await service.set_client_status(
        "Working",
        session=session,
        run_id="run-5",
        show_spinner=False,
    )

    assert result is bool(delivered)
    assert captured == {
        "function": "ui.set_status",
        "args": {"text": "Working", "show_spinner": False},
        "session": session,
        "run_id": "run-5",
    }


@pytest.mark.asyncio
async def test_safe_notify_returns_false_on_exception():
    service = build_service()
    channel = FailingChannel()

    result = await service._safe_notify(channel, "event", {"value": 1})

    assert result is False
    assert channel.calls == [("event", {"value": 1})]


@pytest.mark.asyncio
async def test_broadcast_session_removes_dead_channels():
    service = build_service()
    session = service._session_manager.get_or_create()
    good = SilentChannel()
    bad = FailingChannel()
    session.channels.extend([good, bad])

    delivered = await service._broadcast_session(session, "event", {"k": "v"})

    assert delivered is True
    assert good.calls == [("event", {"k": "v"})]
    assert bad not in session.channels


@pytest.mark.asyncio
async def test_broadcast_session_queues_when_no_channels():
    service = build_service()
    session = service._session_manager.get_or_create()
    session.channels.clear()
    session.pending_events = []

    delivered = await service._broadcast_session(
        session,
        "notice",
        {"x": 1},
        queue_if_unseen=True,
    )

    assert delivered is False
    assert session.pending_events == [("notice", {"x": 1})]


@pytest.mark.asyncio
async def test_register_server_function_metadata_and_invocation():
    service = build_service()
    calls = []

    async def on_invoke(service_arg, session_arg, args):
        calls.append(("on_invoke", session_arg, args))
        return "via-on"

    def handler(service_arg, session_arg, args):
        calls.append(("handler", session_arg.id if session_arg else None, args))
        return {"handled": True}

    handler._server_function_strict = False

    service.register_server_function(
        "with_on_invoke",
        handler,
        description="desc",
        params_schema={"type": "object"},
        result_schema={"type": "string"},
        on_invoke=on_invoke,
        strict=True,
    )
    service.register_server_function("direct_handler", handler)

    session = service._session_manager.get_or_create()

    result_on = await service.server_call(
        "with_on_invoke",
        {"value": 1},
        session_id=session.id,
    )
    result_direct = await service.server_call(
        "direct_handler",
        {"x": 2},
        session_id=session.id,
    )

    assert result_on == "via-on"
    assert result_direct == {"handled": True}
    assert calls[0][0] == "on_invoke"
    assert calls[0][1] is session
    assert calls[0][2] == {"value": 1}
    assert calls[1] == ("handler", session.id, {"x": 2})

    metadata = await service.list_server_functions()
    assert metadata[0] == {
        "name": "session.ensure",
        "description": "Ensure a session exists and persist workspace metadata.",
        "params_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "workspace_root": {"type": "string"},
                "context": {"type": "object"},
                "variables": {"type": "object"},
            },
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "workspace_root": {"type": "string"},
                "context": {"type": "object"},
                "variables": {"type": "object"},
            },
            "required": ["session_id"],
        },
        "strict": False,
    }
    assert metadata[1] == {
        "name": "with_on_invoke",
        "description": "desc",
        "params_schema": {"type": "object"},
        "result_schema": {"type": "string"},
        "strict": True,
    }
    assert metadata[2] == {
        "name": "direct_handler",
        "description": None,
        "params_schema": None,
        "result_schema": None,
        "strict": False,
    }


@pytest.mark.asyncio
async def test_server_call_unknown_function_raises():
    service = build_service()

    with pytest.raises(ValueError):
        await service.server_call("missing")


@pytest.mark.asyncio
async def test_session_ensure_sets_workspace_root_and_persists():
    service = build_service()

    result = await service.server_call(
        "session.ensure",
        {"workspace_root": "/workspace"},
    )

    session_id = result["session_id"]
    assert result["workspace_root"] == "/workspace"
    assert result["context"]["workspace_root"] == "/workspace"
    assert result["variables"]["workspace_root"] == "/workspace"

    new_service = build_service()
    restored = new_service._session_manager.get_or_create(session_id)
    assert restored.context["workspace_root"] == "/workspace"
    assert restored.variables["workspace_root"] == "/workspace"


@pytest.mark.asyncio
async def test_tool_approval_always_routes_mcp_tuple_to_mcp_allowlist():
    """Always-approving an MCP-derived call lands the (server, tool) tuple in
    ``always_allow_mcp_tools``; plain tools keep landing in the flat set."""
    service = build_service()
    sess = types.SimpleNamespace(id="s1", channels=[])

    # MCP-derived call: tracker holds the identity tuple.
    fut = asyncio.get_event_loop().create_future()
    sess._pending_approval_tool_names = {"call-mcp": ("omni-projects", "create_ticket")}
    service._pending_tool_approvals["call-mcp"] = (fut, sess)
    ok = await service.tool_approval_response("call-mcp", "approve", always_approve=True)
    assert ok is True
    assert ("omni-projects", "create_ticket") in sess.always_allow_mcp_tools
    assert not getattr(sess, "always_allow_tools", None)
    assert (await fut)["decision"] == "approve"

    # Plain function tool: tracker holds the bare name.
    fut2 = asyncio.get_event_loop().create_future()
    sess._pending_approval_tool_names["call-fn"] = "execute_bash"
    service._pending_tool_approvals["call-fn"] = (fut2, sess)
    ok2 = await service.tool_approval_response("call-fn", "approve", always_approve=True)
    assert ok2 is True
    assert "execute_bash" in sess.always_allow_tools
    assert ("omni-projects", "create_ticket") in sess.always_allow_mcp_tools
