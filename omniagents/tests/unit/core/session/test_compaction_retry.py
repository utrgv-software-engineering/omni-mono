import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from omniagents.core.session.compaction import (
    wrap_with_compaction,
    NativeCompactionTimeoutError,
)
from omniagents.core.session.compaction_retry import (
    CompactionRetriesExhausted,
    RetryingCompactionClient,
    TransientCompactionError,
)
from omniagents.core.session.manager import Session


@pytest.mark.asyncio
@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("failure", ["server", "disconnect", "exhausted", "permanent"])
async def test_request_retries_and_history(codex, failure, monkeypatch):
    monkeypatch.setattr("omniagents.core.session.compaction_retry._delay", lambda *_: 0)
    requests = []
    item = {"type": "compaction", "id": "cmp_test", "encrypted_content": "encrypted"}

    def respond(request):
        requests.append(json.loads(request.content))
        if failure == "permanent":
            return httpx.Response(404, json={"detail": "Not Found"})
        if failure == "exhausted" or len(requests) == 1:
            if failure == "disconnect":
                raise httpx.ReadError("disconnected", request=request)
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        if codex:
            events = [
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_test",
                        "status": "completed",
                        "output": [item],
                    },
                },
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join("data: " + json.dumps(e) + "\n\n" for e in events),
            )
        return httpx.Response(200, json={"id": "cmp_test", "output": [item]})

    async with AsyncOpenAI(
        api_key="test",
        base_url=(
            "https://chatgpt.com/backend-api/codex"
            if codex
            else "https://api.openai.com/v1"
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        session = Session("retry", "wire")
        original = [
            {"role": "user", "content": "Keep this"},
            {"role": "assistant", "content": "Working context"},
        ]
        await session.add_items(original)
        native = wrap_with_compaction(
            session, {"enabled": True, "client": client}, parent_model="gpt-6-astra"
        )
        if failure in {"exhausted", "permanent"}:
            with pytest.raises(Exception) as exc:
                await native.run_compaction({"force": True})
            assert len(requests) == (3 if failure == "exhausted" else 1)
            assert await session.get_items() == original
            assert await Session("retry", "wire", session.id).get_items() == original
            if failure == "exhausted":
                from omniagents.core.agents.service import _is_retryable_error

                assert isinstance(exc.value, CompactionRetriesExhausted)
                assert not _is_retryable_error(exc.value)
        else:
            await native.run_compaction({"force": True})
            assert len(requests) == 2
            assert requests[0] == requests[1]
            assert (await session.get_items())[-1] == item
        assert not session._sdk_compaction_in_progress
        assert client.max_retries == 2  # normal generation retains SDK defaults


@pytest.mark.asyncio
async def test_dropped_stream_retries_without_partial_output(monkeypatch):
    from omniagents.core.providers.codex_compaction import CodexCompactionClient

    monkeypatch.setattr("omniagents.core.session.compaction_retry._delay", lambda *_: 0)
    item = {"type": "compaction", "encrypted_content": "valid"}

    class Stream:
        def __init__(self, complete):
            self.complete = complete
            self.closed = False

        async def __aiter__(self):
            yield SimpleNamespace(
                type="response.output_item.done",
                item=SimpleNamespace(model_dump=lambda **_: item),
            )
            if self.complete:
                yield SimpleNamespace(type="response.completed")

        async def close(self):
            self.closed = True

    streams = [Stream(False), Stream(True)]
    create = AsyncMock(side_effect=streams)
    raw = SimpleNamespace(responses=SimpleNamespace(create=create))
    # Fake client supports copies, like the real SDK client.
    raw.with_options = lambda **_: raw
    result = await RetryingCompactionClient(CodexCompactionClient(raw)).compact(
        model="gpt-6-astra", input=[]
    )
    assert result.output == [item]
    assert create.await_count == 2
    assert all(s.closed for s in streams)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_total_timeout_and_cancellation_include_backoff(cancel, monkeypatch):
    entered = asyncio.Event()

    def delay(*_):
        entered.set()
        return 10

    monkeypatch.setattr("omniagents.core.session.compaction_retry._delay", delay)
    endpoint = AsyncMock(side_effect=TransientCompactionError("interrupted"))
    session = Session("retry", "timeout")
    original = [{"role": "user", "content": "Keep this"}]
    await session.add_items(original)
    native = wrap_with_compaction(
        session,
        {
            "enabled": True,
            "client": SimpleNamespace(responses=SimpleNamespace(compact=endpoint)),
            "timeout_seconds": 5 if cancel else 0.05,
        },
        parent_model="gpt-6-astra",
    )
    task = asyncio.create_task(native.run_compaction({"force": True}))
    await asyncio.wait_for(entered.wait(), 1)
    if cancel:
        task.cancel()
    with pytest.raises(
        asyncio.CancelledError if cancel else NativeCompactionTimeoutError
    ):
        await task
    assert endpoint.await_count == 1
    assert await session.get_items() == original
    assert not session._sdk_compaction_in_progress


@pytest.mark.asyncio
async def test_sdk_sse_error_recovers_and_honors_retry_after(monkeypatch):
    from openai import APIError, RateLimitError
    from omniagents.core.session.compaction_retry import _delay

    request = httpx.Request("POST", "https://example.test/responses")
    response = httpx.Response(429, headers={"retry-after": "7"}, request=request)
    assert _delay(RateLimitError("busy", response=response, body=None), 0) == 7
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("omniagents.core.session.compaction_retry.asyncio.sleep", sleep)
    endpoint = AsyncMock(
        side_effect=[
            APIError("busy", request, body={"code": "server_error"}),
            SimpleNamespace(output=["ok"]),
        ]
    )
    result = await RetryingCompactionClient(
        SimpleNamespace(responses=SimpleNamespace(compact=endpoint))
    ).compact(input=[])
    assert result.output == ["ok"]
    assert delays == [1]
