import json
import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from omniagents_workplace.security import RequestLimits
from omniagents_workplace.accounts import Accounts, AccountError, SESSION_COOKIE
from test_auth_security import accounts, browser


def test_role_changes_revoke_sessions(accounts):
    person = accounts.accounts["a"]
    old = accounts.start_session(person)
    accounts.update(person, admin=True)
    assert accounts.signed_in(old) is None
    old = accounts.start_session(person)
    accounts.update(person, admin=False)
    assert accounts.signed_in(old) is None


def test_disabled_tokens_do_not_return_on_reenable(browser):
    workplace, person, _ = browser
    services = ["slack", "github", "linear", "canvas", "memory"]
    for service in services:
        workplace.make_personal_token(person, service)
    assert all(workplace.personal_tokens(person).values())
    workplace.update_person(person, disabled=True)
    with pytest.raises(AccountError):
        workplace.make_personal_token(person, "memory")
    workplace.update_person(person, disabled=False)
    assert not any(workplace.personal_tokens(person).values())


def test_shorter_session_policy_applies_to_existing_sessions(accounts, monkeypatch):
    person = accounts.accounts["a"]
    token = accounts.start_session(person)
    now = next(iter(accounts._sessions.values())).created_at
    reopened = Accounts(accounts.store, session_seconds=300)
    monkeypatch.setattr("omniagents_workplace.accounts.time.time", lambda: now + 300)
    assert reopened.signed_in(token) is None


def test_security_logs_exclude_credentials(accounts, caplog):
    person = accounts.accounts["a"]
    accounts.set_password(person, "secret-password")
    _, token = accounts.sign_in("alice", "secret-password")
    accounts.sign_out(token)
    assert "auth.signed_in" in caplog.text
    assert "auth.signed_out" in caplog.text
    assert "secret-password" not in caplog.text and token not in caplog.text


def limits_app():
    app = FastAPI()
    app.add_middleware(
        RequestLimits, auth_bytes=32, body_bytes=64, websocket_bytes=16, attempts=2
    )

    @app.post("/workplace/api/signin")
    @app.post("/upload")
    async def body(request: Request):
        return {"size": len(await request.body())}

    @app.websocket("/ws")
    async def echo(ws: WebSocket):
        await ws.accept()
        await ws.send_text(await ws.receive_text())

    return app


def test_size_limits_and_source_throttle():
    with TestClient(limits_app()) as client:
        assert client.post("/upload", content=b"x" * 65).status_code == 413
        assert client.post("/upload", content=b"x" * 64).status_code == 200
        assert (
            client.post("/workplace/api/signin", content=b"x" * 33).status_code == 413
        )
        assert client.post("/workplace/api/signin", content=b"ok").status_code == 200
        # Forging forwarded headers cannot change the ASGI client address.
        result = client.post(
            "/workplace/api/signin",
            content=b"ok",
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert result.status_code == 429 and result.headers["retry-after"] == "60"
        with client.websocket_connect("/ws") as ws:
            ws.send_text("x" * 17)
            with pytest.raises(WebSocketDisconnect) as error:
                ws.receive_text()
            assert error.value.code == 1009


@pytest.mark.asyncio
async def test_chunked_body_limit_before_handler():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    messages = iter(
        [
            {"type": "http.request", "body": b"a" * 40, "more_body": True},
            {"type": "http.request", "body": b"b" * 40, "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    output = []

    async def send(message):
        output.append(message)

    await RequestLimits(app, body_bytes=64)(
        {"type": "http", "method": "POST", "path": "/upload", "headers": []},
        receive,
        send,
    )
    assert output[0]["status"] == 413
    assert not called


def test_server_lock_prevents_multiple_writers(tmp_path):
    from omniagents_workplace.security import lock_data_directory

    path = tmp_path / "private"
    first = lock_data_directory(path)
    try:
        with pytest.raises(RuntimeError, match="Another workplace server"):
            lock_data_directory(path)
        assert path.stat().st_mode & 0o777 == 0o700
    finally:
        first.close()
    lock_data_directory(path).close()


def test_production_policy_rejects_legacy_short_password(accounts):
    person = accounts.accounts["a"]
    accounts.set_password(person, "old-password")
    strict = Accounts(accounts.store, min_password=15)
    with pytest.raises(AccountError) as error:
        strict.sign_in(person.username, "old-password")
    assert error.value.status == 403
    assert not strict._sessions
    invite = strict.invite(strict.accounts[person.id])
    _, token = strict.accept_invite(invite, "a new long passphrase")
    assert strict.signed_in(token).id == person.id
