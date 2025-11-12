import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.rpc.agents.local import LocalAgent
from omniagents.rpc.transport import TransportClosedError


def make_agent_spec(**overrides):
    async def default_builder(settings, mcp_server, spec):
        return SimpleNamespace(
            name="builder-agent", tools=[], instructions="", model="gpt-4"
        )

    spec = AgentSpec(
        name="Test Agent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "instructions",
        build_agent=overrides.get("build_agent", default_builder),
    )
    spec.safe_agent_options = overrides.get("safe_agent_options", SafeAgentOptions())
    spec.use_safe_agent = overrides.get("use_safe_agent", False)
    spec.mcp_servers = overrides.get("mcp_servers", [])
    return spec


@pytest.mark.asyncio
async def test_initialize_restores_spec_builder():
    original_builder = AsyncMock()
    spec = make_agent_spec(build_agent=original_builder)
    agent = LocalAgent(spec, settings={"alpha": 1})

    await agent.initialize()
    assert spec.build_agent is not original_builder

    await agent.cleanup()
    assert spec.build_agent is original_builder


@pytest.mark.asyncio
async def test_custom_builder_preserves_safe_agent_callback(monkeypatch):
    callback = Mock()

    class FakeSafeAgent:
        call_args = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.callback = None

        @classmethod
        def set_global_safe_tools(cls, tool_names=None, tool_patterns=None):
            cls.call_args = (tool_names, tool_patterns)

        def set_approval_callback(self, cb):
            self.callback = cb

    safe_instance = FakeSafeAgent()
    original_builder = AsyncMock(return_value=safe_instance)
    spec = make_agent_spec(build_agent=original_builder)
    agent = LocalAgent(spec, approval_callback=callback)

    monkeypatch.setattr("omniagents.core.agents.safe.SafeAgent", FakeSafeAgent)

    await agent.initialize()
    built_agent = await spec.build_agent({}, None, None)
    assert built_agent.callback is callback
    assert FakeSafeAgent.call_args == ([], [])

    await agent.cleanup()


@pytest.mark.asyncio
async def test_custom_builder_wraps_non_safe_agent(monkeypatch):
    callback = Mock()

    class FakeSafe:
        call_args = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        @classmethod
        def set_global_safe_tools(cls, tool_names=None, tool_patterns=None):
            cls.call_args = (tool_names, tool_patterns)

    monkeypatch.setattr("omniagents.core.agents.safe.SafeAgent", FakeSafe)

    returned_agent = SimpleNamespace(
        name="core",
        tools=[],
        instructions="",
        model="gpt-4",
        model_settings=None,
        mcp_servers=[],
        handoffs=[],
        handoff_description=None,
        prompt=None,
    )

    original_builder = AsyncMock(return_value=returned_agent)
    spec = make_agent_spec(
        build_agent=original_builder,
        use_safe_agent=True,
        safe_agent_options=SafeAgentOptions(
            safe_tool_names=["ping"],
            safe_tool_patterns=["^safe-"],
            skip_approvals=False,
        ),
    )
    agent = LocalAgent(spec, approval_callback=callback)

    await agent.initialize()

    built_agent = await spec.build_agent({}, None, None)
    assert isinstance(built_agent, FakeSafe)
    assert built_agent.kwargs["approval_callback"] is callback
    assert built_agent.kwargs["safe_tool_names"] == ["ping"]
    assert FakeSafe.call_args == (["ping"], ["^safe-"])

    await agent.cleanup()


@pytest.mark.asyncio
async def test_list_and_archive_sessions(monkeypatch):
    spec = make_agent_spec()
    agent = LocalAgent(spec)

    async def noop():
        return None

    agent._ensure_client = AsyncMock(side_effect=noop)
    list_response = SimpleNamespace(result=[{"id": "s1"}])
    archive_calls = []

    class DummyOther:
        async def list_sessions(self):
            return list_response

        async def archive_session(self, session_id):
            archive_calls.append(session_id)

    agent._rpc_client = SimpleNamespace(other=DummyOther())

    sessions = await agent.list_sessions()
    assert sessions == list_response.result

    await agent.archive_session("s1")
    assert archive_calls == ["s1"]


@pytest.mark.asyncio
async def test_stop_run_success_and_failure():
    spec = make_agent_spec()
    agent = LocalAgent(spec)

    async def noop():
        return None

    agent._ensure_client = AsyncMock(side_effect=noop)

    class SuccessOther:
        async def stop_run(self, run_id):
            return SimpleNamespace(result=True)

    class FailingOther:
        async def stop_run(self, run_id):
            raise RuntimeError("boom")

    agent._rpc_client = SimpleNamespace(other=SuccessOther())
    agent._current_run_id = "run-1"
    success = await agent.stop_run()
    assert success is True
    assert agent._current_run_id is None

    agent._rpc_client = SimpleNamespace(other=FailingOther())
    agent._current_run_id = "run-2"
    failure = await agent.stop_run()
    assert failure is False
    assert agent._current_run_id == "run-2"


