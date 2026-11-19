"""GPT Live voice (``gpt-live-1``) over the OpenAI-compatible Live sessions API.

``voice_backend: live`` serves the same ``/ws/realtime`` contract as realtime
voice, so the shared voice UI streams PCM audio here unchanged. The server
holds the Live session itself — ``wss://<OPENAI_BASE_URL>/live/sessions`` with
the API key — so the key never reaches the browser and any OpenAI-compatible
gateway in front of the API works.

The Live model handles the conversation and delegates work it cannot do by
talking. A delegation becomes a queued run of the agent (its tools, approvals
and history; see :class:`CodexLiveService`), and the run's result is handed
back to the Live model to speak. Live's delegation event carries no task text,
so the task is what the user said since the previous delegation.
"""

import asyncio
import base64
import json
import logging
import os
import sys
import uuid
from array import array
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from omniagents.rpc.codex_live_service import (
    CodexLiveService,
    LiveSession,
    history_messages,
)

logger = logging.getLogger(__name__)

DEFAULT_LIVE_MODEL = "gpt-live-1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
VOICE_SOURCE = "voice:live"
START_TIMEOUT_SECONDS = 20
# Live limits commentary to 500 tokens; leave room below that.
COMMENTARY_CHAR_LIMIT = 1800
INSTRUCTIONS_CHAR_LIMIT = 48000
MAX_HISTORY_ITEMS = 128
# Output audio quieter than this peak (of 32767) is silence.
SILENCE_PEAK = 200
# Silence this long on Live's audio timeline ends a spoken reply.
REPLY_END_SILENCE_MS = 700


PCM16_24K_BYTES_PER_MS = 48


def pcm16_stats(audio_base64: str) -> tuple:
    """``(peak, duration_ms)`` of base64 little-endian 24 kHz PCM16 audio."""
    try:
        raw = base64.b64decode(audio_base64, validate=False)
    except (ValueError, TypeError):
        return 0, 0.0
    samples = array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max((abs(s) for s in samples), default=0)
    return peak, len(raw) / PCM16_24K_BYTES_PER_MS


LIVE_INSTRUCTIONS = """You are the voice of the agent whose instructions follow.
Speak naturally and keep each reply brief.
You cannot use tools yourself. When the user asks for something that needs one of the agent's tools, its files, or information you do not have, delegate it: the backend agent will do the work and send you the result.
Briefly acknowledge delegated work, then speak its result when it arrives. Never claim a result before it arrives.
Handle greetings and ordinary conversation yourself, without delegating.
Stay in the role, and follow the rules, that the agent instructions describe."""


def live_sessions_url(base_url: Optional[str]) -> str:
    """``https://host/v1`` -> ``wss://host/v1/live/sessions``.

    A base without ``/v1`` gets it, so ``https://api.openai.com`` and
    ``https://api.openai.com/v1`` both resolve to OpenAI's Live endpoint.
    """
    parsed = urlsplit((base_url or DEFAULT_OPENAI_BASE_URL).strip())
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((scheme, parsed.netloc, path + "/live/sessions", "", ""))


def _setting(settings: Dict[str, Any], *keys_and_env: str) -> Optional[str]:
    """First non-empty settings key, then environment variable (last argument)."""
    *keys, env = keys_and_env
    for key in keys:
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = os.getenv(env)
    return value.strip() if value and value.strip() else None


def _tool_lines(spec: Any) -> list:
    lines = []
    for tool in getattr(spec, "available_tools", None) or []:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if not name:
            continue
        description = " ".join(str(getattr(tool, "description", "") or "").split())
        lines.append(f"- {name}: {description[:300]}" if description else f"- {name}")
    return lines


def build_live_instructions(spec: Any) -> str:
    """Live frontend instructions: voice rules, the agent's own instructions, its tools."""
    parts = [LIVE_INSTRUCTIONS]
    try:
        agent_instructions = spec.get_agent_instructions()
    except Exception:
        agent_instructions = None
    if isinstance(agent_instructions, str) and agent_instructions.strip():
        parts.append("# Agent instructions\n" + agent_instructions.strip())
    tools = _tool_lines(spec)
    if tools:
        parts.append("# Tools the backend agent can use for you\n" + "\n".join(tools))
    voice_instructions = getattr(spec, "voice_instructions", None)
    if isinstance(voice_instructions, str) and voice_instructions.strip():
        parts.append("# Voice instructions\n" + voice_instructions.strip())
    return "\n\n".join(parts)[:INSTRUCTIONS_CHAR_LIMIT]


