import asyncio
import base64
import logging
import os
import uuid
from typing import Any, AsyncIterator, Dict, Optional, cast

from omniagents.rpc import RpcMethodsBase
from omniagents.rpc.base import _current_channel
from omniagents.rpc.transport import (
    BoundedTransportQueue,
    QueueClosedError,
    QueuePolicy,
)
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.debug import Debug

logger = logging.getLogger(__name__)


DEBUG_VOICE = (
    os.getenv("OMNIAGENTS_SERVER_DEBUG", "0").lower() in {"1", "true"}
    or Debug.enabled()
)

# Agent event types that should be forwarded to the voice WS channel
# so the voice modal can display tool calls, results, and approval requests.
_FORWARDED_AGENT_EVENTS = frozenset(
    {"tool_called", "tool_result", "client_request", "client_request_resolved"}
)


class _VoiceBridgeChannel:
    """RPC channel that captures ``message_output`` text for TTS and
    forwards agent events (tool calls, results, approvals) to the voice
    WebSocket channel so the voice modal can display them.

    A single instance is created per voice session and reused across
    turns so it only appears once in ``session.channels``.
    """

    def __init__(
        self,
        voice_channel: Any = None,
        *,
        max_text_chunks: int = 128,
        max_text_bytes: int = 1024 * 1024,
    ) -> None:
        self._max_text_chunks = max_text_chunks
        self._max_text_bytes = max_text_bytes
        self._text_queue = self._new_text_queue()
        self._voice_channel = voice_channel

    def _new_text_queue(self) -> BoundedTransportQueue[str]:
        return BoundedTransportQueue(
            resource="voice_text",
            max_messages=self._max_text_chunks,
            max_bytes=self._max_text_bytes,
        )

    async def start_turn(self) -> None:
        previous_queue = self._text_queue
        self._text_queue = self._new_text_queue()
        await previous_queue.close()

    async def finish_turn(self) -> None:
        await self._text_queue.close()

    async def async_call(
        self, method: str, args: Optional[Dict[str, Any]] = None
    ) -> None:
        if method == "message_output":
            text = (args or {}).get("content")
            if text:
                await self._text_queue.put(
                    text,
                    size=len(text.encode("utf-8")),
                    policy=QueuePolicy.COALESCE_APPEND,
                    key="active_turn",
                    event_class="voice_text",
                    merge=lambda previous, current: previous + current,
                    merged_size=lambda value: len(value.encode("utf-8")),
                )
        elif method == "run_end":
            await self.finish_turn()
        elif method in _FORWARDED_AGENT_EVENTS and self._voice_channel:
            try:
                await self._voice_channel.async_call(
                    "realtime_event",
                    {
                        "type": "agent_event",
                        "event_type": method,
                        "data": args or {},
                    },
                )
            except Exception:
                pass

    async def text_chunks(self) -> AsyncIterator[str]:
        """Yield text chunks until the run ends."""
        text_queue = self._text_queue
        while True:
            try:
                yield await text_queue.get()
            except QueueClosedError:
                return


class _OmniSingleAgentVoiceWorkflow:
    """Voice workflow that routes LLM calls through :class:`AgentService`.

    Instead of building its own agent and calling ``Runner.run_streamed``
    directly, this workflow delegates to ``AgentService.start_run`` so that
    the voice session shares the same agent instance, conversation history,
    and SQLite persistence as the chat UI.
    """

    def __init__(
        self,
        *,
        agent_service: Any,
        session_id: str,
        voice_channel: Any = None,
    ):
        self._agent_service = agent_service
        self._session_id = session_id
        self._bridge = _VoiceBridgeChannel(voice_channel=voice_channel)

    async def on_start(self) -> AsyncIterator[str]:
        return
        yield

    async def run(self, transcription: str) -> AsyncIterator[str]:
        if DEBUG_VOICE:
            try:
                print("[voice] transcription", transcription)
            except Exception:
                pass

        await self._bridge.start_turn()
        # _VoiceBridgeChannel duck-types JsonRpcChannel's notify surface.
        token = _current_channel.set(cast(Any, self._bridge))
        try:
            result = await self._agent_service.start_run(
                transcription, self._session_id
            )
            if DEBUG_VOICE:
                try:
                    print("[voice] start_run returned", result)
                except Exception:
                    pass

            chunk_count = 0
            chunk_chars = 0
            async for chunk in self._bridge.text_chunks():
                chunk_count += 1
                try:
                    chunk_chars += len(chunk)
                except Exception:
                    pass
                yield chunk

            if DEBUG_VOICE:
                try:
                    print(
                        "[voice] agent run complete",
                        {"chunks": chunk_count, "chars": chunk_chars},
                    )
                except Exception:
                    pass
        finally:
            await self._bridge.finish_turn()
            _current_channel.reset(token)


