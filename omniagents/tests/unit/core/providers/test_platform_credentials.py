"""Platform credential vending: fetch/cache/expiry semantics, the
Optional-on-failure contract, {session_id} ref resolution, local session
bootstrap, and the product-installed stored-credentials loader seam."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from omniagents.core.providers.platform import bootstrap
from omniagents.core.providers.platform.credentials import (
    PlatformCredentialProvider,
    ensure_platform_session,
)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _iso_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def provider():
    return PlatformCredentialProvider(
        "https://platform.test/", runtime_token="rt-token", session_id="sess-1"
    )


def _patch_get(monkeypatch, responses):
    """Queue fake responses for requests.get; records call URLs."""
    calls = []

    def _get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(requests, "get", _get)
    return calls


# ---------------------------------------------------------------------------
# Fetch + cache
# ---------------------------------------------------------------------------


def test_fetch_success_and_cache_hit(provider, monkeypatch):
    cred = {
        "credential_type": "api_key",
        "api_key": "sk-1",
        "expires_at": _iso_in(3600),
    }
    calls = _patch_get(monkeypatch, [_FakeResponse(payload=cred)])

    assert provider.get_credential_sync(42) == cred
    # Second call served from cache — no new HTTP request.
    assert provider.get_credential_sync(42) == cred
    assert len(calls) == 1
    assert (
        calls[0]["url"] == "https://platform.test/api/v1/sessions/sess-1/credentials/42"
    )
    assert calls[0]["headers"]["Authorization"] == "Bearer rt-token"


def test_refetches_within_expiry_margin(provider, monkeypatch):
    # Expires 30s from now — inside the 60s refresh margin, so a second
    # call must refetch.
    first = {"api_key": "old", "expires_at": _iso_in(30)}
    second = {"api_key": "new", "expires_at": _iso_in(3600)}
    calls = _patch_get(
        monkeypatch, [_FakeResponse(payload=first), _FakeResponse(payload=second)]
    )

    assert provider.get_credential_sync(42)["api_key"] == "old"
    assert provider.get_credential_sync(42)["api_key"] == "new"
    assert len(calls) == 2


def test_unparseable_expiry_uses_default_ttl(provider, monkeypatch):
    cred = {"api_key": "k", "expires_at": "not-a-date"}
    _patch_get(monkeypatch, [_FakeResponse(payload=cred)])
    assert provider.get_credential_sync(1) == cred
    # Cached with the fallback TTL — comfortably in the future.
    assert provider._cache_expiry["1"] > time.time() + 400


def test_by_ref_resolves_session_placeholder(provider, monkeypatch):
    cred = {"api_key": "k", "expires_at": _iso_in(3600)}
    calls = _patch_get(monkeypatch, [_FakeResponse(payload=cred)])

    ref = "/api/v1/sessions/{session_id}/credentials/7"
    assert provider.get_credential_by_ref_sync(ref) == cred
    assert (
        calls[0]["url"] == "https://platform.test/api/v1/sessions/sess-1/credentials/7"
    )
    # Cached under the unresolved ref.
    assert provider.get_credential_by_ref_sync(ref) == cred
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Failure contract: None, never error dicts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(status_code=401, payload={"error": "expired"}),
        _FakeResponse(status_code=403, payload={"error": "denied"}),
        _FakeResponse(status_code=500, payload={"error": "boom"}),
        _FakeResponse(payload=ValueError("bad json")),
        _FakeResponse(payload=["not", "a", "dict"]),
        requests.RequestException("network down"),
    ],
)
def test_failures_return_none(provider, monkeypatch, response):
    _patch_get(monkeypatch, [response])
    assert provider.get_credential_sync(42) is None
    # Failures are not cached.
    assert provider._cache == {}


def test_invalidate(provider, monkeypatch):
    cred = {"api_key": "k", "expires_at": _iso_in(3600)}
    calls = _patch_get(
        monkeypatch, [_FakeResponse(payload=cred), _FakeResponse(payload=cred)]
    )
    provider.get_credential_sync(42)
    provider.invalidate(42)
    provider.get_credential_sync(42)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_wrapper_delegates(provider, monkeypatch):
    cred = {"api_key": "k", "expires_at": _iso_in(3600)}
    _patch_get(monkeypatch, [_FakeResponse(payload=cred)])
    assert await provider.get_credential(42) == cred


# ---------------------------------------------------------------------------
# ensure_platform_session
# ---------------------------------------------------------------------------


def test_ensure_session_success_exports_env(monkeypatch):
    posted = {}

    def _post(url, headers=None, json=None, timeout=None):
        posted.update({"url": url, "json": json, "headers": headers})
        return _FakeResponse(payload={"session_id": "s-9", "session_token": "st-9"})

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.delenv("OMNI_SESSION_ID", raising=False)
    monkeypatch.delenv("OMNI_RUNTIME_TOKEN", raising=False)

    session = ensure_platform_session(
        "https://platform.test", "access-tok", agent="my_product"
    )
    assert session == {"session_id": "s-9", "session_token": "st-9"}
    assert posted["url"] == "https://platform.test/api/v1/sessions/bootstrap"
    assert posted["json"] == {"agent": "my_product"}
    assert posted["headers"]["Authorization"] == "Bearer access-tok"
    import os

    assert os.environ["OMNI_SESSION_ID"] == "s-9"
    assert os.environ["OMNI_RUNTIME_TOKEN"] == "st-9"


def test_ensure_session_no_env_export_when_disabled(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: _FakeResponse(
            payload={"session_id": "s", "session_token": "t"}
        ),
    )
    monkeypatch.delenv("OMNI_SESSION_ID", raising=False)
    session = ensure_platform_session(
        "https://platform.test", "tok", agent="p", export_env=False
    )
    assert session is not None
    import os

    assert "OMNI_SESSION_ID" not in os.environ


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(status_code=401, text="denied"),
        _FakeResponse(payload=ValueError("bad json")),
        requests.RequestException("down"),
    ],
)
def test_ensure_session_failures_return_none(monkeypatch, response):
    def _post(*a, **k):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(requests, "post", _post)
    assert ensure_platform_session("https://p.test", "tok", agent="p") is None


# ---------------------------------------------------------------------------
# Stored-credentials loader seam (bootstrap)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_loader():
    prior = bootstrap._stored_credentials_loader
    try:
        yield
    finally:
        bootstrap.set_platform_credentials_loader(prior)


def test_no_loader_means_no_stored_credentials():
    bootstrap.set_platform_credentials_loader(None)
    assert bootstrap._find_stored_credentials() is None


def test_loader_supplies_credentials():
    creds = {"platform_url": "https://p.test", "access_token": "tok"}
    bootstrap.set_platform_credentials_loader(lambda: creds)
    assert bootstrap._find_stored_credentials() == creds


def test_loader_without_access_token_ignored():
    bootstrap.set_platform_credentials_loader(
        lambda: {"platform_url": "https://p.test"}
    )
    assert bootstrap._find_stored_credentials() is None


def test_loader_exception_is_contained():
    def _boom():
        raise RuntimeError("disk on fire")

    bootstrap.set_platform_credentials_loader(_boom)
    assert bootstrap._find_stored_credentials() is None
