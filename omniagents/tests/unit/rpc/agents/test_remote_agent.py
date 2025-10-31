import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.rpc.agents.remote import RemoteAgent, _ClientEventMethods


@pytest.mark.asyncio
async def test_run_streamed_yields_events(monkeypatch):
    agent = RemoteAgent("ws://host")

    start_response = SimpleNamespace(result={"session_id": "sess-1", "run_id": "run-1"})

    monkeypatch.setattr("omniagents.rpc.agents.remote.Debug.enabled", lambda: True)

    class DummyOther:
        async def start_run(self, **kwargs):
            return start_response

    agent._client = SimpleNamespace(other=DummyOther())
    agent._ensure_client = AsyncMock()

    async def produce_events():
        await asyncio.sleep(0)
        await agent._event_queue.put({"method": "token", "params": {"usage": 1}})
        await agent._event_queue.put(
            {"method": "run_end", "params": {"run_id": "run-1", "end_reason": "done"}}
        )

    async def consume_events(gen):
        events = []
        async for event in gen:
            events.append(event)
        return events

    result = agent.run_streamed([{"role": "user", "content": "hey"}])
    events_gen = result.stream_events()

    events, _ = await asyncio.gather(consume_events(events_gen), produce_events())

    assert agent._session_id == "sess-1"
    assert agent._last_run_end == {"run_id": "run-1", "end_reason": "done"}
    assert agent._current_run_id is None
    assert events[0].name == "token_usage"


def test_extract_run_and_session_ids():
    agent = RemoteAgent("ws://host")

    resp_dict = {"run_id": "r1", "session_id": "s1"}
    assert agent._extract_run_id(resp_dict) == "r1"
    assert agent._extract_session_id(resp_dict) == "s1"

    resp_ns = SimpleNamespace(result={"run_id": "r2", "session_id": "s2"})
    assert agent._extract_run_id(resp_ns) == "r2"
    assert agent._extract_session_id(resp_ns) == "s2"

    json_payload = '{"run_id": "r3", "session_id": "s3"}'
    assert agent._extract_run_id(SimpleNamespace(result=json_payload)) == "r3"
    assert agent._extract_session_id(SimpleNamespace(result=json_payload)) == "s3"

    bad_payload = "not-json"
    assert agent._extract_run_id(SimpleNamespace(result=bad_payload)) is None
    assert agent._extract_session_id(SimpleNamespace(result=bad_payload)) is None


@pytest.mark.asyncio
async def test_stop_run_success_and_failure(monkeypatch):
    agent = RemoteAgent("ws://host")

    class SuccessOther:
        async def stop_run(self, run_id):
            return SimpleNamespace(result=True)

    class FailingOther:
        async def stop_run(self, run_id):
            raise RuntimeError("fail")

    agent._client = SimpleNamespace(other=SuccessOther())
    agent._current_run_id = "run-1"
    success = await agent.stop_run()
    assert success is True
    assert agent._current_run_id is None

    monkeypatch.setattr("omniagents.rpc.agents.remote.Debug.enabled", lambda: True)

    agent._client = SimpleNamespace(other=FailingOther())
    agent._current_run_id = "run-2"
    failure = await agent.stop_run()
    assert failure is False
    assert agent._current_run_id == "run-2"


@pytest.mark.asyncio
async def test_list_and_archive_sessions():
    agent = RemoteAgent("ws://host")

    class DummyOther:
        async def list_sessions(self):
            return SimpleNamespace(result=[{"id": "s1"}])

        async def archive_session(self, session_id):
            return SimpleNamespace(result=None)

    agent._client = SimpleNamespace(other=DummyOther())
    agent._ensure_client = AsyncMock()

    sessions = await agent.list_sessions()
    assert sessions == [{"id": "s1"}]

    result = await agent.archive_session("s1")
    assert result is None


@pytest.mark.asyncio
async def test_ensure_client_initializes_rpc(monkeypatch):
    agent = RemoteAgent("ws://host")

    created = []

    class FakeClient:
        def __init__(self, url, methods):
            self.url = url
            self.methods = methods
            self.entered = False
            self.other = SimpleNamespace()

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.entered = False

    def fake_from_url(url, methods):
        client = FakeClient(url, methods)
        created.append((url, methods))
        return client

    monkeypatch.setattr(
        "omniagents.rpc.agents.remote.JsonRpcClient.from_url", fake_from_url
    )

    await agent._ensure_client()
    assert isinstance(agent._client, FakeClient)
    assert agent._client.entered is True
    assert created[0][0] == "ws://host"
    assert created[0][1] is agent._methods

    await agent._ensure_client()
    assert len(created) == 1


def test_remote_agent_tool_methods():
    agent = RemoteAgent("ws://host")
    assert agent.get_all_tools() == []
    agent.reset_run_state()


@pytest.mark.asyncio
async def test_run_streamed_with_variables(monkeypatch):
    agent = RemoteAgent("ws://host", variables={"x": 1}, context={"y": 2})

    class DummyOther:
        async def start_run(self, **kwargs):
            start_run_params.update(kwargs)
            return SimpleNamespace(result={"session_id": "abc", "run_id": "rid"})

    start_run_params = {}
    agent._client = SimpleNamespace(other=DummyOther())
    agent._ensure_client = AsyncMock()

    async def produce():
        await asyncio.sleep(0)
        await agent._event_queue.put({"method": "run_end", "params": {"run_id": "rid"}})

    runner = agent.run_streamed(
        [{"role": "user", "content": "hi"}], run_config=SimpleNamespace()
    )
    gen = runner.stream_events()

    await asyncio.gather(
        asyncio.create_task(produce()), asyncio.create_task(consume(gen))
    )

    assert start_run_params["variables"] == {"x": 1}
    assert start_run_params["context"] == {"y": 2}
    assert agent._session_id == "abc"


async def consume(gen):
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_client_event_methods_queue():
    queue = asyncio.Queue()
    methods = _ClientEventMethods(queue)

    await methods.tool_called(tool="a")
    await methods.tool_result(tool="a", output="ok")
    await methods.message_output(content="hi")
    await methods.token(usage=1)
    await methods.run_end(run_id="1")
    await methods.run_status(status="running")
    await methods.run_started(run_id="1")
    await methods.client_request(action="ping")

    items = [await queue.get() for _ in range(8)]
    names = [item["method"] for item in items]
    assert names == [
        "tool_called",
        "tool_result",
        "message_output",
        "token",
        "run_end",
        "run_status",
        "run_started",
        "client_request",
    ]
