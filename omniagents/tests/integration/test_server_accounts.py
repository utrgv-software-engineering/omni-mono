"""End-to-end ``account_*`` RPC tests over the real /ws surface.

Exercises the acceptance criteria of the unified account surface:

* login/logout completable through stable RPCs (API-key without a
  browser; device-code with cancellation mid-login),
* ``account_changed`` broadcast to every connected client,
* usage updating live from captured rate-limit headers (no restart),
* typed capability/state errors on the wire,
* authorization: reads allowed for any principal, credential mutations
  refused for individual principals in enforced multi-user mode,
* secrets never appearing in any frame either socket receives nor in
  server debug logging (the PR #294 token-leakage house pattern).
"""

from __future__ import annotations

import json

import pytest
from agents import Agent
from fastapi.testclient import TestClient

import omniagents.core.providers.codex as codex
from omniagents.backends.server import app as server_app
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.debug import Debug
from omniagents.core.providers.accounts import (
    CHATGPT_PROVIDER_ID,
    CODEX_STORE_DURABILITY_ENV,
)
from omniagents.core.providers.auth.base import UserIdentity
from omniagents.execution import AgentHostResources
from omniagents.rpc.auth import AuthorizationDenied, AuthorizationPolicy
from tests.fixtures.gui_ws import perform_gui_handshake

API_KEY_SECRET = "sk-INTEGRATION-SECRET-000111"
REFRESH_SECRET = "rt-INTEGRATION-SECRET-222333"


async def dummy_build_agent(settings, mcp, app):
    return Agent(name="acct", tools=[], instructions="", model="gpt")


def _spec() -> AgentSpec:
    return AgentSpec(
        name="AccountAgent",
        welcome_text="hi",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    codex.set_token_store_dir(tmp_path)
    monkeypatch.delenv(CODEX_STORE_DURABILITY_ENV, raising=False)
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "AZURE_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    codex._rate_limit_snapshot.clear()
    yield
    codex._token_store_dir = None
    codex._rate_limit_snapshot.clear()


@pytest.fixture
def client():
    # No auth token: the single-user (anonymous) topology in which
    # credential mutations are permitted. Entered as a context manager so
    # every websocket shares one event loop, as connections do in a real
    # server; separate portals made cross-client broadcasts hang at random.
    with TestClient(
        server_app.build_app(spec=_spec(), default_host_workspace_root="/tmp")
    ) as test_client:
        yield test_client


def _call(ws, call_id, method, params=None):
    ws.send_json(
        {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params or {}}
    )


def _result(ws, call_id):
    frames = []
    while True:
        message = ws.receive_json()
        frames.append(message)
        if message.get("id") == call_id:
            return message, frames


def _wait_for_method(ws, method):
    frames = []
    while True:
        message = ws.receive_json()
        frames.append(message)
        if message.get("method") == method:
            return message, frames


def test_account_status_over_ws(client):
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws)
        _call(ws, 1, "account_status")
        response, _ = _result(ws, 1)
        providers = {p["id"]: p for p in response["result"]["providers"]}
        assert providers["openai-chatgpt"]["capabilities"]["login_modes"] == [
            "device_code",
            "browser",
        ]
        assert providers["openai"]["state"] == "signed_out"


def test_api_key_login_broadcasts_to_all_clients_without_leaking(client, capsys):
    """Stable-RPC login; account_changed reaches a second client; no leak."""
    Debug.set_enabled(True)
    Debug._log_file = None  # route Debug.log to stderr → captured by capsys
    try:
        with (
            client.websocket_connect("/ws") as ws_a,
            client.websocket_connect("/ws") as ws_b,
        ):
            perform_gui_handshake(ws_a)
            perform_gui_handshake(ws_b)

            _call(
                ws_a,
                1,
                "account_login_start",
                {"provider": "openai", "mode": "api_key", "api_key": API_KEY_SECRET},
            )
            response, frames_a = _result(ws_a, 1)
            assert response["result"]["state"] == "completed"
            assert response["result"]["account"]["source"] == "rpc"

            changed, frames_b = _wait_for_method(ws_b, "account_changed")
            assert changed["params"]["provider"] == "openai"
            assert changed["params"]["reason"] == "login"
            assert changed["params"]["account"]["state"] == "signed_in"

            # Logout also lands on the other client.
            _call(ws_a, 2, "account_logout", {"provider": "openai"})
            logout, more_a = _result(ws_a, 2)
            assert logout["result"]["ok"] is True
            changed2, more_b = _wait_for_method(ws_b, "account_changed")
            assert changed2["params"]["reason"] == "logout"

            # The key the client sent never appears in ANY frame either
            # socket received, in either direction of the transcript.
            for frame in frames_a + frames_b + more_a + more_b:
                assert API_KEY_SECRET not in json.dumps(frame)
    finally:
        Debug.set_enabled(False)

    # ...nor in anything the server wrote to stdout/stderr, including the
    # debug logging of the inbound account_login_start params.
    captured = capsys.readouterr()
    assert "account_login_start" in captured.err  # debug logging was live
    assert API_KEY_SECRET not in captured.out
    assert API_KEY_SECRET not in captured.err