def initial_items(messages: list) -> list:
    """Chat history as Live's startup ``input`` (text-only messages)."""
    items = []
    for message in messages[-MAX_HISTORY_ITEMS:]:
        part_type = "output_text" if message["role"] == "assistant" else "input_text"
        items.append(
            {
                "type": "message",
                "role": message["role"],
                "content": [{"type": part_type, "text": message["text"]}],
            }
        )
    return items


class GptLiveCall(LiveSession):
    """One server-held Live session paired with a chat session."""

    def __init__(self, session_id, channel, ws, *, paired, owner, principal, model):
        super().__init__(session_id, channel, paired=paired)
        self.ws = ws
        self.owner = owner
        self.principal = principal
        self.model = model
        self.send_lock = asyncio.Lock()
        self.turn: Optional[Dict[str, Any]] = None  # {"id", "role", "text"}
        self.serial = 0
        self.request_text: list = []  # user words since the last delegation
        self.speaking = False
        self.silent_ms = 0.0  # silence since the reply's last voiced audio

    async def send_upstream(self, event: Dict[str, Any]) -> None:
        async with self.send_lock:
            await self.ws.send(json.dumps(event))

    async def emit(self, event):
        # The agent's progress and results go to the Live model, which speaks
        # them; the voice UI sees the delegation as tool activity.
        kind = event.get("type")
        delegation_id = event.get("delegation_id")
        text = str(event.get("text") or "")
        if kind in ("live_delegation_result", "live_delegation_output"):
            await self._safe_upstream(
                {
                    "type": "session.commentary.append",
                    "delegation_id": delegation_id,
                    "content": text[:COMMENTARY_CHAR_LIMIT],
                }
            )
            return
        if kind == "live_delegation_progress":
            await self._safe_upstream(
                {
                    "type": "session.thinking.append",
                    "delegation_id": delegation_id,
                    "content": text[:COMMENTARY_CHAR_LIMIT],
                }
            )
            return
        await super().emit(event)

    async def _safe_upstream(self, event):
        if not self._active:
            return
        try:
            await self.send_upstream(event)
        except Exception:
            logger.warning(
                "Live session %s: could not send %s", self.session_id, event.get("type")
            )

    # -- transcripts ---------------------------------------------------

    async def end_speaking(self) -> None:
        if self.speaking:
            self.speaking = False
            await super().emit({"type": "realtime_audio_end"})
            await super().emit({"type": "realtime_turn_ended"})

    async def add_transcript(self, role: str, delta: str) -> None:
        opens_turn = self.turn is None or self.turn["role"] != role
        if opens_turn and not delta.strip():
            return  # Live sends blank fragments during silence; they are not a turn
        if role == "user":
            await self.end_speaking()
        if self.turn is None or self.turn["role"] != role:
            await self.close_turn()
            self.serial += 1
            self.turn = {"id": str(self.serial), "role": role, "text": ""}
        self.turn["text"] = (self.turn["text"] + delta)[-12000:]
        if role == "user":
            self.request_text.append(delta)
        await super().emit(
            {
                "type": "realtime_transcript",
                "item_id": f"live:{self._run_id}:{self.turn['id']}",
                "role": role,
                "transcript": self.turn["text"].strip(),
            }
        )

    async def close_turn(self) -> None:
        """Save the open turn into the paired chat (recorded, not queued)."""
        turn, self.turn = self.turn, None
        if turn is None:
            return
        if turn["role"] == "assistant":
            await self.end_speaking()
        text = turn["text"].strip()
        if not text or self.paired is None or turn["id"] in self.persisted:
            return
        role = turn["role"]
        message = {
            "role": role,
            "type": "message",
            "content": (
                text
                if role == "user"
                else [{"type": "output_text", "text": text, "annotations": []}]
            ),
            "omniagents": {
                "source": VOICE_SOURCE,
                "live_call": self._run_id,
                "voice_id": turn["id"],
            },
        }
        try:
            self.paired.append_message(
                message, projection={"version": 1, "kind": "voice_transcript"}
            )
            self.persisted.add(turn["id"])
        except Exception:
            logger.exception("Live session %s: voice turn not saved", self.session_id)

    def take_request(self) -> str:
        text, self.request_text = "".join(self.request_text).strip(), []
        return text

    async def stop(self):
        try:
            await self.close_turn()
        except Exception:
            pass
        if self._active:
            try:
                await asyncio.wait_for(self.send_upstream({"type": "session.close"}), 5)
            except Exception:
                pass
        await super().stop()
        try:
            await self.ws.close()
        except Exception:
            pass


