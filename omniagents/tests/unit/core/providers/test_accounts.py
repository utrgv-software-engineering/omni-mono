"""Unit tests for the provider account manager (``account_*`` RPC core).

Covers the normalized capability matrix, every login mode (API key,
device-code, browser OAuth to the URL-handoff point), cancellation
mid-login, refresh failure, usage snapshots — and, adversarially, that
no snapshot or flow result ever carries credential material.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

import omniagents.core.providers.codex as codex
from omniagents.core.providers.accounts import (
    CHATGPT_PROVIDER_ID,
    AccountAuthFailed,
    AccountCapabilityUnsupported,
    AccountManager,
    AccountStateInvalid,
)

REFRESH_SECRET = "rt-REFRESH-SECRET-abc123"
ACCESS_SECRET = "at-ACCESS-SECRET-def456"
API_KEY_SECRET = "sk-API-KEY-SECRET-xyz789"
SECRETS = (REFRESH_SECRET, ACCESS_SECRET, API_KEY_SECRET)

PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY",
)


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJh.{body}.sig"


def _access_token_with_claims() -> str:
    return _jwt(
        {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-1",
                "chatgpt_plan_type": "plus",
            },
            "https://api.openai.com/profile": {"email": "user@example.edu"},
        }
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    codex.set_token_store_dir(tmp_path)
    for var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    codex._rate_limit_snapshot.clear()
    yield
    codex._token_store_dir = None
    codex._rate_limit_snapshot.clear()


def _sign_in_codex() -> None:
    codex.save_tokens(
        {
            "refresh": REFRESH_SECRET,
            "access": _access_token_with_claims(),
            "expires": int(time.time() * 1000) + 3_600_000,
            "account_id": "acct-1",
        }
    )


def _assert_no_secrets(payload) -> None:
    text = json.dumps(payload)
    for secret in SECRETS:
        assert secret not in text, f"secret leaked into payload: {payload}"


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------


def test_status_lists_normalized_capability_matrix():
    status = AccountManager().status()
    by_id = {entry["id"]: entry for entry in status["providers"]}
    assert by_id[CHATGPT_PROVIDER_ID]["capabilities"] == {
        "login_modes": ["device_code", "browser"],
        "logout": True,
        "refresh": True,
        "usage": True,
    }
    assert by_id["openai"]["capabilities"] == {
        "login_modes": ["api_key"],
        "logout": True,
        "refresh": False,
        "usage": False,
    }
    assert {"anthropic", "google", "azure-openai"} <= set(by_id)
    assert status["selected_provider"] is None


@pytest.mark.asyncio
async def test_unsupported_operations_raise_typed_capability_errors():
    manager = AccountManager()
    with pytest.raises(AccountCapabilityUnsupported) as excinfo:
        await manager.refresh("openai")
    assert excinfo.value.code == -32040
    assert excinfo.value.data["kind"] == "account_capability_unsupported"
    assert excinfo.value.data["operation"] == "refresh"
    assert excinfo.value.data["supported"] == [CHATGPT_PROVIDER_ID]

    with pytest.raises(AccountCapabilityUnsupported):
        manager.usage("anthropic")

    with pytest.raises(AccountCapabilityUnsupported) as excinfo:
        await manager.login_start(CHATGPT_PROVIDER_ID, "api_key", api_key="x")
    assert excinfo.value.data["operation"] == "login:api_key"
    assert excinfo.value.data["supported"] == ["browser", "device_code"]

    with pytest.raises(AccountCapabilityUnsupported):
        await manager.login_start("openai", "device_code")


def test_unknown_provider_is_typed_state_error():
    manager = AccountManager()
    with pytest.raises(AccountStateInvalid) as excinfo:
        manager.logout("made-up")
    assert excinfo.value.code == -32041
    assert excinfo.value.data["reason"] == "unknown_provider"


# ---------------------------------------------------------------------------
# API-key flow (no browser required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_login_logout_roundtrip(monkeypatch):
    import os

    manager = AccountManager()
    result = await manager.login_start("openai", "api_key", api_key=API_KEY_SECRET)
    assert result["state"] == "completed"
    assert result["account"]["state"] == "signed_in"
    assert result["account"]["source"] == "rpc"
    _assert_no_secrets(result)
    assert os.environ["OPENAI_API_KEY"] == API_KEY_SECRET

    logout = manager.logout("openai")
    assert logout["ok"] is True
    assert logout["account"]["state"] == "signed_out"
    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.asyncio
async def test_api_key_login_requires_key():
    manager = AccountManager()
    with pytest.raises(AccountStateInvalid) as excinfo:
        await manager.login_start("openai", "api_key")
    assert excinfo.value.data["reason"] == "api_key_required"


def test_externally_managed_key_visible_but_not_ours_to_clear(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY_SECRET)
    manager = AccountManager()
    snapshot = manager.provider_snapshot("anthropic")
    assert snapshot["state"] == "signed_in"
    assert snapshot["source"] == "env"
    _assert_no_secrets(snapshot)
    with pytest.raises(AccountStateInvalid) as excinfo:
        manager.logout("anthropic")
    assert excinfo.value.data["reason"] == "externally_managed"


def test_logout_signed_out_provider_is_state_error():
    with pytest.raises(AccountStateInvalid) as excinfo:
        AccountManager().logout("openai")
    assert excinfo.value.data["reason"] == "not_signed_in"


# ---------------------------------------------------------------------------
# Device-code flow
# ---------------------------------------------------------------------------


def _patch_device_start(monkeypatch):
    async def fake_start():
        return {
            "device_auth_id": "dev-1",
            "user_code": "ABCD-1234",
            "interval": 1,
            "verification_url": codex.DEVICE_VERIFICATION_URL,
        }

    monkeypatch.setattr(codex, "device_auth_start", fake_start)


@pytest.mark.asyncio
async def test_device_flow_start_poll_complete(monkeypatch):
    _patch_device_start(monkeypatch)
    polls = {"count": 0}

    async def fake_poll(device_auth_id, user_code):
        assert (device_auth_id, user_code) == ("dev-1", "ABCD-1234")
        polls["count"] += 1
        if polls["count"] < 2:
            return None
        _sign_in_codex()
        return codex.load_tokens()

    monkeypatch.setattr(codex, "device_auth_poll", fake_poll)

    manager = AccountManager()
    started = await manager.login_start(CHATGPT_PROVIDER_ID, "device_code")
    assert started["state"] == "pending"
    assert started["user_code"] == "ABCD-1234"
    assert started["verification_url"].startswith("https://auth.openai.com")
    _assert_no_secrets(started)

    pending = await manager.login_complete(started["login_id"])
    assert pending["state"] == "pending"

    done = await manager.login_complete(started["login_id"])
    assert done["state"] == "completed"
    assert done["account"]["state"] == "signed_in"
    assert done["account"]["identity"] == {
        "account_id": "acct-1",
        "plan": "plus",
        "email": "user@example.edu",
    }
    _assert_no_secrets(done)

    # The login is consumed — completing again is a typed state error.
    with pytest.raises(AccountStateInvalid) as excinfo:
        await manager.login_complete(started["login_id"])
    assert excinfo.value.data["reason"] == "unknown_login"


@pytest.mark.asyncio
async def test_completed_device_login_survives_manager_restart(monkeypatch):
    _patch_device_start(monkeypatch)

    async def fake_poll(device_auth_id, user_code):
        _sign_in_codex()
        return codex.load_tokens()

    monkeypatch.setattr(codex, "device_auth_poll", fake_poll)

    before_restart = AccountManager()
    started = await before_restart.login_start(CHATGPT_PROVIDER_ID, "device_code")
    completed = await before_restart.login_complete(started["login_id"])
    assert completed["account"]["state"] == "signed_in"

    after_restart = AccountManager()
    status = after_restart.status()
    provider = next(
        item for item in status["providers"] if item["id"] == CHATGPT_PROVIDER_ID
    )
    assert provider["state"] == "signed_in"
    _assert_no_secrets(status)


@pytest.mark.asyncio
async def test_pending_device_login_does_not_survive_manager_restart(monkeypatch):
    _patch_device_start(monkeypatch)

    before_restart = AccountManager()
    started = await before_restart.login_start(CHATGPT_PROVIDER_ID, "device_code")
    after_restart = AccountManager()

    with pytest.raises(AccountStateInvalid) as excinfo:
        await after_restart.login_complete(started["login_id"])
    assert excinfo.value.data["reason"] == "unknown_login"


@pytest.mark.asyncio
async def test_device_flow_cancellation_mid_login(monkeypatch):
    _patch_device_start(monkeypatch)

    async def fake_poll(device_auth_id, user_code):  # pragma: no cover - unused
        raise AssertionError("cancelled login must never poll upstream")

    monkeypatch.setattr(codex, "device_auth_poll", fake_poll)

    manager = AccountManager()
    started = await manager.login_start(CHATGPT_PROVIDER_ID, "device_code")
    assert manager.login_cancel(started["login_id"]) is True
    # Cancel is idempotent and terminal.
    assert manager.login_cancel(started["login_id"]) is False
    with pytest.raises(AccountStateInvalid) as excinfo:
        await manager.login_complete(started["login_id"])
    assert excinfo.value.data["reason"] == "unknown_login"


@pytest.mark.asyncio
async def test_device_flow_upstream_rejection_is_auth_failed(monkeypatch):
    _patch_device_start(monkeypatch)

    async def fake_poll(device_auth_id, user_code):
        raise RuntimeError("Device authorization failed: 400")

    monkeypatch.setattr(codex, "device_auth_poll", fake_poll)
    manager = AccountManager()
    started = await manager.login_start(CHATGPT_PROVIDER_ID, "device_code")
    with pytest.raises(AccountAuthFailed) as excinfo:
        await manager.login_complete(started["login_id"])
    assert excinfo.value.code == -32042
    assert excinfo.value.data["provider"] == CHATGPT_PROVIDER_ID


@pytest.mark.asyncio
async def test_expired_login_is_pruned(monkeypatch):
    _patch_device_start(monkeypatch)
    now = {"value": 1000.0}
    manager = AccountManager(clock=lambda: now["value"])
    started = await manager.login_start(CHATGPT_PROVIDER_ID, "device_code")
    now["value"] += 16 * 60  # past LOGIN_TTL_SECONDS
    with pytest.raises(AccountStateInvalid) as excinfo:
        await manager.login_complete(started["login_id"])
    assert excinfo.value.data["reason"] == "unknown_login"


# ---------------------------------------------------------------------------
# Browser (PKCE) flow — testable to the URL handoff point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_flow_url_handoff_and_completion(monkeypatch):
    exchanged = {}

    async def fake_exchange(code, *, code_verifier, redirect_uri):
        exchanged.update(
            {"code": code, "verifier": code_verifier, "redirect_uri": redirect_uri}
        )
        _sign_in_codex()
        return codex.load_tokens()

    monkeypatch.setattr(codex, "exchange_authorization_code", fake_exchange)

    manager = AccountManager()
    started = await manager.login_start(CHATGPT_PROVIDER_ID, "browser")
    assert started["state"] == "pending"
    auth_url = started["auth_url"]
    assert auth_url.startswith("https://auth.openai.com/oauth/authorize?")
    assert "code_challenge=" in auth_url
    assert "code_challenge_method=S256" in auth_url
    # The PKCE verifier never leaves the server.
    login = manager._pending[started["login_id"]]
    assert login.code_verifier not in auth_url
    _assert_no_secrets(started)
    assert login.code_verifier not in json.dumps(started)

    done = await manager.login_complete(started["login_id"], code="auth-code-1")
    assert done["state"] == "completed"
    assert exchanged["code"] == "auth-code-1"
    assert exchanged["verifier"] == login.code_verifier
    assert exchanged["redirect_uri"] == codex.DEFAULT_BROWSER_REDIRECT_URI


@pytest.mark.asyncio
async def test_browser_flow_accepts_full_redirect_url_and_checks_state(monkeypatch):
    async def fake_exchange(code, *, code_verifier, redirect_uri):
        assert code == "the-code"
        _sign_in_codex()
        return codex.load_tokens()

    monkeypatch.setattr(codex, "exchange_authorization_code", fake_exchange)
    manager = AccountManager()
    started = await manager.login_start(CHATGPT_PROVIDER_ID, "browser")
    state = manager._pending[started["login_id"]].state

    with pytest.raises(AccountStateInvalid) as excinfo:
        await manager.login_complete(
            started["login_id"],
            code="http://localhost:1455/auth/callback?code=x&state=forged",
        )
    assert excinfo.value.data["reason"] == "state_mismatch"

    done = await manager.login_complete(
        started["login_id"],
        code=f"http://localhost:1455/auth/callback?code=the-code&state={state}",
    )
    assert done["state"] == "completed"


@pytest.mark.asyncio
async def test_browser_flow_requires_code():
    manager = AccountManager()
    started = await manager.login_start(CHATGPT_PROVIDER_ID, "browser")
    with pytest.raises(AccountStateInvalid) as excinfo:
        await manager.login_complete(started["login_id"])
    assert excinfo.value.data["reason"] == "code_required"


# ---------------------------------------------------------------------------
# Logout / refresh (OAuth)
# ---------------------------------------------------------------------------


def test_oauth_logout_clears_tokens():
    _sign_in_codex()
    manager = AccountManager()
    result = manager.logout(CHATGPT_PROVIDER_ID)
    assert result["account"]["state"] == "signed_out"
    assert codex.load_tokens() is None
    _assert_no_secrets(result)


def test_oauth_logout_does_not_resurrect_after_manager_restart(monkeypatch):
    _sign_in_codex()
    before_restart = AccountManager()
    assert before_restart.logout(CHATGPT_PROVIDER_ID)["ok"] is True

    after_restart = AccountManager()
    snapshot = after_restart.provider_snapshot(CHATGPT_PROVIDER_ID)
    assert snapshot["state"] == "signed_out"
    assert codex.load_tokens() is None
    _assert_no_secrets(after_restart.status())


@pytest.mark.asyncio
async def test_refresh_success_returns_redacted_snapshot(monkeypatch):
    _sign_in_codex()

    async def fake_refresh():
        return codex.load_tokens()

    monkeypatch.setattr(codex, "refresh_now_async", fake_refresh)
    result = await AccountManager().refresh(CHATGPT_PROVIDER_ID)
    assert result["ok"] is True
    assert result["account"]["state"] == "signed_in"
    _assert_no_secrets(result)


@pytest.mark.asyncio
async def test_refresh_failure_is_typed_with_upstream_status(monkeypatch):
    _sign_in_codex()

    async def fake_refresh():
        codex._stamp_auth_error(401)
        raise RuntimeError("Codex token refresh failed: 401")

    monkeypatch.setattr(codex, "refresh_now_async", fake_refresh)
    manager = AccountManager()
    with pytest.raises(AccountAuthFailed) as excinfo:
        await manager.refresh(CHATGPT_PROVIDER_ID)
    assert excinfo.value.code == -32042
    assert excinfo.value.data["status"] == 401
    # The broken credential is visible (and typed) in status afterwards.
    snapshot = manager.provider_snapshot(CHATGPT_PROVIDER_ID)
    assert snapshot["state"] == "error"
    assert snapshot["error"]["status"] == 401
    _assert_no_secrets(snapshot)


@pytest.mark.asyncio
async def test_refresh_signed_out_is_state_error():
    with pytest.raises(AccountStateInvalid) as excinfo:
        await AccountManager().refresh(CHATGPT_PROVIDER_ID)
    assert excinfo.value.data["reason"] == "not_signed_in"


# ---------------------------------------------------------------------------
# Usage / rate limits
# ---------------------------------------------------------------------------


def test_usage_reflects_live_rate_limit_headers_without_restart():
    _sign_in_codex()
    manager = AccountManager()

    first = manager.usage(CHATGPT_PROVIDER_ID)
    entry = first["providers"][0]
    assert entry["plan"] == "plus"
    assert entry["rate_limits"] == []

    codex.record_rate_limit_headers(
        {
            "x-codex-primary-used-percent": "42.5",
            "x-codex-primary-window-minutes": "300",
            "x-codex-primary-reset-after-seconds": "1200",
            "x-codex-secondary-used-percent": "7",
        }
    )
    second = manager.usage(CHATGPT_PROVIDER_ID)
    limits = {item["name"]: item for item in second["providers"][0]["rate_limits"]}
    assert limits["primary"]["used_percent"] == 42.5
    assert limits["primary"]["window_minutes"] == 300
    assert limits["primary"]["resets_at"] > time.time()
    assert limits["secondary"]["used_percent"] == 7.0
    assert second["providers"][0]["captured_at"] is not None
    assert second["providers"][0]["notices"] == []
    _assert_no_secrets(second)


def test_usage_signed_out_is_state_error():
    with pytest.raises(AccountStateInvalid) as excinfo:
        AccountManager().usage(CHATGPT_PROVIDER_ID)
    assert excinfo.value.data["reason"] == "not_signed_in"


def test_bulk_usage_skips_unavailable_providers():
    assert AccountManager().usage() == {"providers": []}
    _sign_in_codex()
    entries = AccountManager().usage()["providers"]
    assert [entry["id"] for entry in entries] == [CHATGPT_PROVIDER_ID]


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_select_provider_round_trip():
    manager = AccountManager()
    result = manager.select(CHATGPT_PROVIDER_ID)
    assert result["selected_provider"] == CHATGPT_PROVIDER_ID
    assert result["account"]["selected"] is True
    assert manager.status()["selected_provider"] == CHATGPT_PROVIDER_ID
    with pytest.raises(AccountStateInvalid):
        manager.select("nonsense")


# ---------------------------------------------------------------------------
# Redaction — every read path against a store full of secrets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_secret_material_in_any_account_payload(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", API_KEY_SECRET)
    _sign_in_codex()
    manager = AccountManager()
    _assert_no_secrets(manager.status())
    _assert_no_secrets(manager.usage())
    _assert_no_secrets(manager.select(CHATGPT_PROVIDER_ID))
    for provider in ("openai", "anthropic", CHATGPT_PROVIDER_ID):
        _assert_no_secrets(manager.provider_snapshot(provider))
