"""GPT Live voice backend against a local fake Live server; no network/model calls."""

import asyncio
import base64
import json
from array import array
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from websockets.asyncio.server import serve

from omniagents.rpc.base import _current_channel
from omniagents.rpc.live_voice_service import (
    LiveVoiceService,
    build_live_instructions,
    live_sessions_url,
    pcm16_stats,
)


def pcm(level: int, ms: int = 100) -> str:
    return base64.b64encode(array("h", [level] * (24 * ms)).tobytes()).decode()


VOICED, SILENT = pcm(3000), pcm(0)


class FakeLive:
    """Records what the client sends; replies as the test scripts it."""

    def __init__(self, start_reply=None, refuse_status=None):
        self.received = []
        self.headers = None
        self.path = None
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.start_reply = start_reply or {
            "type": "session.started",
            "session": {"model": "gpt-live-1"},
        }
        self.refuse_status = refuse_status

    def process_request(self, connection, request):
        self.path, self.headers = request.path, request.headers
        if self.refuse_status:
            return connection.respond(self.refuse_status, "refused\n")
        return None

    async def handler(self, ws):
        async def outgoing():
            while True:
                await ws.send(json.dumps(await self.outbox.get()))

        sender = asyncio.create_task(outgoing())
        try:
            async for raw in ws:
                message = json.loads(raw)
                self.received.append(message)
                if message["type"] == "session.start":
                    await ws.send(json.dumps(self.start_reply))
                if message["type"] == "session.close":
                    await ws.send(
                        json.dumps(
                            {"type": "session.closed", "reason": "close_requested"}
                        )
                    )
        except Exception:
            pass
        finally:
            sender.cancel()

    def sent(self, kind):
        return [m for m in self.received if m["type"] == kind]


