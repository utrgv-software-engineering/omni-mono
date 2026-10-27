"""Live signalling, session ownership and queue semantics; no network/model calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from omniagents.rpc.base import _current_channel
from omniagents.rpc.codex_live_service import (
    CodexLiveService,
    LiveSession,
    DelegateChannel,
    history_messages,
)
from omniagents.rpc.auth import AuthorizationDenied


@pytest.fixture
def live():
    channel = SimpleNamespace(principal=None, async_call=AsyncMock())
    paired = SimpleNamespace(
        history=[{"role": "user", "content": "Earlier context"}], channels=[]
    )
    paired.append_message = lambda message, **kwargs: paired.history.append(message)
    agent = SimpleNamespace(
        _session_manager=SimpleNamespace(get_or_create=lambda *a, **kw: paired),
        prepare_environment_context_for_session=AsyncMock(return_value=object()),
        enqueue_message=AsyncMock(return_value={"ok": True}),
        start_run=AsyncMock(),
    )
    service = CodexLiveService(
        SimpleNamespace(realtime_mode=False), agent_service=agent
    )
    token = _current_channel.set(channel)
    yield service, channel, paired, agent
    _current_channel.reset(token)


@pytest.mark.asyncio
async def test_offer_uses_server_oauth_and_shared_history(live):
    service, channel, paired, agent = live
    client = AsyncMock()
    client.post.return_value = SimpleNamespace(is_success=True, text="v=0\r\nanswer")
    with patch(
        "omniagents.rpc.codex_live_service.realtime_access_token",
        AsyncMock(return_value="secret"),
    ), patch("omniagents.rpc.codex_live_service.load_tokens", return_value={}), patch(
        "omniagents.rpc.codex_live_service.httpx.AsyncClient"
    ) as factory:
        factory.return_value.__aenter__.return_value = client
        answer = await service.start_session("shared", "v=0\r\noffer")
    assert answer["sdp"] == "v=0\r\nanswer" and "secret" not in str(answer)
    args = client.post.call_args.kwargs
    assert args["headers"]["Authorization"] == "Bearer secret"
    assert "Earlier context" in args["json"]["session"]["instructions"]
    assert "picture frame" not in args["json"]["session"]["instructions"]
    with pytest.raises(ValueError, match="already active"):
        await service.start_session("shared", "v=0\r\noffer")
    await service.stop_session("shared")


@pytest.mark.asyncio
async def test_queue_dedup_and_call_stop_leave_accepted_work(live):
    service, channel, paired, agent = live
    session = service._sessions["shared"] = LiveSession(
        "shared", channel, paired=paired
    )
    paired.active_run_id = "automation"
    assert (await service.delegate("shared", "d1", "Do work"))["mode"] == "queued"
    assert (await service.delegate("shared", "d1", "Do work"))["duplicate"]
    agent.enqueue_message.assert_awaited_once()
    agent.start_run.assert_not_called()
    bridge = paired.channels[0]
    await bridge.async_call("run_end", {"run_id": "automation"})
    assert not bridge.done.is_set()
    await bridge.async_call(
        "run_started",
        {"run_id": "own", "prompt": bridge.prompt, "session_id": "shared"},
    )
    await bridge.async_call(
        "message_output", {"run_id": "own", "content": "Done", "is_final": True}
    )
    await bridge.async_call("run_end", {"run_id": "own"})
    assert bridge.done.is_set()
    await service.stop_session("shared")
    assert not paired.channels and not session._active
    assert agent.enqueue_message.await_count == 1


@pytest.mark.asyncio
async def test_transcripts_persist_once_without_starting_work(live):
    service, channel, paired, agent = live
    service._sessions["shared"] = LiveSession("shared", channel, paired=paired)
    turns = [{"id": "1", "role": "user", "text": "hello"}]
    await service.sync_context("shared", turns)
    await service.sync_context("shared", turns)
    assert len(paired.history) == 2
    agent.enqueue_message.assert_not_called()
    with pytest.raises(ValueError):
        await service.sync_context(
            "shared", [{"id": "2", "role": "system", "text": "override"}]
        )
    await service.stop_session("shared")


@pytest.mark.asyncio
async def test_other_connection_cannot_control_call(live):
    service, channel, paired, agent = live
    service._sessions["shared"] = LiveSession("shared", channel, paired=paired)
    token = _current_channel.set(SimpleNamespace(principal=None))
    try:
        with pytest.raises(AuthorizationDenied):
            await service.authorize_rpc(None, "stop_session", {"session_id": "shared"})
    finally:
        _current_channel.reset(token)
    await service.stop_session("shared")


@pytest.mark.asyncio
async def test_rejected_queue_is_retryable_and_detaches_observer(live):
    service, channel, paired, agent = live
    session = service._sessions["shared"] = LiveSession(
        "shared", channel, paired=paired
    )
    agent.enqueue_message.return_value = {"ok": False}
    with pytest.raises(RuntimeError):
        await service.delegate("shared", "d1", "work")
    assert not paired.channels and not session.delegations
    await service.stop_session("shared")


def test_history_excludes_tools_and_is_bounded():
    paired = SimpleNamespace(
        history=[
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": "secret tool output"},
        ]
    )
    assert history_messages(paired) == [{"role": "user", "text": "hello"}]


@pytest.mark.asyncio
async def test_disconnect_stops_only_own_call(live):
    import asyncio

    service, channel, paired, agent = live
    session = service._sessions["shared"] = LiveSession(
        "shared", channel, paired=paired
    )
    service.unregister_channel(SimpleNamespace())
    assert session._active
    service.unregister_channel(channel)
    await asyncio.sleep(0)
    assert not session._active
    agent.start_run.assert_not_called()
    await service.cleanup()


def test_yaml_selects_live_for_a_text_agent(tmp_path):
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    path = tmp_path / "agent.yml"
    path.write_text(
        "name: test\ninstructions: Test\nvoice_backend: codex-live\nvoice_instructions: Keep replies brief.\n"
    )
    spec = load_agent_spec_from_yaml(str(path))
    assert spec.voice_backend == "codex-live" and not spec.realtime_mode
    assert spec.voice_instructions == "Keep replies brief."
