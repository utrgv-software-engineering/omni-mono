"""Bounded request retries, before any compacted context is persisted."""

import asyncio
import logging
import math
from typing import Any

import httpx
from openai import APIConnectionError, APIError, APIStatusError

logger = logging.getLogger("omniagents.session.compaction")
TRANSIENT_ERROR_CODES = frozenset(
    {
        "server_error",
        "internal_server_error",
        "rate_limit_exceeded",
        "server_overloaded",
    }
)


class TransientCompactionError(RuntimeError):
    """A compaction stream was interrupted or reported a temporary failure."""


class CompactionRetriesExhausted(RuntimeError):
    """The request budget is exhausted; the enclosing turn must not retry it."""


def _retryable(error: Exception) -> bool:
    if isinstance(error, APIStatusError):
        return error.status_code in {408, 429} or error.status_code >= 500
    if isinstance(error, APIError) and error.code in TRANSIENT_ERROR_CODES:
        return True
    return isinstance(
        error, (APIConnectionError, httpx.TransportError, TransientCompactionError)
    )


def _delay(error: Exception, retry: int) -> float:
    response = getattr(error, "response", None)
    if response is not None:
        try:
            seconds = float(response.headers.get("retry-after", ""))
            if math.isfinite(seconds) and seconds >= 0:
                return min(seconds, 30.0)
        except ValueError:
            pass
    return float(2**retry)


class RetryingCompactionClient:
    def __init__(self, client: Any):
        # Own the complete request budget, including failures after SSE headers.
        # Otherwise SDK HTTP retries multiply these retries.
        self._client = (
            client.with_options(max_retries=0)
            if callable(getattr(client, "with_options", None))
            else client
        )
        self.responses = self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def with_options(self, **kwargs: Any) -> "RetryingCompactionClient":
        if callable(getattr(self._client, "with_options", None)):
            return type(self)(self._client.with_options(**kwargs))
        return self

    async def compact(self, **kwargs: Any) -> Any:
        for attempt in range(3):
            try:
                return await self._client.responses.compact(**kwargs)
            except Exception as error:
                if not _retryable(error):
                    raise
                if attempt == 2:
                    raise CompactionRetriesExhausted(
                        "Compaction failed after 3 attempts; history was preserved."
                    ) from error
                delay = _delay(error, attempt)
                logger.warning(
                    "Compaction interrupted (%s); retry %s/2 in %.1fs",
                    type(error).__name__,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