async def eventually(predicate, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def voice_events(channel):
    return [
        call.args[1]
        for call in channel.async_call.call_args_list
        if call.args and call.args[0] == "realtime_event"
    ]


def kinds(channel):
    return [e["type"] for e in voice_events(channel)]


@pytest_asyncio.fixture
async def live(request):
    fake = FakeLive(**getattr(request, "param", {}))
    server = await serve(
        fake.handler, "127.0.0.1", 0, process_request=fake.process_request
    )
    port = server.sockets[0].getsockname()[1]
    channel = SimpleNamespace(principal=None, async_call=AsyncMock())
    paired = SimpleNamespace(
        history=[{"role": "assistant", "type": "message", "content": "Earlier reply"}],
        channels=[],
    )
    paired.append_message = lambda message, **kwargs: paired.history.append(message)
    agent = SimpleNamespace(
        _session_manager=SimpleNamespace(get_or_create=lambda *a, **kw: paired),
        enqueue_message=AsyncMock(return_value={"ok": True}),
    )
    spec = SimpleNamespace(
        realtime_mode=False,
        name="Tutor",
        get_agent_instructions=lambda: "You are Rosa, a Spanish market vendor.",
        available_tools=[
            SimpleNamespace(name="read_scenario", description="Read today's scenario.")
        ],
        voice_instructions="Speak slowly.",
    )
    service = LiveVoiceService(
        spec,
        {"openai_api_key": "sk-test", "openai_base_url": f"http://127.0.0.1:{port}/v1"},
        agent_service=agent,
    )
    token = _current_channel.set(channel)
    yield SimpleNamespace(
        service=service, fake=fake, channel=channel, paired=paired, agent=agent
    )
    _current_channel.reset(token)
    await service.cleanup()
    server.close()
    await server.wait_closed()


def test_live_sessions_url_keeps_the_base_path():
    assert (
        live_sessions_url("https://rgvaiclass.com/v1")
        == "wss://rgvaiclass.com/v1/live/sessions"
    )
    assert (
        live_sessions_url("https://api.openai.com")
        == "wss://api.openai.com/v1/live/sessions"
    )
    assert (
        live_sessions_url("https://res.openai.azure.com/openai/v1/")
        == "wss://res.openai.azure.com/openai/v1/live/sessions"
    )
    assert live_sessions_url(None) == "wss://api.openai.com/v1/live/sessions"


def test_pcm16_stats_measures_level_and_length():
    assert pcm16_stats(VOICED) == (3000, 100.0)
    assert pcm16_stats(SILENT) == (0, 100.0)
    assert pcm16_stats("") == (0, 0.0)


@pytest.mark.asyncio
async def test_instructions_carry_the_agent_its_tools_and_voice_rules(live):
    text = build_live_instructions(live.service.spec)
    assert "You are Rosa, a Spanish market vendor." in text
    assert "- read_scenario: Read today's scenario." in text
    assert "Speak slowly." in text
    assert "delegate" in text


@pytest.mark.asyncio
async def test_capabilities_need_an_api_key(live, monkeypatch):
    caps = await live.service.capabilities()
    assert (
        caps["enabled"] and caps["kind"] == "live" and caps["transport"] == "websocket"
    )
    assert caps["model"] == "gpt-live-1" and "sk-test" not in str(caps)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    live.service.settings = {}
    assert not (await live.service.capabilities())["enabled"]


@pytest.mark.asyncio
async def test_start_session_opens_live_with_key_persona_and_history(live):
    result = await live.service.start_session("chat-1")
    assert result["session_id"] == "chat-1" and result["model"] == "gpt-live-1"
    assert live.fake.path == "/v1/live/sessions"
    assert live.fake.headers["Authorization"] == "Bearer sk-test"
    start = live.fake.sent("session.start")[0]["session"]
    assert start["model"] == "gpt-live-1"
    assert start["delegation"] == {"type": "client"}
    assert "You are Rosa" in start["instructions"]
    assert start["input"][0]["content"][0]["text"] == "Earlier reply"
    with pytest.raises(RuntimeError, match="already exists"):
        await live.service.start_session("chat-1")


@pytest.mark.parametrize(
    "live",
    [{"start_reply": {"type": "error", "error": {"message": "model not available"}}}],
    indirect=True,
)
@pytest.mark.asyncio
async def test_start_session_reports_a_refused_session(live):
    with pytest.raises(RuntimeError, match="model not available"):
        await live.service.start_session("chat-1")
    assert "chat-1" not in live.service._sessions


@pytest.mark.parametrize(
    "live", [{"refuse_status": HTTPStatus.UNAUTHORIZED}], indirect=True
)
@pytest.mark.asyncio
async def test_start_session_reports_a_refused_handshake(live):
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await live.service.start_session("chat-1")


@pytest.mark.asyncio
async def test_audio_flows_both_ways_without_forwarding_silence(live):
    await live.service.start_session("chat-1")
    await live.service.send_audio("chat-1", VOICED)
    await eventually(lambda: live.fake.sent("session.input_audio.append"))
    assert live.fake.sent("session.input_audio.append")[0]["audio"] == VOICED

    # Silence before a reply must not reach the UI: it mutes the mic while audio plays.
    for audio in [SILENT, SILENT, VOICED, SILENT, VOICED] + [SILENT] * 8:
        await live.fake.outbox.put(
            {"type": "session.output_audio.delta", "delta": audio}
        )
    await eventually(lambda: "realtime_turn_ended" in kinds(live.channel))
    seen = kinds(live.channel)
    assert seen[0] == "realtime_response_start"
    audio = [
        e["audio_base64"]
        for e in voice_events(live.channel)
        if e["type"] == "realtime_audio"
    ]
    # voiced, the pause inside the reply, voiced, then 600 ms of trailing silence
    assert audio[0] == VOICED and audio[2] == VOICED and len(audio) == 3 + 6
    assert seen[-2:] == ["realtime_audio_end", "realtime_turn_ended"]


@pytest.mark.asyncio
async def test_transcripts_are_shown_and_saved_as_voice_turns(live):
    await live.service.start_session("chat-1")
    for delta in ["Hola,", " quiero", " un taco."]:
        await live.fake.outbox.put(
            {"type": "session.input_transcript.delta", "delta": delta}
        )
    await live.fake.outbox.put(
        {"type": "session.input_transcript.delta", "delta": "  "}
    )
    await live.fake.outbox.put(
        {"type": "session.output_transcript.delta", "delta": "¡Claro!"}
    )
    await eventually(lambda: len(live.paired.history) == 2)
    saved = live.paired.history[1]
    assert saved["role"] == "user" and saved["content"] == "Hola, quiero un taco."
    assert saved["omniagents"]["source"] == "voice:live"
    shown = [
        e for e in voice_events(live.channel) if e["type"] == "realtime_transcript"
    ]
    assert shown[-1]["role"] == "assistant" and shown[-1]["transcript"] == "¡Claro!"
    await live.service.stop_session("chat-1")
    assert live.paired.history[-1]["content"][0]["text"] == "¡Claro!"


@pytest.mark.asyncio
async def test_delegation_runs_the_agent_and_speaks_its_result(live):
    await live.service.start_session("chat-1")
    for delta in ["Read", " the scenario", " please."]:
        await live.fake.outbox.put(
            {"type": "session.input_transcript.delta", "delta": delta}
        )
    await live.fake.outbox.put(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "item_1", "type": "delegation", "target": "client"},
        }
    )
    await eventually(lambda: live.agent.enqueue_message.await_count == 1)
    queued = live.agent.enqueue_message.await_args.kwargs
    assert "Read the scenario please." in queued["content"]
    assert queued["source"] == "voice:live" and queued["session_id"] == "chat-1"

    bridge = live.paired.channels[0]
    await bridge.async_call(
        "run_started", {"run_id": "r1", "prompt": bridge.prompt, "session_id": "chat-1"}
    )
    await bridge.async_call(
        "message_output",
        {"run_id": "r1", "content": "Rosa sells fruit at a market.", "is_final": True},
    )
    await bridge.async_call("run_end", {"run_id": "r1"})

    await eventually(lambda: live.fake.sent("session.commentary.append"))
    spoken = live.fake.sent("session.commentary.append")[-1]
    assert (
        spoken["delegation_id"] == "item_1"
        and spoken["content"] == "Rosa sells fruit at a market."
    )
    assert live.fake.sent(
        "session.thinking.append"
    )  # "the backend is now working" stays silent
    await eventually(lambda: "realtime_tool_end" in kinds(live.channel))
    start = next(
        e for e in voice_events(live.channel) if e["type"] == "realtime_tool_start"
    )
    end = next(
        e for e in voice_events(live.channel) if e["type"] == "realtime_tool_end"
    )
    assert start["tool_call_id"] == end["tool_call_id"] == "item_1"
    assert end["output"] == "Rosa sells fruit at a market."


