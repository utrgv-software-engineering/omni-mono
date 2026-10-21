"""Management-plane client: credential store round-trip + permissions,
env-then-file resolution, refresh-on-401 policy fetches in both scopes,
device-code login, and the bootstrap/policy-hook delegation onto the one
shared client."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest
import requests

from omniagents.core.providers.platform import bootstrap, client


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

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _isolated_client(tmp_path, monkeypatch):
    """Bind the store to a temp dir, reset the slug, scrub platform env."""
    for var in (
        "OMNI_PLATFORM_URL",
        "OMNI_RUNTIME_TOKEN",
        "OMNI_ACCESS_TOKEN",
        "OMNI_SESSION_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    prior_dir = client._credential_store_dir
    prior_slug = client._product_slug
    client.set_credential_store_dir(tmp_path)
    client.set_product_slug("omniagents")
    yield
    client.set_credential_store_dir(prior_dir)
    client.set_product_slug(prior_slug)


def _patch_get(monkeypatch, responses):
    """Queue fake responses for requests.get; records call URLs/headers."""
    calls = []

    def _get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(requests, "get", _get)
    return calls


def _patch_post(monkeypatch, responses):
    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(requests, "post", _post)
    return calls


_CREDS = {
    "platform_url": "https://platform.test",
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "user": {"email": "u@test", "name": "U"},
}


# ---------------------------------------------------------------------------
# Credential store
# ---------------------------------------------------------------------------


def test_credential_round_trip_and_permissions(tmp_path):
    client.save_credentials(_CREDS)
    path = client.credential_path()
    assert path == tmp_path / "platform.json"
    assert path.exists()
    if os.name != "nt":  # Windows has no POSIX mode bits (profile ACLs apply)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600
    assert client.load_credentials() == _CREDS


def test_clear_credentials_removes_file():
    client.save_credentials(_CREDS)
    client.clear_credentials()
    assert not client.credential_path().exists()
    assert client.load_credentials() is None
    # Clearing again is a no-op
    client.clear_credentials()


def test_load_credentials_missing_or_invalid(tmp_path):
    assert client.load_credentials() is None
    client.credential_path().write_text("{not json")
    assert client.load_credentials() is None
    client.credential_path().write_text(json.dumps(["not", "a", "dict"]))
    assert client.load_credentials() is None


def test_store_dir_binding(tmp_path):
    other = tmp_path / "product"
    client.set_credential_store_dir(other)
    client.save_credentials(_CREDS)
    assert (other / "platform.json").exists()


# ---------------------------------------------------------------------------
# Env-then-file resolution
# ---------------------------------------------------------------------------


def test_url_env_wins_and_strips_trailing_slash(monkeypatch):
    client.save_credentials(_CREDS)
    monkeypatch.setenv("OMNI_PLATFORM_URL", "https://env.test/")
    assert client.get_platform_url() == "https://env.test"


def test_url_falls_back_to_store():
    client.save_credentials(_CREDS)
    assert client.get_platform_url() == "https://platform.test"
    client.clear_credentials()
    assert client.get_platform_url() is None


def test_token_env_priority(monkeypatch):
    client.save_credentials(_CREDS)
    assert client.get_access_token() == "at-1"
    monkeypatch.setenv("OMNI_ACCESS_TOKEN", "env-access")
    assert client.get_access_token() == "env-access"
    monkeypatch.setenv("OMNI_RUNTIME_TOKEN", "env-runtime")
    assert client.get_access_token() == "env-runtime"


def test_is_authenticated_requires_url_and_token(monkeypatch):
    assert client.is_authenticated() is False
    monkeypatch.setenv("OMNI_PLATFORM_URL", "https://env.test")
    assert client.is_authenticated() is False
    monkeypatch.setenv("OMNI_ACCESS_TOKEN", "tok")
    assert client.is_authenticated() is True


def test_resolve_platform_env_pair_carries_session(monkeypatch):
    monkeypatch.setenv("OMNI_PLATFORM_URL", "https://env.test/")
    monkeypatch.setenv("OMNI_RUNTIME_TOKEN", "rt")
    monkeypatch.setenv("OMNI_SESSION_ID", "sess-9")
    assert client.resolve_platform() == {
        "platform_url": "https://env.test",
        "access_token": "rt",
        "session_id": "sess-9",
    }


def test_resolve_platform_partial_env_falls_to_store(monkeypatch):
    # URL alone in the env is not enough — both must come from one layer.
    monkeypatch.setenv("OMNI_PLATFORM_URL", "https://env.test")
    client.save_credentials(_CREDS)
    assert client.resolve_platform() == {
        "platform_url": "https://platform.test",
        "access_token": "at-1",
        "session_id": "",
    }


def test_resolve_platform_custom_loader_and_failure():
    assert client.resolve_platform() is None
    loaded = client.resolve_platform(
        credentials_loader=lambda: {
            "platform_url": "https://l.test/",
            "access_token": "lt",
        }
    )
    assert loaded == {
        "platform_url": "https://l.test",
        "access_token": "lt",
        "session_id": "",
    }

    def _boom():
        raise RuntimeError("disk on fire")

    assert client.resolve_platform(credentials_loader=_boom) is None


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


def test_refresh_access_token_updates_store(monkeypatch):
    client.save_credentials(_CREDS)
    calls = _patch_post(monkeypatch, [_FakeResponse(payload={"access_token": "at-2"})])
    assert client.refresh_access_token() == "at-2"
    assert calls[0]["url"] == "https://platform.test/api/v1/auth/refresh"
    assert calls[0]["headers"]["Authorization"] == "Bearer rt-1"
    assert client.load_credentials()["access_token"] == "at-2"


def test_refresh_access_token_failures(monkeypatch):
    # No credentials at all
    assert client.refresh_access_token() is None
    # Credentials without a refresh token
    client.save_credentials(
        {"platform_url": "https://platform.test", "access_token": "a"}
    )
    assert client.refresh_access_token() is None
    # HTTP failure
    client.save_credentials(_CREDS)
    _patch_post(monkeypatch, [_FakeResponse(status_code=401)])
    assert client.refresh_access_token() is None
    # Network error
    _patch_post(monkeypatch, [requests.ConnectionError("down")])
    assert client.refresh_access_token() is None
    assert client.load_credentials()["access_token"] == "at-1"


# ---------------------------------------------------------------------------
# Policy client — user scope
# ---------------------------------------------------------------------------


def test_fetch_policy_user_scope_uses_bound_slug(monkeypatch):
    client.save_credentials(_CREDS)
    client.set_product_slug("omni_code")
    policy = {"session": {"phi_boundary": False}}
    calls = _patch_get(monkeypatch, [_FakeResponse(payload=policy)])
    assert client.fetch_policy() == policy
    assert calls[0]["url"] == "https://platform.test/api/v1/policy/omni_code"
    assert calls[0]["headers"]["Authorization"] == "Bearer at-1"


def test_fetch_policy_explicit_slug_overrides_binding(monkeypatch):
    client.save_credentials(_CREDS)
    calls = _patch_get(monkeypatch, [_FakeResponse(payload={})])
    assert client.fetch_policy(slug="other") == {}
    assert calls[0]["url"] == "https://platform.test/api/v1/policy/other"


def test_fetch_policy_without_credentials_is_none(monkeypatch):
    calls = _patch_get(monkeypatch, [])
    assert client.fetch_policy() is None
    assert calls == []


def test_fetch_policy_refresh_on_401_retries_once(monkeypatch):
    client.save_credentials(_CREDS)
    policy = {"mcp_servers": []}
    calls = _patch_get(
        monkeypatch,
        [_FakeResponse(status_code=401), _FakeResponse(payload=policy)],
    )
    _patch_post(monkeypatch, [_FakeResponse(payload={"access_token": "at-2"})])
    assert client.fetch_policy() == policy
    assert len(calls) == 2
    assert calls[0]["headers"]["Authorization"] == "Bearer at-1"
    assert calls[1]["headers"]["Authorization"] == "Bearer at-2"


def test_fetch_policy_401_with_failed_refresh_is_none(monkeypatch):
    client.save_credentials(_CREDS)
    calls = _patch_get(monkeypatch, [_FakeResponse(status_code=401)])
    _patch_post(monkeypatch, [_FakeResponse(status_code=401)])
    assert client.fetch_policy() is None
    assert len(calls) == 1


def test_fetch_policy_refresh_opt_out(monkeypatch):
    client.save_credentials(_CREDS)
    refreshed = []
    monkeypatch.setattr(
        client, "refresh_access_token", lambda: refreshed.append(1) or "at-2"
    )
    _patch_get(monkeypatch, [_FakeResponse(status_code=401)])
    assert client.fetch_policy(refresh_on_401=False) is None
    assert refreshed == []


def test_fetch_policy_403_and_error_are_none(monkeypatch):
    client.save_credentials(_CREDS)
    _patch_get(monkeypatch, [_FakeResponse(status_code=403)])
    assert client.fetch_policy() is None
    _patch_get(monkeypatch, [_FakeResponse(status_code=500, text="boom")])
    assert client.fetch_policy() is None
    _patch_get(monkeypatch, [requests.ConnectionError("down")])
    assert client.fetch_policy() is None


# ---------------------------------------------------------------------------
# Policy client — session scope
# ---------------------------------------------------------------------------


def test_fetch_policy_session_scope(monkeypatch):
    policy = {"session": {"phi_boundary": True}}
    calls = _patch_get(monkeypatch, [_FakeResponse(payload=policy)])
    result = client.fetch_policy(
        session_id="sess-1",
        platform_url="https://platform.test/",
        token="runtime-tok",
    )
    assert result == policy
    assert calls[0]["url"] == "https://platform.test/api/v1/sessions/sess-1/policy"
    assert calls[0]["headers"]["Authorization"] == "Bearer runtime-tok"


def test_fetch_policy_session_scope_refreshes_on_401(monkeypatch):
    client.save_credentials(_CREDS)
    policy = {"skills": []}
    calls = _patch_get(
        monkeypatch,
        [_FakeResponse(status_code=401), _FakeResponse(payload=policy)],
    )
    _patch_post(monkeypatch, [_FakeResponse(payload={"access_token": "at-2"})])
    result = client.fetch_policy(
        session_id="sess-1",
        platform_url="https://platform.test",
        token="stale-runtime-tok",
    )
    assert result == policy
    assert calls[1]["headers"]["Authorization"] == "Bearer at-2"


# ---------------------------------------------------------------------------
# Device-code login
# ---------------------------------------------------------------------------


def test_device_code_login_polls_until_authenticated(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = _patch_post(
        monkeypatch,
        [
            _FakeResponse(
                payload={
                    "device_code": "dev-1",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://platform.test/activate",
                    "interval": 0,
                    "expires_in": 900,
                }
            ),
            _FakeResponse(payload={"status": "pending"}),
            _FakeResponse(
                payload={
                    "status": "authenticated",
                    "access_token": "at-new",
                    "refresh_token": "rt-new",
                    "expires_in": 3600,
                    "user": {"email": "u@test"},
                }
            ),
        ],
    )
    creds = client.device_code_login("https://platform.test/")
    assert creds == {
        "platform_url": "https://platform.test",
        "access_token": "at-new",
        "refresh_token": "rt-new",
        "expires_at": 3600,
        "user": {"email": "u@test"},
    }
    assert calls[0]["url"] == "https://platform.test/api/v1/auth/device_code"
    assert calls[1]["json"] == {"device_code": "dev-1"}


def test_device_code_login_expired_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    _patch_post(
        monkeypatch,
        [
            _FakeResponse(
                payload={
                    "device_code": "dev-1",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://platform.test/activate",
                    "interval": 0,
                }
            ),
            _FakeResponse(payload={"status": "expired"}),
        ],
    )
    with pytest.raises(RuntimeError, match="expired"):
        client.device_code_login("https://platform.test")


# ---------------------------------------------------------------------------
# Bootstrap delegation (_detect_platform → resolve_platform)
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_loader():
    prior = bootstrap._stored_credentials_loader
    yield
    bootstrap.set_platform_credentials_loader(prior)


def test_detect_platform_env_priority(monkeypatch, _reset_loader):
    bootstrap.set_platform_credentials_loader(None)
    monkeypatch.setenv("OMNI_PLATFORM_URL", "https://env.test/")
    monkeypatch.setenv("OMNI_RUNTIME_TOKEN", "rt")
    monkeypatch.setenv("OMNI_SESSION_ID", "sess-2")
    assert bootstrap._detect_platform() == {
        "platform_url": "https://env.test",
        "access_token": "rt",
        "session_id": "sess-2",
    }


def test_detect_platform_product_loader_wins(_reset_loader):
    client.save_credentials(_CREDS)
    bootstrap.set_platform_credentials_loader(
        lambda: {"platform_url": "https://product.test", "access_token": "pt"}
    )
    assert bootstrap._detect_platform() == {
        "platform_url": "https://product.test",
        "access_token": "pt",
        "session_id": "",
    }


def test_detect_platform_falls_back_to_client_store(_reset_loader):
    bootstrap.set_platform_credentials_loader(None)
    client.save_credentials(_CREDS)
    assert bootstrap._detect_platform() == {
        "platform_url": "https://platform.test",
        "access_token": "at-1",
        "session_id": "",
    }
    client.clear_credentials()
    assert bootstrap._detect_platform() is None


# ---------------------------------------------------------------------------
# PlatformPolicyFetcher delegation
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine on a private loop without touching the current-loop
    state (asyncio.run would unset it for later tests in the session)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_policy_fetcher_delegates_to_client(monkeypatch):
    from omniagents.core.providers.platform.policy import PlatformPolicyFetcher

    policy = {"mcp_servers": [], "session": {}}
    calls = []

    def _fake_fetch_policy(**kwargs):
        calls.append(kwargs)
        return policy

    monkeypatch.setattr(client, "fetch_policy", _fake_fetch_policy)
    fetcher = PlatformPolicyFetcher(
        "https://platform.test/", runtime_token="rt", session_id="sess-1"
    )
    assert _run(fetcher.fetch()) == policy
    assert calls == [
        {
            "session_id": "sess-1",
            "platform_url": "https://platform.test",
            "token": "rt",
        }
    ]
    # Cached — no second client call
    assert _run(fetcher.fetch()) == policy
    assert len(calls) == 1


def test_policy_fetcher_maps_failure_to_empty_dict(monkeypatch):
    from omniagents.core.providers.platform.policy import PlatformPolicyFetcher

    monkeypatch.setattr(client, "fetch_policy", lambda **kw: None)
    fetcher = PlatformPolicyFetcher(
        "https://platform.test", runtime_token="rt", session_id="sess-1"
    )
    assert _run(fetcher.fetch()) == {}
    assert fetcher.policy is None
