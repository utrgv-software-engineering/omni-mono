"""Security regressions at the account and browser boundaries."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import (
    Accounts,
    Account,
    AccountError,
    SESSION_COOKIE,
)
from omniagents_workplace.factory_runtime import allowed, trusted_origin
from omniagents_workplace.memory.state import AdminPrincipal, MemoryError, MemoryState
from omniagents_workplace.server import Workplace, create_app
from omniagents_workplace.store import Store


@pytest.fixture
def accounts(tmp_path):
    store = Store(tmp_path / "auth.db")
    accounts = Accounts(store)
    accounts.add(Account("a", "alice", "Alice", "a@example.test", "U1", "alice", "L1"))
    yield accounts
    store.close()


def test_password_change_revokes_sessions_and_invites(accounts):
    account = accounts.accounts["a"]
    accounts.set_password(account, "old-password")
    old = [accounts.start_session(account) for _ in range(2)]
    invite = accounts.invite(account)
    with pytest.raises(AccountError):
        accounts.change_password(account, "incorrect", "new-password")
    assert all(accounts.signed_in(t) for t in old)
    replacement = accounts.change_password(account, "old-password", "new-password")
    assert all(accounts.signed_in(t) is None for t in old)
    assert accounts.invited(invite) is None
    assert accounts.signed_in(replacement) is account
    assert len(accounts._sessions) == 1
    # Revocation survives reopening the account repository.
    again = Accounts(accounts.store)
    assert all(again.signed_in(t) is None for t in old)
    assert again.signed_in(replacement).id == account.id


def test_invite_has_only_one_concurrent_winner(accounts):
    invite = accounts.invite(accounts.accounts["a"])
    barrier = threading.Barrier(8)

    def redeem(i):
        barrier.wait()
        try:
            return accounts.accept_invite(invite, f"password-{i}")
        except AccountError as exc:
            assert exc.status == 404
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [r for r in pool.map(redeem, range(8)) if r is not None]
    assert len(winners) == len(accounts._sessions) == 1


def test_disabled_account_cannot_issue_or_reuse_tokens(accounts):
    account = accounts.accounts["a"]
    token, invite = accounts.start_session(account), accounts.invite(account)
    accounts.update(account, disabled=True)
    with pytest.raises(AccountError):
        accounts.start_session(account)
    accounts.update(account, disabled=False)
    assert accounts.signed_in(token) is None
    assert accounts.invited(invite) is None


@pytest.fixture
def browser(tmp_path):
    workplace = Workplace(tmp_path / "workplace", base_url="https://workplace.test")
    account = workplace.add_person(
        username="alice", name="Alice", email="alice@example.test", admin=True
    )
    workplace.accounts.set_password(account, "old-password")
    with TestClient(
        create_app(workplace, static_dir=tmp_path), base_url=workplace.base_url
    ) as client:
        client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(account))
        yield workplace, account, client
    workplace.close()


def test_password_endpoint_rotates_secure_cookie(browser):
    workplace, account, client = browser
    old = next(iter(workplace.accounts._sessions))
    result = client.post(
        "/workplace/api/me/password",
        json={"current": "old-password", "new": "new-password"},
    )
    assert result.status_code == 200
    assert "httponly" in result.headers["set-cookie"].lower()
    assert "secure" in result.headers["set-cookie"].lower()
    assert old not in workplace.accounts._sessions
    assert len(workplace.accounts._sessions) == 1
    assert client.get("/workplace/api/session").json()["account"]["id"] == account.id


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.test",
        "https://sibling.workplace.test",
        "http://workplace.test",
        "null",
    ],
)
@pytest.mark.parametrize(
    "body", [{}, {"json": {}}, {"files": {"file": ("x.txt", b"x")}}]
)
def test_csrf_rejects_all_body_types(browser, origin, body):
    workplace, account, client = browser
    result = client.post("/workplace/api/signout", headers={"Origin": origin}, **body)
    assert result.status_code == 403
    assert client.get("/workplace/api/session").json()["account"]["id"] == account.id


def test_same_origin_and_fetch_metadata(browser):
    _, _, client = browser
    assert (
        client.post(
            "/workplace/api/signout", headers={"Sec-Fetch-Site": "same-site"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/workplace/api/signout", headers={"Origin": "https://workplace.test"}
        ).status_code
        == 200
    )


def test_memory_admin_access_is_not_persisted_or_inherited_by_tokens(browser):
    workplace, account, client = browser
    memory = workplace.memory
    other = memory.provision("other", "personal")
    item = memory.remember("other", other, "private memory")
    assert other in {s["id"] for s in client.get("/workplace/api/memory/stores").json()}
    with pytest.raises(MemoryError):
        memory.get(
            account.id, item["id"]
        )  # Personal MCP principal does not gain UI admin authority.
    workplace.accounts.update(account, admin=False)
    client = browser[2]
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(account))
    assert other not in {
        s["id"] for s in client.get("/workplace/api/memory/stores").json()
    }
    assert (
        client.post("/workplace/api/memory/get", json={"id": item["id"]}).status_code
        == 403
    )


def test_legacy_admin_grant_migration_fails_closed(accounts):
    state = MemoryState(accounts.store)
    own = state.provision("a", "personal")
    other = state.provision("other", "personal")
    state.grant("a", other, "write")
    state.remove_legacy_admin_grants({"a"})
    assert {s["id"] for s in state.stores("a")} == {own, "team"}
    state.grant("a", other, "read")
    state.remove_legacy_admin_grants({"a"})
    assert other in {
        s["id"] for s in state.stores("a")
    }  # Explicit new grants survive restart.


def test_runtime_is_fail_closed():
    for method in [
        "get_config",
        "get_future_secret",
        "read_file",
        "$/anything",
        "shutdown",
    ]:
        assert not allowed(method)
    assert allowed("get_thread")
    assert trusted_origin("https://workplace.test:443", "https://workplace.test")
    for origin in [
        "http://workplace.test",
        "https://workplace.test.evil",
        "https://user@workplace.test",
        "null",
    ]:
        assert not trusted_origin(origin, "https://workplace.test")


def test_runtime_get_cannot_escape_to_other_factory_endpoints(browser, tmp_path):
    workplace, _, client = browser
    from types import SimpleNamespace

    workplace.factory_config = SimpleNamespace(
        root=tmp_path,
        load=lambda: {
            "factory": {"url": "http://127.0.0.1:1", "auth_token": "test-token"}
        },
    )
    # A configured upstream would fail the request if the proxy tried to reach it.
    for path in ["get_config", "files/secret", "%2e%2e/%2e%2e/integrations/config"]:
        result = client.get("/workplace/api/factory/runtime/agents/foreman/" + path)
        assert result.status_code in (403, 404)


def test_failure_throttle_is_atomic_and_persistent(accounts):
    account = accounts.accounts["a"]
    accounts.set_password(account, "old-password")

    def fail(_):
        try:
            accounts.sign_in("alice", "wrong-password")
        except AccountError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(fail, range(12)))
    assert results.count(401) == 10
    assert results.count(429) == 2
    with pytest.raises(AccountError) as error:
        Accounts(accounts.store).sign_in("alice", "old-password")
    assert error.value.status == 429


def test_unknown_usernames_cannot_grow_throttle_state(accounts):
    for i in range(30):
        with pytest.raises(AccountError):
            accounts.sign_in(f"nonexistent-{i}", "wrong-password")
    assert len(accounts._failures) == 1


def test_initial_setup_has_one_concurrent_winner(tmp_path):
    workplace = Workplace(tmp_path / "setup", base_url="http://testserver")
    barrier = threading.Barrier(4)

    def setup(i):
        barrier.wait()
        try:
            return workplace.setup(
                organization="Example",
                slug="example",
                username=f"admin{i}",
                name="Admin",
                email=f"admin{i}@example.test",
                password="new-password",
                tz="UTC",
            )
        except AccountError as exc:
            assert exc.status == 409

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert len([v for v in pool.map(setup, range(4)) if v]) == 1
        assert len(workplace.accounts.people()) == 1
    finally:
        workplace.close()


def test_wire_and_raw_transcripts_require_admin(browser):
    workplace, account, client = browser
    workplace.accounts.update(account, admin=False)
    client = browser[2]
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(account))
    for path in [
        "/workplace/api/wire",
        "/workplace/api/factory/runs/1",
        "/workplace/api/factory/runtime/agents/foreman/health",
    ]:
        assert client.get(path).status_code == 403
    assert client.delete("/workplace/api/wire").status_code == 403


def test_attachment_signature_requires_complete_match_and_html_is_sandboxed(
    browser, monkeypatch
):
    from omniagents_workplace.linear.state import Attachment

    workplace, _, client = browser
    attachment = Attachment(
        "attachment",
        "test.html",
        "/linear/uploads/attachment?signature=secret-full-signature",
        content=b"<script>alert(document.cookie)</script>",
        content_type="text/html",
    )
    monkeypatch.setattr(workplace.linear, "attachment", lambda _: attachment)
    client.cookies.clear()
    for signature in ["", "s", "secret-full", "secret-full-signature-extra"]:
        assert (
            client.get(
                "/linear/uploads/attachment", params={"signature": signature}
            ).status_code
            == 404
        )
    result = client.get("/linear/uploads/attachment?signature=secret-full-signature")
    assert result.status_code == 200
    assert result.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert result.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_event_stream_stops_when_session_is_revoked(browser, tmp_path):
    from starlette.requests import Request

    workplace, account, _ = browser
    app = create_app(workplace, static_dir=tmp_path)
    token = workplace.accounts.start_session(account)
    endpoint = next(
        r.endpoint
        for r in app.routes
        if getattr(r, "path", "") == "/workplace/api/stream"
    )
    request = Request(
        {
            "type": "http",
            "state": {"account": account},
            "headers": [(b"cookie", f"{SESSION_COOKIE}={token}".encode())],
        }
    )
    response = await endpoint(request)
    assert "ready" in await anext(response.body_iterator)
    workplace.accounts.sign_out(token)
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)


def test_browser_auth_responses_are_not_cached(browser):
    _, _, client = browser
    response = client.get("/workplace/api/session")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"


def test_legacy_passwords_are_upgraded_and_malformed_hashes_fail_closed(accounts):
    import hashlib
    from omniagents_workplace.accounts import _check_password

    account = accounts.accounts["a"]
    salt = b"0" * 16
    digest = hashlib.scrypt(b"old-password", salt=salt, n=2**14, r=8, p=1, dklen=32)
    account.password = f"scrypt${salt.hex()}${digest.hex()}"
    accounts.sign_in("alice", "old-password")
    assert account.password.startswith("scrypt$v2$")
    assert _check_password("old-password", account.password)
    assert not _check_password("wrong-password", account.password)
    for malformed in ["scrypt$bad", "scrypt$nothex$bad", "scrypt$v99$bad$bad"]:
        assert not _check_password("password", malformed)


def test_production_rejects_short_passwords_before_creating_admin(tmp_path):
    workplace = Workplace(
        tmp_path / "production",
        base_url="https://workplace.test",
        environment="production",
    )
    try:
        with pytest.raises(AccountError):
            workplace.setup(
                organization="Example",
                slug="example",
                username="alice",
                name="Alice",
                email="a@example.test",
                password="eight888",
                tz="UTC",
            )
        assert workplace.accounts.needs_setup()
    finally:
        workplace.close()


@pytest.mark.asyncio
async def test_event_stream_does_not_disclose_wire_to_non_admin(browser, tmp_path):
    import asyncio
    from starlette.requests import Request

    workplace, account, _ = browser
    workplace.accounts.update(account, admin=False)
    client = browser[2]
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(account))
    app = create_app(workplace, static_dir=tmp_path)
    token = workplace.accounts.start_session(account)
    endpoint = next(
        r.endpoint
        for r in app.routes
        if getattr(r, "path", "") == "/workplace/api/stream"
    )
    request = Request(
        {
            "type": "http",
            "state": {"account": account},
            "headers": [(b"cookie", f"{SESSION_COOKIE}={token}".encode())],
        }
    )
    response = await endpoint(request)
    await anext(response.body_iterator)
    workplace.wire.record(
        service="slack",
        direction="in",
        kind="mcp",
        title="private",
        response={"text": "private message"},
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(response.body_iterator), timeout=0.1)


def test_explicit_alternate_origins_allow_mutations_without_trusting_host(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "WORKPLACE_ALLOWED_ORIGINS", "http://127.0.0.1:7395,http://localhost:7395"
    )
    workplace = Workplace(tmp_path / "origins", base_url="https://workplace.test")
    account = workplace.add_person(
        username="alice", name="Alice", email="alice@example.test", admin=True
    )
    try:
        with TestClient(create_app(workplace, static_dir=tmp_path)) as client:
            for origin in [
                "https://evil.test",
                "http://127.0.0.1:7396",
                "https://localhost:7395",
            ]:
                client.cookies.set(
                    SESSION_COOKIE, workplace.accounts.start_session(account)
                )
                assert (
                    client.post(
                        "/workplace/api/signout",
                        headers={"Origin": origin, "Host": origin.split("://")[1]},
                    ).status_code
                    == 403
                )
            for origin in [
                "http://127.0.0.1:7395",
                "http://localhost:7395",
                "https://workplace.test",
            ]:
                client.cookies.set(
                    SESSION_COOKIE, workplace.accounts.start_session(account)
                )
                assert (
                    client.post(
                        "/workplace/api/signout", headers={"Origin": origin}
                    ).status_code
                    == 200
                )
    finally:
        workplace.close()


def test_local_origins_are_not_implicitly_trusted():
    assert not trusted_origin("http://127.0.0.1:7395", "https://workplace.test")
    assert not trusted_origin("http://localhost:7395", "https://workplace.test")
