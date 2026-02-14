import asyncio
import base64
import logging
import os
import uuid
from contextlib import AsyncExitStack
from typing import Any, AsyncIterator, Dict, Optional

from omniagents.rpc import RpcMethodsBase
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.debug import Debug


logger = logging.getLogger(__name__)


DEBUG_VOICE = (
    os.getenv("OMNIAGENTS_SERVER_DEBUG", "0").lower() in {"1", "true"}
    or Debug.enabled()
)


def _safe_model_provider_debug(provider: Any) -> Dict[str, Any]:
    info: Dict[str, Any] = {"provider_type": type(provider).__name__}
    try:
        client = getattr(provider, "openai_client", None)
        if client is not None:
            info["base_url"] = str(getattr(client, "base_url", ""))
    except Exception:
        pass
    return info


class _OmniSingleAgentVoiceWorkflow:
    def __init__(
        self,
        *,
        agent: Any,
        context: Any,
        max_turns: int,
        group_id: str,
        workflow_name: str,
        model_provider: Any,
    ):
        self._input_history: list[dict[str, Any]] = []
        self._current_agent = agent
        self._context = context
        self._max_turns = max_turns
        self._group_id = group_id
        self._workflow_name = workflow_name
        self._model_provider = model_provider

    async def on_start(self) -> AsyncIterator[str]:
        return
        yield

    async def run(self, transcription: str) -> AsyncIterator[str]:
        from agents import RunConfig, Runner
        from agents.voice.workflow import VoiceWorkflowHelper

        if DEBUG_VOICE:
            try:
                print("[voice] transcription", transcription)
            except Exception:
                pass

        self._input_history.append({"role": "user", "content": transcription})

        run_config = RunConfig(
            group_id=self._group_id,
            workflow_name=self._workflow_name,
            model_provider=self._model_provider,
        )

        if DEBUG_VOICE:
            try:
                print(
                    "[voice] llm_run start",
                    {
                        "group_id": self._group_id,
                        "workflow": self._workflow_name,
                        "model_provider": _safe_model_provider_debug(
                            self._model_provider
                        ),
                        "agent_model": getattr(self._current_agent, "model", None),
                    },
                )
            except Exception:
                pass

        result = Runner.run_streamed(
            self._current_agent,
            self._input_history,
            context=self._context,
            max_turns=self._max_turns,
            run_config=run_config,
        )

        yielded = False
        chunk_count = 0
        chunk_chars = 0
        async for chunk in VoiceWorkflowHelper.stream_text_from(result):
            yielded = True
            chunk_count += 1
            try:
                chunk_chars += len(chunk)
            except Exception:
                pass
            yield chunk

        if not yielded:
            final_output = getattr(result, "final_output", None)
            if isinstance(final_output, str) and final_output:
                if DEBUG_VOICE:
                    try:
                        print(
                            "[voice] llm_run used_final_output",
                            {"chars": len(final_output)},
                        )
                    except Exception:
                        pass
                yield final_output
        elif DEBUG_VOICE:
            try:
                print(
                    "[voice] llm_run streamed_text",
                    {"chunks": int(chunk_count), "chars": int(chunk_chars)},
                )
            except Exception:
                pass

        self._input_history = result.to_input_list()
        self._current_agent = result.last_agent


