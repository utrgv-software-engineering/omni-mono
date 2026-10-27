"""Give each event loop its own HTTP client for the Agents SDK's OpenAI calls.

The SDK shares one ``httpx.AsyncClient`` across the whole process
(``agents.models.openai_provider.shared_http_client``). A pooled connection
belongs to the event loop that opened it, so once that loop has closed, the
next run on another loop picks the dead connection and fails with "Event
loop is closed" before retrying with backoff. Anything that runs agents on
more than one loop in a process hits this: a server running evaluations
with ``asyncio.run`` on worker threads, or an optimizer evaluating each
batch with ``asyncio.run``.

Connections are still shared, within a loop: the client is created once per
event loop and dropped with it.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any


def _install_patch() -> None:
    from agents.models import openai_provider
    from openai import DefaultAsyncHttpxClient

    by_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]" = (
        weakref.WeakKeyDictionary()
    )
    outside_a_loop: list[Any] = []

    def shared_http_client() -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Built outside any loop (a provider set up ahead of a run).
            if not outside_a_loop:
                outside_a_loop.append(DefaultAsyncHttpxClient())
            return outside_a_loop[0]
        client = by_loop.get(loop)
        if client is None:
            client = by_loop[loop] = DefaultAsyncHttpxClient()
        return client

    openai_provider.shared_http_client = shared_http_client


_install_patch()
