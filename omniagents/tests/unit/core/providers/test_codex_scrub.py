"""Tests for the Codex transport-level unsupported-param scrub
(``core/providers/codex.scrub_codex_unsupported_params``).

The Codex backend 400s on ``prompt_cache_retention`` ("Unsupported
parameter"). The agent builder strips it when the session model config
names the provider — but worker runs whose model resolution fell through
to the ambient default client still carried it. The transport hook is
the guarantee: every codex-bound request is scrubbed.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from omniagents.core.providers.codex import scrub_codex_unsupported_params


def _post(body: dict) -> httpx.Request:
    return httpx.Request(
        "POST",
        "https://chatgpt.com/backend-api/codex/responses",
        json=body,
    )


def test_scrub_drops_prompt_cache_retention():
    request = _post(
        {
            "model": "gpt-5.6-sol",
            "prompt_cache_key": "omniagents:abc",
            "prompt_cache_retention": "24h",
            "input": [{"role": "user", "content": "hi"}],
        }
    )
    asyncio.run(scrub_codex_unsupported_params(request))

    body = json.loads(request.content.decode("utf-8"))
    assert "prompt_cache_retention" not in body
    # Everything else — including prompt_cache_key, which codex accepts —
    # survives, and the header matches the rewritten payload.
    assert body["prompt_cache_key"] == "omniagents:abc"
    assert body["input"] == [{"role": "user", "content": "hi"}]
    assert request.headers["Content-Length"] == str(len(request.content))


def test_scrub_leaves_clean_requests_untouched():
    body = {"model": "gpt-5.6-sol", "input": []}
    request = _post(body)
    original = request.content
    asyncio.run(scrub_codex_unsupported_params(request))
    assert request.content == original


def test_scrub_ignores_non_json_bodies():
    request = httpx.Request(
        "POST",
        "https://chatgpt.com/backend-api/codex/upload",
        content=b"prompt_cache_retention raw bytes, not json",
    )
    asyncio.run(scrub_codex_unsupported_params(request))
    assert request.content == b"prompt_cache_retention raw bytes, not json"
