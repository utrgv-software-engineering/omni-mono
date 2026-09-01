import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.rpc.transport import TransportOverloadedError
from omniagents.rpc.voice_pipeline_service import (
    _OmniSingleAgentVoiceWorkflow,
    _VoiceBridgeChannel,
)


@pytest.mark.asyncio
async def test_voice_bridge_coalesces_ordered_text_and_tracks_utf8_bytes():
    bridge = _VoiceBridgeChannel(max_text_chunks=2, max_text_bytes=8)

    await bridge.async_call("message_output", {"content": "hé"})
    await bridge.async_call("message_output", {"content": "llo"})

    assert bridge._text_queue.qsize == 1
    assert bridge._text_queue.byte_size == len("héllo".encode("utf-8"))

    await bridge.async_call("run_end")
    assert [chunk async for chunk in bridge.text_chunks()] == ["héllo"]


@pytest.mark.asyncio
async def test_voice_bridge_run_end_closes_full_queue_out_of_band():
    bridge = _VoiceBridgeChannel(max_text_chunks=1, max_text_bytes=8)

    await bridge.async_call("message_output", {"content": "complete"})
    await asyncio.wait_for(bridge.async_call("run_end"), timeout=0.1)

    assert [chunk async for chunk in bridge.text_chunks()] == ["complete"]


@pytest.mark.asyncio
async def test_voice_bridge_reset_discards_previous_turn_and_wakes_consumer():
    bridge = _VoiceBridgeChannel(max_text_chunks=2, max_text_bytes=16)
    consumer = asyncio.create_task(anext(bridge.text_chunks()))
    await asyncio.sleep(0)

    await bridge.start_turn()

    with pytest.raises(StopAsyncIteration):
        await consumer
    await bridge.async_call("message_output", {"content": "new turn"})
    await bridge.async_call("run_end")
    assert [chunk async for chunk in bridge.text_chunks()] == ["new turn"]


@pytest.mark.asyncio
async def test_voice_bridge_rejects_oversized_text_without_truncation():
    bridge = _VoiceBridgeChannel(max_text_chunks=2, max_text_bytes=4)

    with pytest.raises(TransportOverloadedError) as exc_info:
        await bridge.async_call("message_output", {"content": "hello"})

    assert exc_info.value.resource == "voice_text"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_workflow_falls_back_to_default_environment_when_unbound():
    """A voice-first session has a targetless thread binding, so `inherit`
    would run every turn with no environment (and no workers). Name the
    server default explicitly instead — what the GUI does on its first run."""
    agent_service = SimpleNamespace(
        prepare_environment_context_for_session=AsyncMock(return_value=None)
    )
    workflow = _OmniSingleAgentVoiceWorkflow(
        agent_service=agent_service,
        session_id="voice-first",
        default_environment_id="env-default",
    )

    assert await workflow._selection() == {
        "mode": "explicit",
        "environment_id": "env-default",
    }
    agent_service.prepare_environment_context_for_session.assert_awaited_with(
        "voice-first"
    )


@pytest.mark.asyncio
async def test_workflow_inherits_once_the_session_is_bound():
    """With a binding — including one the first fallback turn created — the
    run follows it, so a mid-session environment switch in the GUI wins."""
    agent_service = SimpleNamespace(
        prepare_environment_context_for_session=AsyncMock(
            return_value=SimpleNamespace(environment_lease=object())
        )
    )
    workflow = _OmniSingleAgentVoiceWorkflow(
        agent_service=agent_service,
        session_id="bound",
        default_environment_id="env-default",
    )

    assert await workflow._selection() == {"mode": "inherit"}


@pytest.mark.asyncio
async def test_workflow_inherits_without_a_server_default():
    """No configured default → nothing to fall back to; never probe."""
    agent_service = SimpleNamespace(
        prepare_environment_context_for_session=AsyncMock(return_value=None)
    )
    workflow = _OmniSingleAgentVoiceWorkflow(
        agent_service=agent_service,
        session_id="no-default",
    )

    assert await workflow._selection() == {"mode": "inherit"}
    agent_service.prepare_environment_context_for_session.assert_not_awaited()
