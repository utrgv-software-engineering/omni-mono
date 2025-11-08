from __future__ import annotations

import asyncio
import os
import random
from typing import Any, AsyncIterator

import httpx
from openai import APIError, APIStatusError, Omit, omit
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseStreamEvent,
)

from agents.tracing import SpanError, response_span


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, APIStatusError):
        try:
            code = getattr(exc, "status_code", None)
            return code in {408, 429, 500, 502, 503, 504}
        except Exception:
            return False
    if isinstance(exc, APIError):
        return True
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.RequestError):
        return True
    return False


def _jitter_delay(base: float, attempt: int, cap: float) -> float:
    if attempt <= 1:
        return 0.0
    delay = base * (2 ** (attempt - 2))
    delay = min(delay, cap)
    jitter = delay * (0.2 * (random.random() * 2 - 1))
    return max(0.0, delay + jitter)


def _install_patch() -> None:
    from agents.models.openai_responses import OpenAIResponsesModel

    orig_stream_response = OpenAIResponsesModel.stream_response

    async def patched_stream_response(
        self: Any,
        system_instructions: str | None,
        input: str | list[Any],
        model_settings: Any,
        tools: list[Any],
        output_schema: Any | None,
        handoffs: list[Any],
        tracing: Any,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: Any | None = None,
    ) -> AsyncIterator[ResponseStreamEvent]:
        max_attempts = _env_int("AGENTS_STREAM_RETRY_MAX", 3)
        base_delay = _env_float("AGENTS_STREAM_RETRY_BASE", 0.5)
        max_delay = _env_float("AGENTS_STREAM_RETRY_MAX_DELAY", 8.0)

        response_id: str | None = None
        last_seq: int | None = None
        final_response: Response | None = None

        async def _iterate_events(stream: Any) -> AsyncIterator[ResponseStreamEvent]:
            nonlocal response_id, last_seq, final_response
            async for ev in stream:
                try:
                    if response_id is None and isinstance(ev, ResponseCreatedEvent):
                        rid = getattr(getattr(ev, "response", None), "id", None)
                        if isinstance(rid, str) and rid:
                            response_id = rid
                except Exception:
                    pass
                try:
                    seq = getattr(ev, "sequence_number", None)
                    if isinstance(seq, int):
                        last_seq = seq
                except Exception:
                    pass
                if isinstance(ev, ResponseCompletedEvent):
                    final_response = ev.response
                yield ev

        attempt = 1
        with response_span(disabled=tracing.is_disabled()) as span_response:
            while True:
                try:
                    if attempt == 1:
                        stream = await self._fetch_response(
                            system_instructions,
                            input,
                            model_settings,
                            tools,
                            output_schema,
                            handoffs,
                            previous_response_id=previous_response_id,
                            conversation_id=conversation_id,
                            stream=True,
                            prompt=prompt,
                        )
                        async for chunk in _iterate_events(stream):
                            yield chunk
                    else:
                        if not response_id:
                            raise RuntimeError("Cannot resume stream without response_id")
                        starting_after: int | Omit = last_seq if isinstance(last_seq, int) else omit
                        async with self._client.responses.stream(
                            response_id=response_id, starting_after=starting_after
                        ) as rstream:
                            async for chunk in _iterate_events(rstream):
                                yield chunk

                    if final_response and tracing.include_data():
                        span_response.span_data.response = final_response
                        span_response.span_data.input = input
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if response_id and _is_retryable_exception(e) and attempt < max_attempts:
                        delay = _jitter_delay(base_delay, attempt, max_delay)
                        if delay > 0:
                            try:
                                await asyncio.sleep(delay)
                            except asyncio.CancelledError:
                                raise
                        attempt += 1
                        continue
                    span_response.set_error(
                        SpanError(
                            message="Error streaming response",
                            data={
                                "error": str(e) if tracing.include_data() else e.__class__.__name__,
                            },
                        )
                    )
                    raise

    OpenAIResponsesModel.stream_response = patched_stream_response  # type: ignore[method-assign]


_install_patch()