def test_device_login_cancel_mid_flow_over_ws(client, monkeypatch):
    async def fake_start():
        return {
            "device_auth_id": "dev-9",
            "user_code": "WXYZ-9999",
            "interval": 1,
            "verification_url": codex.DEVICE_VERIFICATION_URL,
        }

    async def fake_poll(device_auth_id, user_code):  # pragma: no cover - guarded
        raise AssertionError("cancelled login must not poll upstream")

    monkeypatch.setattr(codex, "device_auth_start", fake_start)
    monkeypatch.setattr(codex, "device_auth_poll", fake_poll)

    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws)
        _call(
            ws,
            1,
            "account_login_start",
            {"provider": "openai-chatgpt", "mode": "device_code"},
        )
        started, _ = _result(ws, 1)
        assert started["result"]["state"] == "pending"
        assert started["result"]["user_code"] == "WXYZ-9999"
        login_id = started["result"]["login_id"]

        _call(ws, 2, "account_login_cancel", {"login_id": login_id})
        cancelled, _ = _result(ws, 2)
        assert cancelled["result"] is True

        _call(ws, 3, "account_login_complete", {"login_id": login_id})
        completed, _ = _result(ws, 3)
        assert completed["error"]["code"] == -32041
        assert completed["error"]["data"]["kind"] == "account_state_invalid"
        assert completed["error"]["data"]["reason"] == "unknown_login"


def test_refresh_failure_and_capability_errors_are_typed_on_the_wire(
    client, monkeypatch
):
    codex.save_tokens({"refresh": REFRESH_SECRET, "access": "a", "expires": 1})

    async def fake_refresh():
        codex._stamp_auth_error(401)
        raise RuntimeError("Codex token refresh failed: 401")

    monkeypatch.setattr(codex, "refresh_now_async", fake_refresh)

    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws)
        _call(ws, 1, "account_refresh", {"provider": "openai-chatgpt"})
        refresh, frames = _result(ws, 1)
        assert refresh["error"]["code"] == -32042
        assert refresh["error"]["data"]["kind"] == "account_auth_failed"
        assert refresh["error"]["data"]["status"] == 401
        for frame in frames:
            assert REFRESH_SECRET not in json.dumps(frame)

        # Unsupported operation → typed capability error, not -32603.
        _call(ws, 2, "account_refresh", {"provider": "anthropic"})
        capability, _ = _result(ws, 2)
        assert capability["error"]["code"] == -32040
        assert capability["error"]["data"]["kind"] == "account_capability_unsupported"
        assert capability["error"]["data"]["supported"] == ["openai-chatgpt"]


def test_usage_updates_live_without_restart(client):
    codex.save_tokens(
        {"refresh": REFRESH_SECRET, "access": "a", "expires": 1, "account_id": "acct"}
    )
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws)
        _call(ws, 1, "account_usage", {"provider": "openai-chatgpt"})
        first, _ = _result(ws, 1)
        assert first["result"]["providers"][0]["rate_limits"] == []

        # Live model traffic stamps new rate-limit headers…
        codex.record_rate_limit_headers(
            {
                "x-codex-primary-used-percent": "55",
                "x-codex-primary-reset-after-seconds": "600",
            }
        )
        # …and the same connection sees them on the next read.
        _call(ws, 2, "account_usage", {"provider": "openai-chatgpt"})
        second, frames = _result(ws, 2)
        limits = second["result"]["providers"][0]["rate_limits"]
        assert limits[0]["name"] == "primary"
        assert limits[0]["used_percent"] == 55.0
        assert limits[0]["resets_at"] is not None
        for frame in frames:
            assert REFRESH_SECRET not in json.dumps(frame)


def test_enforced_mode_refuses_credential_mutations_but_allows_reads(monkeypatch):
    app = server_app.build_app(
        spec=_spec(), auth_token="secret", default_host_workspace_root="/tmp"
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    with client.websocket_connect("/ws", headers=headers) as ws:
        perform_gui_handshake(ws)
        _call(ws, 1, "account_status")
        status, _ = _result(ws, 1)
        assert "result" in status

        _call(
            ws,
            2,
            "account_login_start",
            {"provider": "openai", "mode": "api_key", "api_key": "sk-x"},
        )
        denied, _ = _result(ws, 2)
        assert denied["error"]["code"] == -32014
        assert denied["error"]["data"]["kind"] == "authorization_denied"

        _call(ws, 3, "account_logout", {"provider": "openai"})
        denied_logout, _ = _result(ws, 3)
        assert denied_logout["error"]["code"] == -32014


@pytest.mark.asyncio
async def test_admin_can_mutate_accounts_but_ordinary_principals_cannot():
    service = AgentService(
        _spec(), AgentHostResources.create("test-server-accounts-host")
    )
    service._authorization_policy = AuthorizationPolicy(enforced=True)

    await service.authorize_rpc(UserIdentity(user_id="alice"), "account_status", {})
    with pytest.raises(AuthorizationDenied):
        await service.authorize_rpc(
            UserIdentity(user_id="alice"),
            "account_logout",
            {"provider": CHATGPT_PROVIDER_ID},
        )

    await service.authorize_rpc(
        UserIdentity(user_id="agent_host_control", roles=["admin"]),
        "account_logout",
        {"provider": CHATGPT_PROVIDER_ID},
    )


@pytest.mark.asyncio
async def test_account_status_rpc_exposes_only_redacted_durability_capability(
    monkeypatch,
):
    codex.save_tokens({"refresh": REFRESH_SECRET, "access": "access-secret"})
    monkeypatch.setenv(CODEX_STORE_DURABILITY_ENV, "host")
    service = AgentService(
        _spec(), AgentHostResources.create("test-server-account-capability-host")
    )

    result = await service.account_status()

    assert result["mutation_persistence"] == {
        "codex_oauth": {"durable": True, "scope": "host"},
        "pending_logins": {"durable": False, "scope": "process"},
    }
    serialized = json.dumps(result)
    assert REFRESH_SECRET not in serialized
    assert "access-secret" not in serialized
    assert "codex.json" not in serialized