@pytest.mark.asyncio
async def test_stop_run_without_active_run():
    spec = make_agent_spec()
    agent = LocalAgent(spec)
    result = await agent.stop_run()
    assert result is False


@pytest.mark.asyncio
async def test_custom_builder_uses_default_when_missing(monkeypatch):
    captured = {}

    async def fake_default(settings, mcp_to_use, spec, approval_callback):
        captured["settings"] = settings
        captured["mcp"] = mcp_to_use
        captured["spec"] = spec
        captured["approval"] = approval_callback
        return SimpleNamespace(
            name="fallback",
            tools=[],
            instructions="",
            model="gpt-4",
            model_settings=None,
            mcp_servers=[],
            handoffs=[],
            handoff_description=None,
            prompt=None,
        )

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_default
    )

    spec = make_agent_spec(build_agent=None)
    agent = LocalAgent(
        spec,
        settings={"alpha": 5},
        approval_callback="cb",
        mcp_servers=["server-a"],
    )

    await agent.initialize()
    built = await spec.build_agent({}, "ignored", None)
    assert built.name == "fallback"
    assert captured["settings"] == {"alpha": 5}
    assert captured["mcp"] == ["server-a"]
    assert captured["approval"] == "cb"

    await agent.cleanup()


@pytest.mark.asyncio
async def test_ensure_client_initializes_rpc(monkeypatch):
    spec = make_agent_spec()
    agent = LocalAgent(spec)

    class DummyTransport:
        def __init__(self):
            self.calls = 0

        async def connect(self):
            self.calls += 1

    async def fake_initialize():
        agent._client_transport = DummyTransport()
        agent._initialized = True

    agent.initialize = AsyncMock(side_effect=fake_initialize)

    class FakeClient:
        def __init__(self, transport, methods):
            self.transport = transport
            self.methods = methods
            self.entered = False
            self.other = SimpleNamespace()

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.entered = False

    monkeypatch.setattr("omniagents.rpc.agents.local.JsonRpcClient", FakeClient)

    await agent._ensure_client()
    assert isinstance(agent._rpc_client, FakeClient)
    assert agent._rpc_client.entered is True
    assert agent._client_transport.calls == 1

    await agent._ensure_client()
    assert agent._client_transport.calls == 1


@pytest.mark.asyncio
async def test_run_streamed_emits_events(monkeypatch):
    spec = make_agent_spec()
    agent = LocalAgent(spec)

    async def noop():
        return None

    agent._ensure_client = AsyncMock(side_effect=noop)

    class DummyOther:
        async def start_run(self, **kwargs):
            return SimpleNamespace(result={"session_id": "sess", "run_id": "run"})

    agent._rpc_client = SimpleNamespace(other=DummyOther())

    async def produce():
        await asyncio.sleep(0)
        await agent._event_queue.put({"method": "token", "params": {"usage": 2}})
        await agent._event_queue.put(
            {"method": "run_end", "params": {"run_id": "run", "status": "ok"}}
        )

    async def consume(gen):
        events = []
        async for event in gen:
            events.append(event)
        return events

    runner = agent.run_streamed([{"role": "user", "content": "hello"}])
    gen = runner.stream_events()

    events, _ = await asyncio.gather(consume(gen), produce())

    assert events[0].name == "token_usage"
    assert agent.get_last_run_end() == {"run_id": "run", "status": "ok"}
    assert agent._current_run_id is None


@pytest.mark.asyncio
async def test_run_server_processes_requests():
    spec = make_agent_spec()
    agent = LocalAgent(spec)

    results = []
    errors = []

    class DummyChannel:
        async def send_result(self, call_id, result):
            results.append((call_id, result))

    class DummyTransport:
        def __init__(self):
            self.messages = [
                {"id": 1, "method": "echo", "params": {"value": 1}},
                "skip",
                {"id": 2, "method": "fail", "params": {}},
            ]
            self.index = 0
            self._connected = True

        def is_connected(self):
            return self._connected

        async def receive_json(self):
            if self.index >= len(self.messages):
                self._connected = False
                raise TransportClosedError("done")
            item = self.messages[self.index]
            self.index += 1
            if item == "skip":
                raise RuntimeError("bad payload")
            return item

        async def send_json(self, payload):
            errors.append(payload)

    agent._server_transport = DummyTransport()

    class Methods:
        async def echo(self, value):
            return value

        async def fail(self):
            raise ValueError("explode")

    endpoint = SimpleNamespace(methods=Methods())

    await agent._run_server(endpoint, DummyChannel())

    assert results == [(1, 1)]
    assert errors[0]["error"]["data"]["method"] == "fail"
