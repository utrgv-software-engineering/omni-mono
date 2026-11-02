"""Azure identity trust, login CSRF, and reuse of Workplace authorization."""

import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import Account, Accounts, SESSION_COOKIE
from omniagents_workplace.easy_auth import (
    EasyAuth,
    PREFIX,
    STATE_COOKIE,
    TENANT,
    OBJECT,
)
from omniagents_workplace.installation import FactoryAccounts
from omniagents_workplace.server import SignedIn
from omniagents_workplace.store import Store

TID = "11111111-1111-1111-1111-111111111111"
OID = "22222222-2222-2222-2222-222222222222"
BASE = "https://workplace.example"


def config(**overrides):
    return {
        "WORKPLACE_AZURE_EASY_AUTH": "true",
        "WORKPLACE_AZURE_TENANT_ID": TID,
        "WORKPLACE_AZURE_ACCOUNT_MAP": json.dumps({OID: "alice"}),
        **overrides,
    }


def principal(tenant=TID, oid=OID, provider="aad", extra=()):
    return {
        "x-ms-client-principal": base64.b64encode(
            json.dumps(
                {
                    "auth_typ": provider,
                    "claims": [
                        {"typ": TENANT, "val": tenant},
                        {"typ": OBJECT, "val": oid},
                        *extra,
                    ],
                }
            ).encode()
        ).decode()
    }


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "accounts.db")
    accounts = Accounts(store)
    accounts.add(Account("alice", "alice", "Alice", "alice@example.test", "", "", ""))
    app = FastAPI()
    app.add_middleware(SignedIn, accounts=accounts, base_url=BASE)
    EasyAuth(BASE, config()).install(app, accounts)
    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        yield client, accounts
    store.close()


def begin(client):
    response = client.get(PREFIX + "/login")
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    return parse_qs(urlsplit(response.headers["location"]).query)[
        "post_login_redirect_uri"
    ][0]


def test_exchange_rotates_session_and_rejects_replay(setup):
    client, accounts = setup
    old = accounts.start_session(accounts.accounts["alice"])
    client.cookies.set(SESSION_COOKIE, old)
    callback = begin(client)
    state_cookie = client.cookies.get(STATE_COOKIE)
    response = client.get(callback, headers=principal())
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    token = response.cookies[SESSION_COOKIE]
    assert accounts.signed_in(token).id == "alice"
    assert accounts.signed_in(old) is None
    assert "Secure" in response.headers["set-cookie"]
    client.cookies.set(STATE_COOKIE, state_cookie)
    assert client.get(callback, headers=principal()).status_code == 403

    # Ordinary factory membership resolution applies to this same SSO session.
    class Installation:
        def membership(self, factory, account):
            return {"local_id": "alice"} if factory == "allowed" else None

    installation = Installation()
    installation.accounts = accounts
    assert (
        FactoryAccounts(installation, "allowed", accounts).signed_in(token).id
        == "alice"
    )
    assert FactoryAccounts(installation, "other", accounts).signed_in(token) is None
    accounts.update(accounts.accounts["alice"], disabled=True)
    assert accounts.signed_in(token) is None


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-ms-client-principal-name": "alice@example.test"},
        {"x-ms-client-principal": "invalid"},
        principal(tenant=OID),
        principal(oid=TID),
        principal(provider="google"),
        principal(extra=[{"typ": "tid", "val": OID}]),
    ],
)
def test_invalid_identity_cannot_start_session(setup, headers):
    client, accounts = setup
    assert client.get(begin(client), headers=headers).status_code == 403
    assert not accounts._sessions


def test_disabled_account_and_missing_state(setup):
    client, accounts = setup
    assert client.get(PREFIX + "/callback", headers=principal()).status_code == 403
    accounts.update(accounts.accounts["alice"], disabled=True)
    assert client.get(begin(client), headers=principal()).status_code == 403
    assert not accounts._sessions


def test_state_bound_to_browser_and_expires(setup):
    client, accounts = setup
    callback = begin(client)
    with TestClient(client.app, base_url=BASE) as stranger:
        assert stranger.get(callback, headers=principal()).status_code == 403
    for key, _ in accounts.store.docs("account.azure_state"):
        accounts.store.put("account.azure_state", key, {"expires": 0})
    assert client.get(callback, headers=principal()).status_code == 403


def test_disabled_ignores_headers_and_azure_environment(setup):
    _, accounts = setup
    app = FastAPI()
    auth = EasyAuth("http://localhost", {"WEBSITE_HOSTNAME": "app.azurewebsites.net"})
    auth.install(app, accounts)
    assert auth.metadata() is None
    with TestClient(app) as client:
        assert client.get(PREFIX + "/callback", headers=principal()).status_code == 404
    assert not accounts._sessions


@pytest.mark.parametrize(
    "env,base",
    [
        (config(), "http://workplace.example"),
        (config(WORKPLACE_AZURE_EASY_AUTH="yes"), BASE),
        (config(WORKPLACE_AZURE_TENANT_ID=""), BASE),
        (config(WORKPLACE_AZURE_ACCOUNT_MAP="[]"), BASE),
        (config(WORKPLACE_AZURE_ACCOUNT_MAP='{"not-a-uuid":"alice"}'), BASE),
    ],
)
def test_bad_configuration_fails_closed(env, base):
    with pytest.raises(ValueError):
        EasyAuth(base, env)


@pytest.mark.parametrize("multi_factory", [False, True])
def test_real_app_wiring(tmp_path, monkeypatch, multi_factory):
    from omniagents_workplace.installation import Installation
    from omniagents_workplace.installation_server import create_installation_app
    from omniagents_workplace.server import Workplace, create_app

    for key, value in config().items():
        monkeypatch.setenv(key, value)
    host = (
        Installation(tmp_path / "host", base_url=BASE)
        if multi_factory
        else Workplace(tmp_path / "host", base_url=BASE)
    )
    host.accounts.add(Account("alice", "alice", "Alice", "a@example.test", "", "", ""))
    app = create_installation_app(host) if multi_factory else create_app(host)
    client = TestClient(app, base_url=BASE, follow_redirects=False)
    try:
        session = client.get("/workplace/api/session").json()
        assert session["azure_auth"]["login_url"] == PREFIX + "/login"
        assert "ACCOUNT_MAP" not in json.dumps(session)
        assert client.get(begin(client), headers=principal()).status_code == 303
        session = client.get("/workplace/api/session").json()
        assert session["account"]["id"] == "alice"
        token = client.cookies.get(SESSION_COOKIE)
        assert client.post("/workplace/api/signout").status_code == 200
        assert host.accounts.signed_in(token) is None
        assert client.get("/healthz").status_code == 200
    finally:
        client.close()
        host.store.close()


def test_malformed_callback_state_is_rejected(setup):
    client, accounts = setup
    begin(client)
    response = client.get(
        PREFIX + "/callback", params={"state": "é"}, headers=principal()
    )
    assert response.status_code == 403
    assert not accounts._sessions
