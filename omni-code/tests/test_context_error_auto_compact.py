import asyncio

import agents

from omniagents.core.runtime.bridge import stream_agent
from omni_agents.omni.runtime_hooks.error import on_error


class FakeRpcChannel:
    async def async_call(self, *_args, **_kwargs):
        return None


class FakeSession:
    def __init__(self):
        self.id = "sess-1"
        self.history = []
        self.pending_events = []
        self.current_stream_result = None

    def append_message(self, item):
        self.history.append(item)


class FakeService:
    def __init__(self):
        self.calls = []

    async def invoke_runtime_hooks(self, event: str, **kwargs):
        if event != "error":
            return None
        return await on_error(service=self, event=event, **kwargs)

    async def server_call(self, function: str, args=None, session_id: str | None = None):
        self.calls.append({"function": function, "args": args, "session_id": session_id})


class FakeContextError(Exception):
    def __init__(self):
        super().__init__("context")
        self.body = {"error": {"code": "context_length_exceeded"}}


class FakeLiteLLMContextError(Exception):
    pass


class FakeStream:
    async def stream_events(self):
        if False:
            yield None


def test_stream_agent_triggers_compact_and_retries(monkeypatch):
    calls = {"count": 0}

    def fake_run_streamed(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FakeContextError()
        return FakeStream()

    monkeypatch.setattr(agents.Runner, "run_streamed", fake_run_streamed)

    service = FakeService()
    session = FakeSession()
    session.variables = {}
    rpc = FakeRpcChannel()

    async def run():
        return await stream_agent(
            agent=object(),
            prompt="hello",
            session=session,
            rpc_channel=rpc,
            service=service,
        )

    result = asyncio.run(run())
    assert calls["count"] == 2
    assert result is not None
    assert service.calls == [{"function": "compact", "args": {}, "session_id": "sess-1"}]
    assert [item.get("role") for item in session.history].count("user") == 1


def test_stream_agent_triggers_compact_for_litellm_context_error(monkeypatch):
    calls = {"count": 0}

    FakeLiteLLMContextError.__name__ = "ContextWindowExceededError"

    def fake_run_streamed(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FakeLiteLLMContextError("litellm.ContextWindowExceededError: boom")
        return FakeStream()

    monkeypatch.setattr(agents.Runner, "run_streamed", fake_run_streamed)

    service = FakeService()
    session = FakeSession()
    session.variables = {}
    rpc = FakeRpcChannel()

    async def run():
        return await stream_agent(
            agent=object(),
            prompt="hello",
            session=session,
            rpc_channel=rpc,
            service=service,
        )

    asyncio.run(run())
    assert calls["count"] == 2
    assert service.calls == [{"function": "compact", "args": {}, "session_id": "sess-1"}]