class VoicePipelineSession:
    def __init__(
        self,
        *,
        spec: AgentSpec,
        settings: Dict[str, Any],
        session_id: str,
        agent_service: Any,
        voice_channel: Any = None,
    ):
        self.spec = spec
        self.settings = settings
        self.session_id = session_id
        self.run_id = str(uuid.uuid4())
        self._agent_service = agent_service
        self._voice_channel = voice_channel

        self._input: Optional[Any] = None
        self._result: Optional[Any] = None

    async def start(self) -> str:
        try:
            from agents.voice import StreamedAudioInput, VoicePipeline
            from agents.voice.pipeline_config import VoicePipelineConfig
        except ImportError as exc:
            raise ImportError(
                "Voice pipeline dependencies not installed. Install with: pip install 'openai-agents[voice]'"
            ) from exc

        # --- Voice / TTS / STT configuration ---

        voice = None
        try:
            rs = getattr(self.spec, "realtime_settings", None)
            voice = getattr(rs, "voice", None) if rs else None
        except Exception:
            voice = None
        if not voice:
            voice = "alloy"

        # Resolve voice model provider for STT/TTS
        voice_model_provider = self._build_voice_model_provider()

        config = VoicePipelineConfig(model_provider=voice_model_provider)
        try:
            config.tts_settings.voice = cast(Any, voice)
        except Exception:
            pass

        try:
            rs = getattr(self.spec, "realtime_settings", None)
            instructions = getattr(rs, "tts_instructions", None) if rs else None
            if isinstance(instructions, str):
                instructions = instructions.strip() or None
            if instructions:
                config.tts_settings.instructions = instructions
        except Exception:
            pass

        try:
            rs = getattr(self.spec, "realtime_settings", None)
            td = getattr(rs, "turn_detection", None) if rs else None
            if isinstance(td, dict) and td.get("type"):
                config.stt_settings.turn_detection = td
        except Exception:
            pass

        if DEBUG_VOICE:
            try:
                print(
                    "[voice] pipeline config",
                    {
                        "voice": voice,
                        "session_id": self.session_id,
                        "stt_turn_detection": getattr(
                            config.stt_settings, "turn_detection", None
                        ),
                    },
                )
            except Exception:
                pass

        # --- Workflow (routes through AgentService) ---

        workflow = _OmniSingleAgentVoiceWorkflow(
            agent_service=self._agent_service,
            session_id=self.session_id,
            voice_channel=self._voice_channel,
        )

        audio_input = StreamedAudioInput()
        # _OmniSingleAgentVoiceWorkflow duck-types VoiceWorkflowBase.
        pipeline = VoicePipeline(workflow=cast(Any, workflow), config=config)
        result = await pipeline.run(audio_input)

        self._input = audio_input
        self._result = result

        return self.run_id

    def _build_voice_model_provider(self) -> Any:
        """Build the STT/TTS model provider from spec and settings."""
        model_config: Optional[Dict[str, Any]] = None
        model_config_resolver = getattr(self.spec, "resolve_default_model_config", None)
        if callable(model_config_resolver):
            try:
                resolved = model_config_resolver()
                if isinstance(resolved, dict) and resolved:
                    model_config = resolved
            except Exception:
                pass

        provider_type = None
        if isinstance(model_config, dict):
            provider_type = model_config.get("provider")

        voice_api_key = None
        voice_base_url = None
        voice_headers = None

        if provider_type in ("openai", "openai-compatible", "azure"):
            voice_api_key = (model_config or {}).get("api_key")
            voice_base_url = (model_config or {}).get("base_url")
            voice_headers = (model_config or {}).get("headers")
        if not voice_api_key:
            voice_api_key = self.settings.get("openai_api_key") or self.settings.get(
                "api_key"
            )
        if not voice_api_key:
            # No explicit key: fall back to the ChatGPT sign-in, gated by the
            # same rule capabilities() reports, so the mic never promises what
            # this cannot authenticate.
            from omniagents.core.providers.codex import realtime_access_token_sync
            from omniagents.rpc.realtime_session import realtime_credential_status

            available, _ = realtime_credential_status(self.settings)
            if available:
                voice_api_key = realtime_access_token_sync()
        if not voice_base_url:
            voice_base_url = self.settings.get("openai_base_url")

        try:
            from omniagents.voice.openai_compatible import (
                build_openai_compatible_voice_model_provider,
            )

            return build_openai_compatible_voice_model_provider(
                api_key=voice_api_key,
                base_url=voice_base_url,
                headers=voice_headers,
            )
        except Exception:
            return None

    async def add_audio(self, audio_base64: str) -> None:
        if not self._input:
            raise RuntimeError("Session not started")

        from agents.voice.imports import np

        audio_bytes = base64.b64decode(audio_base64)
        samples = np.frombuffer(audio_bytes, dtype="<i2")
        if DEBUG_VOICE:
            try:
                print(
                    "[voice] audio_in",
                    {"bytes": len(audio_bytes), "samples": int(samples.size)},
                )
            except Exception:
                pass
        await self._input.add_audio(samples)

    async def end_input(self) -> None:
        if not self._input:
            return
        try:
            await self._input.add_audio(None)
        except Exception:
            pass

    async def stream(self):
        if not self._result:
            raise RuntimeError("Session not started")
        async for ev in self._result.stream():
            yield ev

    async def stop(self) -> None:
        await self.end_input()


