"""The deployment policy, the audit log, strict sessions, access reviews and notifications."""

import json
import sqlite3

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import (
    Account,
    Accounts,
    AccountError,
    SESSION_COOKIE,
)
from omniagents_workplace.audit import AuditLog
from omniagents_workplace.easy_auth import EasyAuth
from omniagents_workplace.notifications import Notifier
from omniagents_workplace.policy import Policy, STRICT_IDLE_SECONDS
from omniagents_workplace.server import SignedIn
from omniagents_workplace.store import Store
from test_easy_auth import BASE, OID, TID, config, principal
from test_installation import installation, prefix  # noqa: F401 - fixture


def test_policy_is_open_by_default_and_strict_on_request(tmp_path):
    assert not Policy({}).strict and Policy({}).session_limits() == {}
    with pytest.raises(ValueError):
        Policy({"WORKPLACE_POLICY": "lenient"})
    strict = Policy({"WORKPLACE_POLICY": "strict"})
    assert strict.session_limits() == {
        "session_seconds": 12 * 3600,
        "idle_seconds": STRICT_IDLE_SECONDS,
    }
    env = strict.runtime_environment(tmp_path)
    overlay = yaml.safe_load(open(env["OMNIAGENTS_PROJECT_OVERLAYS"]))
    assert overlay["security"]["audit"] == {
        "enabled": True,
        "backend": "file",
        "max_detail_chars": 0,
    }
    assert Policy({}).runtime_environment(tmp_path) == {}


