"""Patch OpenAIResponsesModel.stream_response to avoid token-level streaming.

The OpenAI Agents SDK's ``run_streamed()`` calls ``model.stream_response()``
which issues the API request with ``stream=True``.  If the token stream is
interrupted (network hiccup, context-length issue, etc.) without emitting a
``ResponseCompletedEvent``, the SDK raises ``ModelBehaviorError: Model did not
produce a final response!`` — an unrecoverable error.

This patch replaces ``stream_response`` so that it:

1. Calls ``_fetch_response(stream=False)`` — a single request/response, no
   partial token stream, guaranteed to either succeed or raise.
2. Synthesises the ``ResponseOutputItemDoneEvent`` and
   ``ResponseCompletedEvent`` that the SDK's streaming run-loop expects.

The agent-level event stream (tool_called, tool_result, message_output) is
**not** affected — ``run_streamed()`` still yields those events in real-time
as the agent loop progresses across turns.  Only the *per-turn LLM call* is
made non-streaming.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputItemDoneEvent,
    ResponseStreamEvent,
)

from agents.tracing import SpanError, response_span

logger = logging.getLogger(__name__)


def _install_patch() -> None:
    from agents.models.openai_responses import OpenAIResponsesModel

    # Keep the SDK's real streaming implementation so we can delegate to it for
    # backends that mandate server-sent streaming (see below).
    _original_stream_response = OpenAIResponsesModel.stream_response

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
        # The ChatGPT Codex backend rejects `stream=false` ("Stream must be set
        # to true"), so the non-streaming override below can't be used there —
        # fall back to the SDK's genuine streaming path for it. Detected by the
        # client base URL so no other backend is affected.
        client = getattr(self, "_client", None)
        base_url = str(getattr(client, "base_url", "") or "")
        if "backend-api/codex" in base_url:
            async for event in _original_stream_response(
                self,
                system_instructions,
                input,
                model_settings,
                tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            ):
                yield event
            return

        with response_span(disabled=tracing.is_disabled()) as span_response:
            try:
                response: Response = await self._fetch_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    previous_response_id=previous_response_id,
                    conversation_id=conversation_id,
                    stream=False,
                    prompt=prompt,
                )

                if tracing.include_data():
                    span_response.span_data.response = response
                    span_response.span_data.input = input

                # Synthesise the events the SDK run-loop expects.
                seq = 0
                for idx, item in enumerate(response.output):
                    seq += 1
                    yield ResponseOutputItemDoneEvent(
                        item=item,
                        output_index=idx,
                        sequence_number=seq,
                        type="response.output_item.done",
                    )

                seq += 1
                yield ResponseCompletedEvent(
                    response=response,
                    sequence_number=seq,
                    type="response.completed",
                )

            except Exception as e:
                span_response.set_error(
                    SpanError(
                        message="Error getting response",
                        data={
                            "error": (
                                str(e)
                                if tracing.include_data()
                                else e.__class__.__name__
                            ),
                        },
                    )
                )
                logger.error(f"Error getting response: {e}")
                raise

    OpenAIResponsesModel.stream_response = patched_stream_response  # type: ignore[method-assign]


_install_patch()