class VoicePipelineSession:
    def __init__(
        self,
        *,
        spec: AgentSpec,
        settings: Dict[str, Any],
        session_id: str,
    ):
        self.spec = spec
        self.settings = settings
        self.session_id = session_id
        self.run_id = str(uuid.uuid4())

        self._input = None
        self._result = None

        self.model_config: Optional[Dict[str, Any]] = None
        self.model_provider: Any = None

        self._mcp_shutdown: Optional[asyncio.Event] = None
        self._mcp_lifecycle_task: Optional[asyncio.Task] = None
        self._mcp_servers_active: list[Any] = []

    async def _ensure_mcp_servers(self) -> list[Any]:
        if self._mcp_servers_active:
            return self._mcp_servers_active

        if not getattr(self.spec, "mcp_servers", None):
            return []

        from omniagents.core.agents.mcp import build_mcp_servers

        servers_ready = asyncio.Event()
        active_servers_holder: dict[str, list[Any]] = {"servers": []}

        async def mcp_lifecycle_manager():
            settings = dict(self.settings)
            async with AsyncExitStack() as stack:
                active_servers: list[Any] = []
                try:
                    servers = build_mcp_servers(self.spec, settings)
                    for server in servers:
                        server_name = getattr(server, "name", "unknown")
                        try:
                            if hasattr(server, "__aenter__") and hasattr(
                                server, "__aexit__"
                            ):
                                active = await stack.enter_async_context(server)
                            else:
                                await server.connect()
                                stack.push_async_callback(server.cleanup)
                                active = server
                            active_servers.append(active)
                        except Exception as server_err:
                            logger.debug(
                                "MCP server '%s' failed to connect: %s",
                                server_name,
                                server_err,
                            )

                    self._mcp_servers_active = active_servers
                    active_servers_holder["servers"] = active_servers
                    servers_ready.set()

                    shutdown_event = self._mcp_shutdown
                    if shutdown_event:
                        await shutdown_event.wait()
                finally:
                    self._mcp_servers_active = []

        self._mcp_shutdown = asyncio.Event()
        self._mcp_lifecycle_task = asyncio.create_task(
            mcp_lifecycle_manager(), name=f"voice_pipeline_mcp_{self.session_id[:8]}"
        )
        await servers_ready.wait()
        return active_servers_holder["servers"]

    def _build_context(self) -> Any:
        context_dict: Any = None
        try:
            if (
                getattr(self.spec, "build_context", None)
                and getattr(self.spec, "variables", None) is not None
            ):
                context_dict = self.spec.build_context(self.spec.variables)
            elif getattr(self.spec, "context", None) is not None:
                context_dict = self.spec.context
        except Exception:
            context_dict = None
        return context_dict

    async def start(self) -> str:
        try:
            from agents.voice import StreamedAudioInput, VoicePipeline
            from agents.voice.pipeline_config import VoicePipelineConfig
        except ImportError as exc:
            raise ImportError(
                "Voice pipeline dependencies not installed. Install with: pip install 'openai-agents[voice]'"
            ) from exc

        mcp_servers = await self._ensure_mcp_servers()

        if callable(getattr(self.spec, "resolve_default_model_config", None)):
            try:
                resolved = self.spec.resolve_default_model_config()
                if isinstance(resolved, dict) and resolved:
                    self.model_config = resolved
            except Exception:
                self.model_config = None

        self.model_provider = self._build_model_provider(self.model_config)

        from omniagents.core.agents.builder import _default_build_agent

        builder_session = None
        if self.model_config is not None:
            builder_session = type(
                "_VoicePipelineBuilderSession",
                (),
                {"model_config": self.model_config, "reasoning_effort": None},
            )()

        agent = await _default_build_agent(
            settings=dict(self.settings),
            mcp_servers=list(mcp_servers),
            spec=self.spec,
            approval_callback=None,
            session=builder_session,
        )

        voice = None
        try:
            rs = getattr(self.spec, "realtime_settings", None)
            voice = getattr(rs, "voice", None) if rs else None
        except Exception:
            voice = None
        if not voice:
            voice = "alloy"

        provider_type = None
        if isinstance(self.model_config, dict):
            provider_type = self.model_config.get("provider")

        voice_api_key = None
        voice_base_url = None
        voice_headers = None

        if provider_type in ("openai", "openai-compatible", "azure"):
            voice_api_key = (self.model_config or {}).get("api_key")
            voice_base_url = (self.model_config or {}).get("base_url")
            voice_headers = (self.model_config or {}).get("headers")
        if not voice_api_key:
            voice_api_key = self.settings.get("openai_api_key") or self.settings.get(
                "api_key"
            )
        if not voice_base_url:
            voice_base_url = self.settings.get("openai_base_url")

        try:
            from omniagents.voice.openai_compatible import (
                build_openai_compatible_voice_model_provider,
            )

            voice_model_provider = build_openai_compatible_voice_model_provider(
                api_key=voice_api_key,
                base_url=voice_base_url,
                headers=voice_headers,
            )
        except Exception:
            voice_model_provider = None

        config = VoicePipelineConfig(model_provider=voice_model_provider)
        try:
            config.tts_settings.voice = voice
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
                        "provider": provider_type,
                        "base_url": voice_base_url,
                        "stt_turn_detection": getattr(
                            config.stt_settings, "turn_detection", None
                        ),
                    },
                )
            except Exception:
                pass

        workflow = _OmniSingleAgentVoiceWorkflow(
            agent=agent,
            context=self._build_context(),
            max_turns=int(getattr(self.spec, "max_turns", 20) or 20),
            group_id=f"voice_{self.session_id}",
            workflow_name="omniagents_voice_pipeline",
            model_provider=self.model_provider,
        )

        audio_input = StreamedAudioInput()
        pipeline = VoicePipeline(workflow=workflow, config=config)
        result = await pipeline.run(audio_input)

        self._input = audio_input
        self._result = result

        return self.run_id

    def _build_model_provider(self, model_config: Optional[Dict[str, Any]]):
        provider_type = None
        if isinstance(model_config, dict):
            provider_type = model_config.get("provider")
        if (
            provider_type is None
            and isinstance(model_config, dict)
            and model_config.get("base_url")
        ):
            provider_type = "openai-compatible"

        if provider_type == "litellm":
            try:
                from agents.extensions.models.litellm_provider import LitellmProvider

                return LitellmProvider()
            except Exception:
                return None

        if provider_type in ("openai", "openai-compatible", "azure"):
            try:
                from openai import AsyncOpenAI
                from agents.models.openai_provider import OpenAIProvider

                base_url = (
                    (model_config or {}).get("base_url") if model_config else None
                )
                api_key = (model_config or {}).get("api_key") if model_config else None

                def _expand(val: Any) -> Any:
                    import os

                    if (
                        val
                        and isinstance(val, str)
                        and val.startswith("${")
                        and val.endswith("}")
                    ):
                        return os.getenv(val[2:-1])
                    return val

                base_url = _expand(base_url)
                api_key = _expand(api_key)

                if isinstance(base_url, str):
                    base_url = base_url.strip() or None
                if isinstance(api_key, str):
                    api_key = api_key.strip() or None

                if not api_key:
                    api_key = self.settings.get("openai_api_key") or self.settings.get(
                        "api_key"
                    )
                if not api_key:
                    import os

                    api_key = os.getenv("OPENAI_API_KEY")

                if not base_url:
                    base_url = self.settings.get("openai_base_url")
                if not base_url:
                    base_url = "https://api.openai.com/v1"

                client = AsyncOpenAI(api_key=api_key, base_url=base_url)
                return OpenAIProvider(openai_client=client)
            except Exception:
                return None

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

        shutdown_event = self._mcp_shutdown
        if shutdown_event:
            shutdown_event.set()
        lifecycle_task = self._mcp_lifecycle_task
        if lifecycle_task:
            try:
                await lifecycle_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._mcp_shutdown = None
        self._mcp_lifecycle_task = None
        self._mcp_servers_active = []


class VoicePipelineService(RpcMethodsBase):
    def __init__(
        self,
        spec: AgentSpec,
        settings: Optional[Dict[str, Any]] = None,
        settings_resolver: Optional[Any] = None,
    ):
        super().__init__()
        self.spec = spec
        self.settings = settings or {}
        self.settings_resolver = settings_resolver

        self._sessions: Dict[str, VoicePipelineSession] = {}
        self._event_tasks: Dict[str, asyncio.Task] = {}

    async def start_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id in self._sessions:
            raise RuntimeError(f"Session {session_id} already exists")

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
        )

        run_id = await session.start()
        self._sessions[session_id] = session

        task = asyncio.create_task(self._stream_session_events(session_id))
        self._event_tasks[session_id] = task

        return {"session_id": session_id, "run_id": run_id}

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

        return True
