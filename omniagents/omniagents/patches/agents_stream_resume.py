from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)


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
    orig_fetch_response = OpenAIResponsesModel._fetch_response

    async def patched_fetch_response(
        self: Any,
        system_instructions: str | None,
        input: str | list[Any],
        model_settings: Any,
        tools: list[Any],
        output_schema: Any | None,
        handoffs: list[Any],
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        stream: bool = False,
        prompt: Any | None = None,
        background: bool | Omit = omit,
    ) -> Any:
        """Patched _fetch_response that supports background parameter."""
        # Get the original result by calling the original method
        from agents.models.openai_responses import (
            Converter,
            ItemHelpers,
            _to_dump_compatible,
            _debug,
        )
        from typing import cast, Any as TAny, Union
        from openai.types.responses import ResponseIncludable
        import json
        import logging

        logger = logging.getLogger(__name__)

        list_input = ItemHelpers.input_to_new_input_list(input)
        list_input = _to_dump_compatible(list_input)

        if model_settings.parallel_tool_calls and tools:
            parallel_tool_calls: bool | Omit = True
        elif model_settings.parallel_tool_calls is False:
            parallel_tool_calls = False
        else:
            parallel_tool_calls = omit

        tool_choice = Converter.convert_tool_choice(model_settings.tool_choice)
        converted_tools = Converter.convert_tools(tools, handoffs)
        converted_tools_payload = _to_dump_compatible(converted_tools.tools)
        response_format = Converter.get_response_format(output_schema)

        include_set: set[str] = set(converted_tools.includes)
        if model_settings.response_include is not None:
            include_set.update(model_settings.response_include)
        if model_settings.top_logprobs is not None:
            include_set.add("message.output_text.logprobs")
        include = cast(list[ResponseIncludable], list(include_set))

        extra_args = dict(model_settings.extra_args or {})
        if model_settings.top_logprobs is not None:
            extra_args["top_logprobs"] = model_settings.top_logprobs
        if model_settings.verbosity is not None:
            if response_format is not omit:
                response_format["verbosity"] = model_settings.verbosity  # type: ignore [index]
            else:
                response_format = {"verbosity": model_settings.verbosity}

        stream_param: bool | Omit = True if stream else omit

        response = await self._client.responses.create(
            previous_response_id=self._non_null_or_omit(previous_response_id),
            conversation=self._non_null_or_omit(conversation_id),
            instructions=self._non_null_or_omit(system_instructions),
            model=self.model,
            input=list_input,
            include=include,
            tools=converted_tools_payload,
            prompt=self._non_null_or_omit(prompt),
            temperature=self._non_null_or_omit(model_settings.temperature),
            top_p=self._non_null_or_omit(model_settings.top_p),
            truncation=self._non_null_or_omit(model_settings.truncation),
            max_output_tokens=self._non_null_or_omit(model_settings.max_tokens),
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            stream=cast(TAny, stream_param),
            extra_headers=self._merge_headers(model_settings),
            extra_query=model_settings.extra_query,
            extra_body=model_settings.extra_body,
            text=response_format,
            store=self._non_null_or_omit(model_settings.store),
            reasoning=self._non_null_or_omit(model_settings.reasoning),
            metadata=self._non_null_or_omit(model_settings.metadata),
            background=background,
            **extra_args,
        )
        return response

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
                        stream = await patched_fetch_response(
                            self,
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
                            background=True,
                        )
                        async for chunk in _iterate_events(stream):
                            yield chunk
                    else:
                        if not response_id:
                            raise RuntimeError(
                                "Cannot resume stream without response_id"
                            )
                        starting_after: int | Omit = (
                            last_seq if isinstance(last_seq, int) else omit
                        )
                        async with self._client.responses.stream(
                            response_id=response_id, starting_after=starting_after
                        ) as rstream:
                            async for chunk in _iterate_events(rstream):
                                yield chunk

                    # Check if stream ended without ResponseCompletedEvent
                    if final_response is None and response_id and attempt < max_attempts:
                        # Stream ended prematurely - retry by resuming
                        logger.warning(
                            f"Stream ended without ResponseCompletedEvent "
                            f"(response_id={response_id}, last_seq={last_seq}), "
                            f"retrying (attempt {attempt + 1}/{max_attempts})"
                        )
                        delay = _jitter_delay(base_delay, attempt, max_delay)
                        if delay > 0:
                            try:
                                await asyncio.sleep(delay)
                            except asyncio.CancelledError:
                                raise
                        attempt += 1
                        continue

                    if final_response and tracing.include_data():
                        span_response.span_data.response = final_response
                        span_response.span_data.input = input
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if (
                        response_id
                        and _is_retryable_exception(e)
                        and attempt < max_attempts
                    ):
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
                                "error": (
                                    str(e)
                                    if tracing.include_data()
                                    else e.__class__.__name__
                                ),
                            },
                        )
                    )
                    raise

    OpenAIResponsesModel._fetch_response = patched_fetch_response  # type: ignore[method-assign]
    OpenAIResponsesModel.stream_response = patched_stream_response  # type: ignore[method-assign]


_install_patch()
