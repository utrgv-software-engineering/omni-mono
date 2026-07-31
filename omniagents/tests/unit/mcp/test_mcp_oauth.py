"""Unit tests for the MCP OAuth broker (mcp_auth_* machinery).

A fake MCP resource server + authorization server (one ASGI app served
through ``httpx.ASGITransport``) drives the real
``mcp.client.auth.OAuthClientProvider`` end-to-end: RFC 9728 discovery,
dynamic client registration, PKCE authorization to the URL-handoff
point, code exchange, token storage (0600, never echoed), and the
non-interactive personality that turns browser-needed flows into typed
``auth_required`` / ``auth_expired`` failures.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from omniagents.core.agents.mcp_admin import (
    McpAuthFailed,
    McpAuthStateInvalid,
    classify_mcp_failure,
)
from omniagents.core.agents.mcp_oauth import (
    McpInteractiveAuthRequired,
    McpOAuthBroker,
)

from tests.fixtures.mcp_oauth_app import (
    ACCESS_SECRET,
    AUTH_CODE,
    BASE,
    REFRESH_SECRET,
    SERVER_URL,
    make_fake_oauth_app as _fake_oauth_app,
)


@pytest.fixture
def oauth_env(tmp_path):
    state: dict = {}
    app = _fake_oauth_app(state)

    def http_factory(**kwargs):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE, **kwargs
        )

    broker = McpOAuthBroker(token_dir=tmp_path / "mcp-auth", http_factory=http_factory)
    return broker, state, tmp_path


def _no_secret(payload):
    text = json.dumps(payload, default=str)
    for secret in (ACCESS_SECRET, REFRESH_SECRET):
        assert secret not in text, f"credential leaked: {text[:300]}"


def test_full_browser_flow_to_completion(oauth_env):
    broker, state, tmp_path = oauth_env

    async def scenario():
        start = await broker.start("github", SERVER_URL)
        assert start["state"] == "pending"
        assert start["server_name"] == "github"
        _no_secret(start)

        auth_url = start["auth_url"]
        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)
        assert parsed.path == "/authorize"
        assert query["client_id"] == ["test-client-id"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["response_type"] == ["code"]
        # The PKCE verifier never appears in the handoff URL.
        assert "code_verifier" not in query
        assert broker.state_of("github") == "pending"

        # Complete with the full redirect URL (the paste-back contract).
        redirect = (
            f"{start['redirect_uri']}?code={AUTH_CODE}" f"&state={query['state'][0]}"
        )
        done = await broker.complete(start["auth_id"], redirect)
        assert done["state"] == "completed"
        assert done["auth_state"] == "authenticated"
        _no_secret(done)

    asyncio.run(scenario())

    assert broker.state_of("github") == "authenticated"
    assert state["registered"]["token_endpoint_auth_method"] == "none"
    token_file = tmp_path / "mcp-auth" / "github.json"
    assert token_file.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    stored = json.loads(token_file.read_text())
    assert stored["tokens"]["access_token"] == ACCESS_SECRET


def test_completed_credentials_short_circuit_next_start(oauth_env):
    broker, state, _ = oauth_env

    async def scenario():
        start = await broker.start("github", SERVER_URL)
        await broker.complete(start["auth_id"], AUTH_CODE)
        # Second start: stored token satisfies the server without a browser.
        second = await broker.start("github", SERVER_URL)
        assert second["state"] == "completed"
        assert "auth_url" not in second

    asyncio.run(scenario())


def test_cancel_and_unknown_flow_are_typed(oauth_env):
    broker, _, _ = oauth_env

    async def scenario():
        start = await broker.start("github", SERVER_URL)
        assert broker.cancel(start["auth_id"]) is True
        assert broker.cancel(start["auth_id"]) is False
        with pytest.raises(McpAuthStateInvalid) as excinfo:
            await broker.complete(start["auth_id"], AUTH_CODE)
        assert excinfo.value.data["reason"] == "unknown_auth"
        with pytest.raises(McpAuthStateInvalid):
            await broker.complete(start["auth_id"], "")

    asyncio.run(scenario())


def test_bad_code_fails_typed_without_echoing(oauth_env):
    broker, _, _ = oauth_env

    async def scenario():
        start = await broker.start("github", SERVER_URL)
        with pytest.raises(McpAuthFailed) as excinfo:
            await broker.complete(start["auth_id"], "WRONG-CODE")
        _no_secret(excinfo.value.data)
        assert "WRONG-CODE" not in json.dumps(excinfo.value.data)

    asyncio.run(scenario())


def test_noninteractive_expired_tokens_raise_reauth_signal(oauth_env):
    """Expired stored credentials must surface as the typed reauth cue."""
    broker, state, tmp_path = oauth_env
    token_file = tmp_path / "mcp-auth" / "github.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "stale-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "stale-refresh",
                },
                "client_info": {
                    "client_id": "test-client-id",
                    "redirect_uris": ["http://localhost:53535/mcp/oauth/callback"],
                    "token_endpoint_auth_method": "none",
                },
            }
        )
    )
    assert broker.state_of("github") == "authenticated"

    async def scenario():
        auth = broker.noninteractive_auth("github", SERVER_URL)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_fake_oauth_app(state)),
            base_url=BASE,
            auth=auth,
        ) as client:
            with pytest.raises(McpInteractiveAuthRequired) as excinfo:
                await client.post(SERVER_URL, json={"jsonrpc": "2.0", "id": 0})
            assert excinfo.value.had_tokens is True
            assert classify_mcp_failure(excinfo.value)[0] == "auth_expired"

    asyncio.run(scenario())
    assert broker.state_of("github") == "expired"


def test_noninteractive_without_tokens_signals_auth_required(oauth_env):
    broker, state, _ = oauth_env

    async def scenario():
        auth = broker.noninteractive_auth("fresh", SERVER_URL)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_fake_oauth_app(state)),
            base_url=BASE,
            auth=auth,
        ) as client:
            with pytest.raises(McpInteractiveAuthRequired) as excinfo:
                await client.post(SERVER_URL, json={"jsonrpc": "2.0", "id": 0})
            assert excinfo.value.had_tokens is False
            assert classify_mcp_failure(excinfo.value)[0] == "auth_required"

    asyncio.run(scenario())
    assert broker.state_of("fresh") == "required"
