import asyncio
import os
from typing import Any, Optional

from omniagents.core.debug import Debug

DEBUG_VOICE = (
    os.getenv("OMNIAGENTS_SERVER_DEBUG", "0").lower() in {"1", "true"}
    or Debug.enabled()
)


def _mask_secret(value: Any) -> str:
    if not value:
        return ""
    token = str(value)
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def _expand_env(value: Any) -> Any:
    if (
        value
        and isinstance(value, str)
        and value.startswith("${")
        and value.endswith("}")
    ):
        return os.getenv(value[2:-1])
    return value


def resolve_transcription_ws_url(base_url: str | None) -> str:
    base = (base_url or "").strip() or "https://api.openai.com/v1"

    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]

    base = base.rstrip("/")

    if "/realtime" in base:
        if "?" in base:
            return base
        return base + "?intent=transcription"

    return base + "/realtime?intent=transcription"


def build_openai_compatible_voice_model_provider(
    *,
    api_key: Optional[str],
    base_url: Optional[str],
    stt_ws_url: Optional[str] = None,
    headers: Optional[dict[str, Any]] = None,
):
    from openai import AsyncOpenAI
    from agents.voice.model import VoiceModelProvider
    from agents.voice.models.openai_model_provider import (
        DEFAULT_STT_MODEL,
        DEFAULT_TTS_MODEL,
    )
    from agents.voice.models.openai_stt import (
        ErrorSentinel,
        OpenAISTTModel,
        OpenAISTTTranscriptionSession,
    )
    from agents.voice.models.openai_tts import OpenAITTSModel

    api_key = _expand_env(api_key)
    base_url = _expand_env(base_url)

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    resolved_ws_url = stt_ws_url or resolve_transcription_ws_url(base_url)

    if DEBUG_VOICE:
        try:
            print(
                "[voice] stt_ws_url=",
                resolved_ws_url,
                " base_url=",
                base_url,
                " api_key=",
                _mask_secret(api_key),
            )
        except Exception:
            pass

    class OmniOpenAISTTTranscriptionSession(OpenAISTTTranscriptionSession):
        def __init__(
            self,
            *args,
            ws_url: str,
            ws_headers: dict[str, str],
            **kwargs,
        ):
            super().__init__(*args, **kwargs)
            self._omni_ws_url = ws_url
            self._omni_ws_headers = ws_headers

        async def _process_websocket_connection(self) -> None:
            from agents.voice.imports import websockets

            try:
                if DEBUG_VOICE:
                    try:
                        print(
                            "[voice] connecting transcription ws",
                            self._omni_ws_url,
                            {
                                k: (
                                    _mask_secret(v)
                                    if k.lower() == "authorization"
                                    else v
                                )
                                for k, v in self._omni_ws_headers.items()
                            },
                        )
                    except Exception:
                        pass

                async with websockets.connect(
                    self._omni_ws_url,
                    additional_headers=self._omni_ws_headers,
                ) as ws:
                    await self._setup_connection(ws)
                    self._process_events_task = asyncio.create_task(
                        self._handle_events()
                    )
                    self._stream_audio_task = asyncio.create_task(
                        self._stream_audio(self._input_queue)
                    )
                    self.connected = True

                    if DEBUG_VOICE:
                        try:
                            print("[voice] transcription ws connected")
                        except Exception:
                            pass

                    if self._listener_task:
                        await self._listener_task
                    else:
                        raise RuntimeError("Listener task not initialized")
            except Exception as e:
                if DEBUG_VOICE:
                    try:
                        print(
                            "[voice] transcription ws failed", type(e).__name__, str(e)
                        )
                    except Exception:
                        pass
                await self._output_queue.put(ErrorSentinel(e))
                raise

    class OmniOpenAISTTModel(OpenAISTTModel):
        def __init__(
            self,
            model: str,
            *,
            ws_url: str,
            ws_headers: dict[str, str],
        ):
            super().__init__(model, client)
            self._omni_ws_url = ws_url
            self._omni_ws_headers = ws_headers

        async def create_session(
            self,
            input,
            settings,
            trace_include_sensitive_data,
            trace_include_sensitive_audio_data,
        ):
            return OmniOpenAISTTTranscriptionSession(
                input,
                client,
                self.model,
                settings,
                trace_include_sensitive_data,
                trace_include_sensitive_audio_data,
                ws_url=self._omni_ws_url,
                ws_headers=self._omni_ws_headers,
            )

    class Provider(VoiceModelProvider):
        def get_stt_model(self, model_name: str | None):
            ws_headers: dict[str, str] = {"OpenAI-Log-Session": "1"}
            if api_key:
                ws_headers["Authorization"] = f"Bearer {api_key}"
            if headers and isinstance(headers, dict):
                for key, value in headers.items():
                    if value is None:
                        continue
                    ws_headers[str(key)] = str(value)

            return OmniOpenAISTTModel(
                model_name or DEFAULT_STT_MODEL,
                ws_url=resolved_ws_url,
                ws_headers=ws_headers,
            )

        def get_tts_model(self, model_name: str | None):
            return OpenAITTSModel(model_name or DEFAULT_TTS_MODEL, client)

    return Provider()
