"""Exercise the real SDK wire format and session replacement without network."""

import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from omniagents.core.session.compaction import wrap_with_compaction
from omniagents.core.session.manager import Session


def sse(*events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


ITEM = {"type": "compaction", "id": "cmp_test", "encrypted_content": "encrypted"}
DONE = {"type": "response.output_item.done", "output_index": 0, "item": ITEM}
COMPLETED = {
    "type": "response.completed",
    "response": {"id": "resp_test", "status": "completed", "output": [ITEM]},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("codex", [True, False])
async def test_compaction_uses_provider_protocol_and_persists(codex):
    requests = []

    def respond(request):
        requests.append(request)
        if codex:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=sse(DONE, COMPLETED),
            )
        return httpx.Response(200, json={"id": "cmp_response", "output": [ITEM]})

    base = (
        "https://chatgpt.com/backend-api/codex"
        if codex
        else "https://api.openai.com/v1"
    )
    async with AsyncOpenAI(
        api_key="test",
        base_url=base,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        session = Session("codex-protocol", "wire")
        original = [
            {"role": "user", "content": "Keep this instruction"},
            {"role": "assistant", "content": "Long working context"},
        ]
        await session.add_items(original)
        native = wrap_with_compaction(
            session,
            {"enabled": True, "client": client},
            parent_model="gpt-6-astra",
        )
        await native.run_compaction({"force": True, "compaction_mode": "input"})
        assert len(requests) == 1
        body = json.loads(requests[0].content)
        if codex:
            assert str(requests[0].url) == base + "/responses"
            assert body["input"] == [*original, {"type": "compaction_trigger"}]
            assert body["store"] is False
            assert body["stream"] is True
            expected = [original[0], ITEM]
        else:
            assert str(requests[0].url) == base + "/responses/compact"
            expected = [ITEM]
        assert await session.get_items() == expected
        assert (
            await Session("codex-protocol", "wire", session.id).get_items() == expected
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [DONE],  # truncated stream
        [COMPLETED],  # no completed compaction item
        [DONE, DONE, COMPLETED],  # ambiguous output
        [{"type": "response.failed", "response": {"error": {"message": "failed"}}}],
        [{"type": "response.incomplete", "response": {"status": "incomplete"}}],
        [dict(DONE, item=dict(ITEM, encrypted_content="")), COMPLETED],
    ],
)
async def test_invalid_codex_stream_preserves_context(events, monkeypatch):
    monkeypatch.setattr("omniagents.core.session.compaction_retry._delay", lambda *_: 0)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse(*events),
        )

    async with AsyncOpenAI(
        api_key="test",
        base_url="https://chatgpt.com/backend-api/codex",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        session = Session("codex-protocol", "failure")
        original = [{"role": "user", "content": "Do not lose this"}]
        await session.add_items(original)
        native = wrap_with_compaction(
            session,
            {"enabled": True, "client": client},
            parent_model="gpt-6-astra",
        )
        with pytest.raises(RuntimeError, match="Codex compaction|Compaction failed"):
            await native.run_compaction({"force": True})
        assert len(requests) == (3 if events == [DONE] else 1)
        assert await session.get_items() == original
        assert not session._sdk_compaction_in_progress


@pytest.mark.asyncio
async def test_cancellation_closes_stream():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from omniagents.core.providers.codex_compaction import CodexCompactionClient

    entered = asyncio.Event()

    class Stream:
        close = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            entered.set()
            await asyncio.Event().wait()

    stream = Stream()
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=stream))
    )
    task = asyncio.create_task(
        CodexCompactionClient(client).compact(model="gpt-6-astra", input=[])
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stream.close.assert_awaited_once()