class VoicePipelineService(RpcMethodsBase):
    def __init__(
        self,
        spec: AgentSpec,
        settings: Optional[Dict[str, Any]] = None,
        settings_resolver: Optional[Any] = None,
        agent_service: Optional[Any] = None,
    ):
        super().__init__()
        self.spec = spec
        self.settings = settings or {}
        self.settings_resolver = settings_resolver
        self._agent_service = agent_service

        self._sessions: Dict[str, VoicePipelineSession] = {}
        self._event_tasks: Dict[str, asyncio.Task] = {}
        # Owner user_id per active session (None = unowned/anonymous)
        self._session_owners: Dict[str, Optional[str]] = {}
        # Authorization mode; ``build_app`` installs the deployment's policy.
        from omniagents.rpc.auth import AuthorizationPolicy

        self.authorization_policy = AuthorizationPolicy(enforced=False)

    async def authorize_rpc(self, principal, method: str, params: dict) -> None:
        """Deny access to voice sessions owned by another user (see
        :meth:`omniagents.rpc.realtime_service.RealtimeService.authorize_rpc`)."""
        from omniagents.rpc.auth import AuthorizationDenied

        session_id = (params or {}).get("session_id")
        if not isinstance(session_id, str) or session_id not in self._sessions:
            return
        if self.authorization_policy.can_access(
            principal, self._session_owners.get(session_id)
        ):
            return
        raise AuthorizationDenied(
            "Not authorized to access this realtime session",
            resource="realtime_session",
            resource_id=session_id,
        )

    async def capabilities(self) -> Dict[str, Any]:
        """Probe voice availability without opening a session (and thus without creating a trace)."""
        try:
            from agents.voice import StreamedAudioInput, VoicePipeline  # noqa: F401
        except Exception as exc:
            return {
                "enabled": False,
                "kind": "pipeline",
                "agent_name": getattr(self.spec, "name", None),
                "reason": f"Voice pipeline dependencies unavailable: {exc}",
            }

        effective_settings = self.settings
        if callable(self.settings_resolver):
            try:
                resolved = self.settings_resolver()
                if isinstance(resolved, dict):
                    effective_settings = resolved
            except Exception as exc:
                return {
                    "enabled": False,
                    "kind": "pipeline",
                    "agent_name": getattr(self.spec, "name", None),
                    "reason": f"Voice settings unavailable: {exc}",
                }

        # Same gate the realtime backend uses: a ChatGPT sign-in is a valid
        # credential here too, so this must not insist on a plain API key.
        from omniagents.rpc.realtime_session import realtime_credential_status

        available, detail = realtime_credential_status(effective_settings or {})
        if not available:
            return {
                "enabled": False,
                "kind": "pipeline",
                "agent_name": getattr(self.spec, "name", None),
                "reason": detail,
            }

        return {
            "enabled": True,
            "kind": "pipeline",
            "agent_name": getattr(self.spec, "name", None),
            "detail": detail,
        }

    async def start_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id in self._sessions:
            raise RuntimeError(f"Session {session_id} already exists")

        if self._agent_service is None:
            raise RuntimeError(
                "VoicePipelineService requires an agent_service reference "
                "to route voice through the shared session"
            )

        # Capture voice WS channel eagerly — the ContextVar is set now
        # (during the WebSocket handler) but may not be available later
        # when the VoicePipeline's internal tasks call workflow.run().
        voice_channel = self.channel

        resolved_settings = None
        if callable(self.settings_resolver):
            try:
                resolved_settings = self.settings_resolver()
            except Exception:
                resolved_settings = None

        effective_settings = (
            resolved_settings if resolved_settings is not None else dict(self.settings)
        )

        session = VoicePipelineSession(
            spec=self.spec,
            settings=effective_settings,
            session_id=session_id,
            agent_service=self._agent_service,
            voice_channel=voice_channel,
        )

        run_id = await session.start()
        self._sessions[session_id] = session
        channel = self.channel
        principal = getattr(channel, "principal", None) if channel else None
        self._session_owners[session_id] = (
            principal.user_id if principal is not None else None
        )

        task = asyncio.create_task(self._stream_session_events(session_id))
        self._event_tasks[session_id] = task

        return {"session_id": session_id, "run_id": run_id}

    async def client_response(
        self,
        request_id: str,
        ok: bool,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Forward tool approval responses to the AgentService."""
        if self._agent_service and hasattr(self._agent_service, "client_response"):
            return await self._agent_service.client_response(request_id, ok, result)
        return False

    async def _stream_session_events(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return

        try:
            async for event in session.stream():
                if not self.channel:
                    continue
                mapped = await self._map_pipeline_event(session, event)
                if mapped is not None:
                    await self.channel.async_call("realtime_event", mapped)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if self.channel:
                try:
                    await self.channel.async_call(
                        "realtime_event",
                        {
                            "type": "realtime_error",
                            "run_id": session.run_id,
                            "error_message": str(exc),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                except Exception:
                    pass

    async def _map_pipeline_event(
        self, session: VoicePipelineSession, event: Any
    ) -> Optional[Dict[str, Any]]:
        event_type = getattr(event, "type", None)
        if event_type == "voice_stream_event_audio":
            data = getattr(event, "data", None)
            if data is None:
                return None
            try:
                audio_bytes = data.tobytes()
            except Exception:
                return None
            return {
                "type": "realtime_audio",
                "run_id": session.run_id,
                "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
                "item_id": session.run_id,
                "content_index": 0,
                "delta_ms": None,
            }

        if event_type == "voice_stream_event_lifecycle":
            lifecycle = getattr(event, "event", "")
            if lifecycle == "turn_started":
                return {
                    "type": "realtime_response_start",
                    "run_id": session.run_id,
                    "response_id": "",
                }
            if lifecycle == "turn_ended":
                return {
                    "type": "realtime_audio_end",
                    "run_id": session.run_id,
                    "item_id": session.run_id,
                    "content_index": 0,
                }
            if lifecycle == "session_ended":
                return {
                    "type": "realtime_audio_end",
                    "run_id": session.run_id,
                    "item_id": session.run_id,
                    "content_index": 0,
                }
            return None

        if event_type == "voice_stream_event_error":
            err = getattr(event, "error", None)
            return {
                "type": "realtime_error",
                "run_id": session.run_id,
                "error_message": str(err) if err else "Voice pipeline error",
                "error_type": getattr(err, "__class__", type("_", (), {})).__name__,
            }

        return None

    async def send_audio(
        self, session_id: str, audio_base64: str, commit: bool = False
    ) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        await session.add_audio(audio_base64)
        return True

    async def interrupt(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        task = self._event_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await session.end_input()
        return True

    async def stop_session(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        task = self._event_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            await session.stop()
        finally:
            self._sessions.pop(session_id, None)
            self._event_tasks.pop(session_id, None)
            self._session_owners.pop(session_id, None)

        return True