@pytest.mark.asyncio
async def test_refused_delegation_is_spoken_not_dropped(live):
    live.agent.enqueue_message.side_effect = RuntimeError("queue full")
    await live.service.start_session("chat-1")
    await live.fake.outbox.put(
        {"type": "session.input_transcript.delta", "delta": "Do it"}
    )
    await live.fake.outbox.put(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "item_2", "target": "client"},
        }
    )
    await eventually(lambda: live.fake.sent("session.commentary.append"))
    assert "not accepted" in live.fake.sent("session.commentary.append")[0]["content"]


@pytest.mark.asyncio
async def test_stop_closes_the_live_session(live):
    await live.service.start_session("chat-1")
    assert await live.service.stop_session("chat-1")
    assert live.fake.sent("session.close")
    assert "chat-1" not in live.service._sessions
    with pytest.raises(ValueError):
        await live.service.send_audio("chat-1", VOICED)


@pytest.mark.asyncio
async def test_typed_text_is_refused_with_a_clear_message(live):
    await live.service.start_session("chat-1")
    with pytest.raises(ValueError, match="speech only"):
        await live.service.send_text("chat-1", "hola")


@pytest.mark.asyncio
async def test_upstream_close_tells_the_ui(live):
    await live.service.start_session("chat-1")
    await live.fake.outbox.put({"type": "session.closed", "reason": "expired"})
    await eventually(lambda: "realtime_error" in kinds(live.channel))
    error = next(e for e in voice_events(live.channel) if e["type"] == "realtime_error")
    assert "expired" in error["error_message"]


def test_from_agent_selects_the_live_backend():
    from agents import Agent

    from omniagents import Runner

    runner = Runner.from_agent(
        Agent(name="Tutor", instructions="Hi"), voice_backend="live"
    )
    assert runner.spec.voice_backend == "live"


def test_yaml_selects_gpt_live(tmp_path):
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    path = tmp_path / "agent.yml"
    path.write_text("name: test\ninstructions: Test\nvoice_backend: live\n")
    spec = load_agent_spec_from_yaml(str(path))
    assert spec.voice_backend == "live" and not spec.realtime_mode