def test_audit_events_are_kept_and_never_changed(tmp_path):
    store = Store(tmp_path / "audit.db")
    log = AuditLog(store, factory="f1")
    log.record("factory.profile_changed", actor="eric", reason="drill", ignored=None)
    with store.transaction():
        log.record("factory.resumed", actor="eric", reason="fixed")
    events = log.events()
    assert [(e["event"], e["actor"], e["factory"], e["reason"]) for e in events] == [
        ("factory.profile_changed", "eric", "f1", "drill"),
        ("factory.resumed", "eric", "f1", "fixed"),
    ]
    assert "ignored" not in events[0]
    assert log.events(event="factory.resumed")[0]["reason"] == "fixed"
    for statement in ("UPDATE audit_events SET actor='x'", "DELETE FROM audit_events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._db.execute(statement)
    store.close()
    reopened = Store(tmp_path / "audit.db")
    assert len(AuditLog(reopened).events()) == 2
    reopened.close()


def test_account_events_go_to_the_audit_log(tmp_path):
    store = Store(tmp_path / "accounts.db")
    accounts = Accounts(store)
    alice = accounts.add(
        Account("alice", "alice", "Alice", "a@example.test", "", "", "")
    )
    accounts.set_password(alice, "correct horse battery")
    accounts.sign_in("alice", "correct horse battery")
    with pytest.raises(AccountError):
        accounts.sign_in("alice", "wrong password!")
    assert [e["event"] for e in accounts.audit_log.events()] == [
        "password.changed",
        "auth.signed_in",
        "auth.failed",
    ]
    store.close()


def test_strict_sessions_end_when_idle(tmp_path, monkeypatch):
    import omniagents_workplace.accounts as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    store = Store(tmp_path / "accounts.db")
    accounts = Accounts(
        store, **Policy({"WORKPLACE_POLICY": "strict"}).session_limits()
    )
    alice = accounts.add(
        Account("alice", "alice", "Alice", "a@example.test", "", "", "")
    )
    token = accounts.start_session(alice)
    now[0] += STRICT_IDLE_SECONDS - 60
    assert accounts.signed_in(token) is alice  # activity keeps it alive
    now[0] += STRICT_IDLE_SECONDS - 60
    assert accounts.signed_in(token) is alice
    now[0] += STRICT_IDLE_SECONDS + 1
    assert accounts.signed_in(token) is None
    # Twelve hours in total, however active.
    fresh = accounts.start_session(alice)
    for _ in range(25):
        now[0] += 29 * 60
        accounts.signed_in(fresh)
    assert accounts.signed_in(fresh) is None
    store.close()


@pytest.fixture
def strict_azure(tmp_path):
    store = Store(tmp_path / "accounts.db")
    accounts = Accounts(store)
    accounts.add(Account("alice", "alice", "Alice", "alice@example.test", "", "", ""))
    accounts.add(Account("bob", "bob", "Bob", "bob@example.test", "", "", ""))
    azure = EasyAuth(BASE, config())
    app = FastAPI()

    @app.get("/workplace/api/whoami")
    def whoami():
        return {"ok": True}

    app.add_middleware(SignedIn, accounts=accounts, base_url=BASE, identity=azure)
    azure.install(app, accounts)
    azure.require(Policy({"WORKPLACE_POLICY": "strict"}), accounts)
    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        yield client, accounts
    store.close()


def test_strict_easy_auth_turns_password_sign_in_off(strict_azure):
    client, accounts = strict_azure
    accounts.set_password(accounts.accounts["bob"], "correct horse battery")
    with pytest.raises(AccountError, match="Microsoft"):
        accounts.sign_in("bob", "correct horse battery")
    with pytest.raises(AccountError, match="Microsoft"):
        accounts.accept_invite(
            accounts.invite(accounts.accounts["bob"]), "another long password"
        )


def test_a_session_ends_when_its_microsoft_identity_is_gone(strict_azure):
    client, accounts = strict_azure
    token = accounts.start_session(accounts.accounts["alice"])
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/workplace/api/whoami", headers=principal()).status_code == 200
    # Signed out of Microsoft, or removed there: no identity header.
    assert client.get("/workplace/api/whoami").status_code == 401
    assert accounts.signed_in(token) is None
    # Another person's identity never carries this session.
    token = accounts.start_session(accounts.accounts["bob"])
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/workplace/api/whoami", headers=principal()).status_code == 401
    assert accounts.signed_in(token) is None


def test_access_review_lists_everyone_by_factory_and_is_audited(
    installation,
):  # noqa: F811
    i, client, owner, other, a, b = installation
    review = client.get("/workplace/api/installation/access-review").json()
    assert review["generated_by"] == "owner"
    assert [p["username"] for p in review["installation_admins"]] == ["owner"]
    by_factory = {f["id"]: f["members"] for f in review["factories"]}
    assert {(m["username"], m["role"]) for m in by_factory[a["id"]]} == {
        ("owner", "admin"),
        ("other", "member"),
    }
    assert all(m["granted_at"] for m in by_factory[a["id"]])
    events = [e["event"] for e in i.audit_log.events()]
    assert events.count("membership.changed") == 3
    assert events[-1] == "access_review.exported"
    from omniagents_workplace.accounts import SESSION_COOKIE as cookie

    client.cookies.set(cookie, i.accounts.start_session(other))
    assert client.get("/workplace/api/installation/access-review").status_code == 403


def test_kill_switch_and_other_incidents_are_alerts(
    installation, monkeypatch
):  # noqa: F811
    i, client, owner, other, a, b = installation
    sent = []

    def send_soon(title, text, facts=()):
        sent.append((title, dict(facts)))

    monkeypatch.setattr(i.notifier, "send_soon", send_soon)
    client.post(prefix(a) + "/workplace/api/factory/stop", json={"reason": "drill"})
    client.post("/workplace/api/installation/stop", json={"reason": "incident"})
    w, _ = i.instances[a["id"]]
    stopped = w.audit_log.events(event="factory.stopped")[0]
    assert (stopped["actor"], stopped["factory"]) == ("owner", a["id"])
    assert (
        w.audit_log.events(event="alert.kill_switch")[0]["cause"] == "factory.stopped"
    )
    assert i.audit_log.events(event="installation.stopped")[0]["reason"] == "incident"
    assert [title for title, _ in sent] == ["Factory stopped", "All factories stopped"]
    assert sent[0][1]["reason"] == "drill" and sent[0][1]["By"] == "owner"
    # Repeated failed sign-ins are an incident too.
    from omniagents_workplace.accounts import AccountError

    for _ in range(12):
        try:
            i.accounts.sign_in("owner", "not the password at all")
        except AccountError:
            pass
    assert i.audit_log.events(event="alert.sign_in_failures")
    assert "Repeated failed sign-ins" in [title for title, _ in sent]


@pytest.mark.asyncio
async def test_teams_notifications_post_an_adaptive_card():
    assert not await Notifier({}).send("Factory stopped", "drill")
    with pytest.raises(ValueError):
        Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": "http://insecure.example"})
    posted = []

    def handler(request):
        posted.append(json.loads(request.content))
        return httpx.Response(202)

    notifier = Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": "https://teams.example/hook"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await notifier.send("Factory stopped", "drill", [("By", "eric")], client)
    card = posted[0]["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["content"]["body"][0]["text"] == "Factory stopped"
    assert card["content"]["body"][2]["facts"] == [{"title": "By", "value": "eric"}]
