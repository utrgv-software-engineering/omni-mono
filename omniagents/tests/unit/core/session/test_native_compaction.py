"""Native SDK compaction contracts: real wrapper/client, no remote provider calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from omniagents.core.session.compaction import (
    native_compaction_for_session,
    wrap_with_compaction,
)
from omniagents.core.session.manager import Session


def wrapper(session, endpoint, **config):
    result = wrap_with_compaction(
        session,
        {
            "enabled": True,
            "model": "gpt-6-astra",
            "threshold_tokens": 100,
            "client": SimpleNamespace(responses=SimpleNamespace(compact=endpoint)),
            **config,
        },
    )
    assert result is not session
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["timeout", "cancel", "empty"])
async def test_native_failure_preserves_context_and_releases_guard(exit_kind):
    session = Session("native", "failure")
    original = [{"role": "user", "content": "original"}]
    await session.add_items(original)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def endpoint(**kwargs):
        entered.set()
        if exit_kind == "empty":
            return SimpleNamespace(output=[])
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    native = wrapper(
        session, endpoint, timeout_seconds=0.03 if exit_kind == "timeout" else 5
    )
    task = asyncio.create_task(
        native.run_compaction({"force": True, "compaction_mode": "input"})
    )
    await asyncio.wait_for(entered.wait(), 1)
    if exit_kind == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match="timed out|empty context"):
            await task
    if exit_kind != "empty":
        assert cancelled.is_set()
    assert await session.get_items() == original
    assert await Session("native", "failure", session.id).get_items() == original
    assert session.history == original
    assert not session._sdk_compaction_in_progress


@pytest.mark.asyncio
async def test_native_trigger_includes_user_context_and_repairs_stale_cache():
    session = Session("native", "pressure")
    endpoint = AsyncMock(
        return_value=SimpleNamespace(output=[{"role": "user", "content": "short"}])
    )
    native = wrapper(session, endpoint)
    await native.add_items(
        [
            {
                "type": "function_call",
                "call_id": "pending",
                "name": "read_file",
                "arguments": "{}",
            }
        ]
    )
    await native.run_compaction({"force": True})
    endpoint.assert_not_awaited()
    # Bridge cancellation repair bypasses native.add_items.
    session.append_message(
        {"type": "function_call_output", "call_id": "pending", "output": "cancelled"}
    )
    session.append_message({"role": "user", "content": "x" * 2000})
    await native.run_compaction({"compaction_mode": "input"})
    endpoint.assert_awaited_once()
    assert endpoint.call_args.kwargs["input"][-1]["content"] == "x" * 2000
    assert await session.get_items() == [{"role": "user", "content": "short"}]


@pytest.mark.asyncio
async def test_native_azure_transport_and_durable_replacement():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "cmp-1",
                "object": "response.compaction",
                "created_at": 0,
                "output": [
                    {
                        "type": "compaction",
                        "id": "cmp-item",
                        "encrypted_content": "opaque",
                    }
                ],
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 10,
                    "total_tokens": 510,
                    "input_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    session = Session("native", "azure")
    session.append_message({"role": "user", "content": "history"})
    async with AsyncOpenAI(
        api_key="dummy",
        base_url="https://test.openai.azure.com/openai/v1/",
        default_headers={"api-key": "test-key"},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        native = wrap_with_compaction(
            session,
            {"enabled": True},
            model_provider=SimpleNamespace(_get_client=lambda: client),
            parent_model="gpt-6-astra",
            max_input_tokens=272000,
        )
        await native.run_compaction({"force": True, "compaction_mode": "input"})
    assert len(requests) == 1
    assert (
        str(requests[0].url)
        == "https://test.openai.azure.com/openai/v1/responses/compact"
    )
    assert requests[0].headers["api-key"] == "test-key"
    import json

    assert json.loads(requests[0].content) == {
        "model": "gpt-6-astra",
        "input": [{"role": "user", "content": "history"}],
    }
    assert (await Session("native", "azure", session.id).get_items())[0][
        "encrypted_content"
    ] == "opaque"
    assert session.history == [{"role": "user", "content": "history"}]


def test_native_cache_tracks_model_transport_window_and_opt_out(monkeypatch):
    import omniagents.core.models as models

    config = {
        "model": "gpt-6-astra",
        "provider": "openai-compatible",
        "base_url": "https://first.openai.azure.com/openai/v1/",
        "max_input_tokens": 272000,
    }
    service = SimpleNamespace(
        spec=SimpleNamespace(session_compaction_config={"enabled": True})
    )
    session = Session("native", "switch")
    monkeypatch.setattr(
        models, "resolve_service_model_runtime", lambda *a: ("model", config)
    )
    monkeypatch.setattr(
        models,
        "provider_from_runtime",
        lambda *a: (SimpleNamespace(_client=object()), {}),
    )
    first = native_compaction_for_session(service, session)
    assert native_compaction_for_session(service, session) is first
    for key, value in [
        ("model", "gpt-5.6-sol"),
        ("base_url", "https://second.openai.azure.com/openai/v1/"),
        ("max_input_tokens", 128000),
    ]:
        config[key] = value
        next_wrapper = native_compaction_for_session(service, session)
        assert next_wrapper is not first
        first = next_wrapper
    config["use_responses"] = False
    assert native_compaction_for_session(service, session) is None
    assert session._sdk_compaction_session is None


@pytest.mark.asyncio
@pytest.mark.parametrize("store", [True, False])
async def test_sdk_post_response_compaction_respects_response_storage(store):
    session = Session("native", "post-response")
    original = [{"role": "user", "content": "x" * 2000}]
    await session.add_items(original)
    endpoint = AsyncMock(
        return_value=SimpleNamespace(output=[{"role": "user", "content": "short"}])
    )
    native = wrapper(session, endpoint)
    # These are the arguments used by SDK session persistence after a response.
    await native.run_compaction({"response_id": "resp-new", "store": store})
    endpoint.assert_awaited_once()
    kwargs = endpoint.call_args.kwargs
    if store:
        assert kwargs["previous_response_id"] == "resp-new"
        assert "input" not in kwargs
    else:
        assert kwargs["input"] == original
        assert "previous_response_id" not in kwargs
    assert await session.get_items() == [{"role": "user", "content": "short"}]
    assert session.history == original


def test_native_timeout_matches_codex_and_reaches_http_client():
    client = AsyncOpenAI(api_key="test-key", timeout=45)
    native = wrap_with_compaction(
        Session("native", "timeout-budget"),
        {"enabled": True, "client": client},
        parent_model="gpt-6-astra",
    )
    assert native._omni_timeout_seconds == 1200
    assert native.client.timeout == 1200
    assert client.timeout == 45