class LiveVoiceService(CodexLiveService):
    """``voice_backend: live`` — GPT Live with an API key, held server-side."""

    def _credentials(self) -> tuple:
        settings = self._effective_settings()
        key = _setting(settings, "openai_api_key", "api_key", "OPENAI_API_KEY")
        base = _setting(settings, "openai_base_url", "OPENAI_BASE_URL")
        model = _setting(settings, "openai_live_model", "OPENAI_LIVE_MODEL")
        return key, base, model or DEFAULT_LIVE_MODEL

    async def capabilities(self):
        key, base, model = self._credentials()
        return {
            "enabled": bool(key),
            "kind": "live",
            "transport": "websocket",
            "model": model,
            "agent_name": getattr(self.spec, "name", None),
            "detail": (
                "settings api key" if key else "no OpenAI API key (OPENAI_API_KEY)"
            ),
        }

    async def start_session(self, session_id: Optional[str] = None):
        async with self._start_lock:
            return await self._start_live(session_id or str(uuid.uuid4()))

    async def _start_live(self, session_id: str):
        if session_id in self._sessions:
            raise RuntimeError(f"Session {session_id} already exists")
        key, base, model = self._credentials()
        if not key:
            raise ValueError("Live voice needs an OpenAI API key (OPENAI_API_KEY)")

        channel = self.channel
        owner = self._current_owner_user_id()
        principal = getattr(channel, "principal", None)
        paired = None
        if self._agent_service is not None:
            paired = self._agent_service._session_manager.get_or_create(
                session_id, user_id=owner
            )
        history = history_messages(paired) if paired is not None else []

        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus

        url = live_sessions_url(base)
        try:
            ws = await connect(
                url,
                additional_headers={"Authorization": f"Bearer {key}"},
                max_size=None,
                open_timeout=START_TIMEOUT_SECONDS,
            )
        except InvalidStatus as exc:
            status = exc.response.status_code
            raise RuntimeError(
                f"Live voice connection refused (HTTP {status}) by {urlsplit(url).netloc}"
            ) from None

        session_config: Dict[str, Any] = {
            "model": model,
            "instructions": build_live_instructions(self.spec),
            "audio": {"format": {"type": "audio/pcm", "rate": 24000}},
        }
        if self._agent_service is not None:
            session_config["delegation"] = {"type": "client"}
        if history:
            session_config["input"] = initial_items(history)
        try:
            await ws.send(
                json.dumps({"type": "session.start", "session": session_config})
            )
            started = await self._await_started(ws)
        except BaseException:
            await ws.close()
            raise

        call = GptLiveCall(
            session_id,
            channel,
            ws,
            paired=paired,
            owner=owner,
            principal=principal,
            model=started.get("session", {}).get("model") or model,
        )
        call.history_snapshot = history
        self._sessions[session_id] = call
        self._session_owners[session_id] = owner
        self._event_tasks[session_id] = asyncio.create_task(self._read_live(call))
        return {"session_id": session_id, "run_id": call._run_id, "model": call.model}

    @staticmethod
    async def _await_started(ws) -> Dict[str, Any]:
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), START_TIMEOUT_SECONDS))
            if event.get("type") == "session.started":
                return event
            if event.get("type") == "error":
                error = event.get("error") or {}
                raise RuntimeError(
                    "Live voice refused the session: "
                    + str(error.get("message") or error.get("code") or "unknown error")
                )
            if event.get("type") == "session.closed":
                raise RuntimeError("Live voice closed the session before it started")

    async def _read_live(self, call: GptLiveCall) -> None:
        try:
            async for raw in call.ws:
                try:
                    event = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not await self._handle_live_event(call, event):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # connection dropped
            if call._active:
                logger.warning("Live session %s ended: %s", call.session_id, exc)
                await call.emit(
                    {"type": "realtime_error", "error_message": "Voice connection lost"}
                )
                return
        if call._active:
            await call.close_turn()
            await call.emit(
                {"type": "realtime_error", "error_message": "Live voice session ended"}
            )

    async def _handle_live_event(
        self, call: GptLiveCall, event: Dict[str, Any]
    ) -> bool:
        """Translate one Live event. Returns False once the session is over."""
        kind = event.get("type")
        if kind == "session.output_audio.delta":
            await self._relay_audio(call, event)
        elif kind == "session.input_transcript.delta":
            await call.add_transcript("user", str(event.get("delta") or ""))
        elif kind == "session.output_transcript.delta":
            await call.add_transcript("assistant", str(event.get("delta") or ""))
        elif kind == "session.delegation.created":
            delegation = event.get("delegation") or {}
            if delegation.get("target") == "client" and delegation.get("id"):
                await self._delegate_live(call, str(delegation["id"]))
        elif kind == "error":
            error = event.get("error") or {}
            message = str(
                error.get("message") or error.get("code") or "Live voice error"
            )
            logger.warning("Live session %s error: %s", call.session_id, message)
            await call.emit({"type": "realtime_error", "error_message": message})
        elif kind == "session.closed":
            await call.close_turn()
            if call._active:
                await call.emit(
                    {
                        "type": "realtime_error",
                        "error_message": "Live voice session ended: "
                        + str(event.get("reason") or "closed"),
                    }
                )
            return False
        return True

    @staticmethod
    async def _relay_audio(call: GptLiveCall, event: Dict[str, Any]) -> None:
        """Forward the agent's speech, not the silence Live streams between turns.

        Live sends output audio continuously, silent while the user talks. The
        voice UI mutes the microphone while any audio plays, so forwarding
        silence would mute the user for the whole session. Silent frames are
        kept only inside a reply (pauses between words) and dropped otherwise.
        """
        audio = str(event.get("delta") or "")
        peak, duration_ms = pcm16_stats(audio)
        if peak >= SILENCE_PEAK:
            if not call.speaking:
                call.speaking = True
                await call.emit({"type": "realtime_response_start"})
            call.silent_ms = 0.0
        elif not call.speaking:
            return
        else:
            call.silent_ms += duration_ms
            if call.silent_ms >= REPLY_END_SILENCE_MS:
                await call.end_speaking()
                return
        await call.emit({"type": "realtime_audio", "audio_base64": audio})

    async def _delegate_live(self, call: GptLiveCall, delegation_id: str) -> None:
        if call.turn is not None and call.turn["role"] == "user":
            await call.close_turn()
        request = call.take_request() or "(The user's request was not transcribed.)"
        await call.emit(
            {
                "type": "realtime_tool_start",
                "tool_call_id": delegation_id,
                "tool_name": "agent",
                "arguments": request[:2000],
            }
        )
        if self._agent_service is None:
            await call.emit(
                {
                    "type": "live_delegation_result",
                    "delegation_id": delegation_id,
                    "text": "No backend agent is available for this request.",
                }
            )
            return
        try:
            async with call.admission:
                await self._delegate(
                    call,
                    call.session_id,
                    delegation_id,
                    request,
                    owner=call.owner,
                    principal=call.principal,
                    source=VOICE_SOURCE,
                )
            self._report_when_done(call, delegation_id)
        except Exception as exc:
            logger.warning(
                "Live session %s: delegation refused: %s", call.session_id, exc
            )
            await call.emit(
                {
                    "type": "live_delegation_result",
                    "delegation_id": delegation_id,
                    "text": f"The request was not accepted: {exc}",
                }
            )

    @staticmethod
    def _report_when_done(call: GptLiveCall, delegation_id: str) -> None:
        """Show the delegated run as finished tool activity once it ends."""
        bridge = next(
            (b for b in call.bridges if b.delegation_id == delegation_id), None
        )
        if bridge is None:
            return

        async def report():
            await bridge.done.wait()
            output = (bridge.final_text or "".join(bridge.text)).strip()
            await call.emit(
                {
                    "type": "realtime_tool_end",
                    "tool_call_id": delegation_id,
                    "tool_name": "agent",
                    "output": output[:12000],
                }
            )

        task = asyncio.create_task(report())
        call.tasks.add(task)
        task.add_done_callback(call.tasks.discard)

    async def send_audio(
        self, session_id: str, audio_base64: str, commit: bool = False
    ):
        call = self._sessions.get(session_id)
        if call is None or not call._active:
            raise ValueError(f"Session {session_id} not found")
        await call.send_upstream(
            {"type": "session.input_audio.append", "audio": audio_base64}
        )
        return True

    async def send_text(self, session_id: str, text: str):
        raise ValueError("Live voice takes speech only; type in the chat instead")

    async def interrupt(self, session_id: str):
        # Live detects barge-in from the audio itself; there is nothing to cancel.
        if session_id not in self._sessions:
            raise ValueError(f"Session {session_id} not found")
        return True
