import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.repository_settings import KEY, RepositorySettingsBody
from omniagents_workplace.server import create_app
from test_factory_runtime import _workplace


@pytest.fixture
def setup(tmp_path):
    w = _workplace(tmp_path, "http://runtime.invalid")
    owner = w.accounts.by_username("bill")
    w.github.create_repo("initech", "factory", owner.github_login, auto_init=True)
    w.github.create_repo("initech", "app", owner.github_login, auto_init=True)
    client = TestClient(create_app(w, static_dir=tmp_path))
    client.cookies.set(SESSION_COOKIE, w.accounts.start_session(owner))
    yield w, client, owner
    w.close()


def body(**values):
    return {
        "provider": "standin",
        "repositories": ["initech/factory", "initech/app"],
        "factory_repository": None,
        "self_improvement": False,
        **values,
    }


def test_admin_only_and_csrf(setup):
    w, client, _ = setup
    assert (
        client.put(
            "/workplace/api/repositories",
            json=body(),
            headers={"origin": "https://evil.test"},
        ).status_code
        == 403
    )
    member = w.add_person(username="member", name="Member", email="m@example.test")
    client.cookies.set(SESSION_COOKIE, w.accounts.start_session(member))
    for path in ("", "/available"):
        assert client.get("/workplace/api/repositories" + path).status_code == 403
    assert client.put("/workplace/api/repositories", json=body()).status_code == 403
    assert (
        client.post("/workplace/api/repositories/github/connect", json={}).status_code
        == 403
    )
    assert (
        client.put(
            "/workplace/api/repositories/github/app",
            json={"client_id": "id", "client_secret": "secret"},
        ).status_code
        == 403
    )
    assert (
        client.delete("/workplace/api/repositories/github/connection").status_code
        == 403
    )
    assert client.get("/factory/repository-policy").status_code == 401


def test_defaults_link_enable_disable_and_persistence(setup):
    w, client, _ = setup
    assert not client.get("/workplace/api/repositories").json()["self_improvement"]
    assert (
        client.put(
            "/workplace/api/repositories", json=body(self_improvement=True)
        ).status_code
        == 422
    )
    linked = body(factory_repository="initech/factory")
    assert client.put("/workplace/api/repositories", json=linked).status_code == 200
    assert not w.repository_settings.policy()["enabled"]
    assert (
        client.put(
            "/workplace/api/repositories", json={**linked, "self_improvement": True}
        ).status_code
        == 200
    )
    assert w.repository_settings.policy()["enabled"]
    token = w.apps.by_username("initech-factory").memory_token
    assert client.get(
        "/factory/repository-policy", headers={"Authorization": f"Bearer {token}"}
    ).json()["enabled"]
    assert w.store.meta(KEY)["factory_repository"] == "initech/factory"
    assert client.put("/workplace/api/repositories", json=body()).status_code == 200
    assert not w.repository_settings.policy()["enabled"]
    assert "initech/factory" in w.github.repos


def test_app_secrets_are_encrypted_and_never_returned(setup):
    w, client, _ = setup
    response = client.put(
        "/workplace/api/repositories/github/app",
        json={
            "client_id": "Iv1.test",
            "client_secret": "private-secret",
            "webhook_secret": "private-webhook",
        },
    )
    assert response.status_code == 200
    assert response.json()["oauth_configured"]
    assert (
        "private-secret" not in response.text and "private-webhook" not in response.text
    )
    assert "private-secret" not in w.store.meta("repository.github.app")
    assert (w.data_dir / "github-connection.key").stat().st_mode & 0o777 == 0o600
    assert (
        client.put(
            "/workplace/api/repositories/github/app", json={"client_id": "Iv1.test"}
        ).status_code
        == 200
    )
    assert w.repository_settings.oauth_config()["client_secret"] == "private-secret"
    assert (
        client.put(
            "/workplace/api/repositories/github/app", json={"client_id": "Iv1.other"}
        ).status_code
        == 422
    )


def configure(client):
    assert (
        client.put(
            "/workplace/api/repositories/github/app",
            json={"client_id": "Iv1.test", "client_secret": "client-secret"},
        ).status_code
        == 200
    )


def test_oauth_state_session_pkce_and_one_use(setup, monkeypatch):
    w, client, owner = setup
    configure(client)
    called = []

    def post(url, **kwargs):
        called.append(kwargs["data"])
        return httpx.Response(
            200,
            json={
                "access_token": "real-token",
                "expires_in": 28800,
                "refresh_token": "refresh-secret",
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("omniagents_workplace.repository_settings.httpx.post", post)
    monkeypatch.setattr(
        w.repository_settings, "github", lambda path, **kw: {"login": "real-user"}
    )
    query = parse_qs(
        urlsplit(
            client.post("/workplace/api/repositories/github/connect", json={}).json()[
                "url"
            ]
        ).query
    )
    pending = w.store.meta("repository.github.oauth")
    assert query["code_challenge_method"] == ["S256"] and pending[
        "verifier"
    ] not in str(query)
    path = "/workplace/api/repositories/github/callback"
    assert client.get(path, params={"state": "bad", "code": "code"}).status_code == 400
    other = TestClient(create_app(w))
    other.cookies.set(SESSION_COOKIE, w.accounts.start_session(owner))
    assert (
        other.get(path, params={"state": query["state"][0], "code": "code"}).status_code
        == 400
    )
    response = client.get(
        path,
        params={"state": query["state"][0], "code": "code"},
        follow_redirects=False,
    )
    assert (
        response.status_code == 303
        and called[0]["code_verifier"] == pending["verifier"]
    )
    assert (
        client.get(
            path, params={"state": query["state"][0], "code": "code"}
        ).status_code
        == 400
    )
    public = client.get("/workplace/api/repositories").json()
    assert public["connected_as"] == "real-user" and public["provider"] == "github"
    assert not public["self_improvement"] and public["repositories"] == []
    assert "real-token" not in json.dumps(public)
    assert "real-token" not in w.store.meta("repository.github.connection")


def connect_fake(w, owner):
    w.repository_settings._connection(
        {"account_id": owner.id, "login": "real-user", "access_token": "real-token"}
    )


def test_environment_reuses_existing_integration_and_redacts_ui(setup, monkeypatch):
    w, client, owner = setup
    connect_fake(w, owner)
    monkeypatch.setattr(
        w.repository_settings,
        "github",
        lambda path, **kw: {"default_branch": "main", "permissions": {"push": True}},
    )
    response = client.put(
        "/workplace/api/repositories",
        json=body(provider="github", factory_repository="initech/factory"),
    )
    assert response.status_code == 200
    app = w.apps.by_username("initech-factory")
    env = w.apps.env(app)
    assert env["GITHUB_MCP_URL"] == "https://api.githubcopilot.com/mcp/"
    assert env["GITHUB_MCP_TOKEN"] == "real-token" and env["GITHUB_GIT_URL"] == ""
    assert json.loads(env["GITHUB_REPOSITORIES"]) == ["initech/factory", "initech/app"]
    assert env["FACTORY_SELF_IMPROVEMENT"] == "false"
    assert "real-token" not in json.dumps(w.apps.settings(app))
    assert client.get("/workplace/api/state").json()["github"]["repositories"] == []
    assert client.get("/workplace/api/github/repos").status_code == 409
    assert client.post("/github/mcp", json={}).status_code == 409
    assert client.put("/workplace/api/repositories", json=body()).status_code == 200
    assert w.apps.env(app)["GITHUB_MCP_TOKEN"] != "real-token"
    assert w.apps.env(app)["GITHUB_GIT_URL"].endswith("/github")


def test_revoke_and_disconnect_disable_without_standin_fallback(setup, monkeypatch):
    from fastapi import HTTPException

    w, client, owner = setup
    connect_fake(w, owner)
    monkeypatch.setattr(
        w.repository_settings,
        "github",
        lambda *a, **k: {"default_branch": "main", "permissions": {"push": True}},
    )
    assert (
        client.put(
            "/workplace/api/repositories",
            json=body(
                provider="github",
                factory_repository="initech/factory",
                self_improvement=True,
            ),
        ).status_code
        == 200
    )

    def denied(*args, **kw):
        raise HTTPException(409, "Revoked")

    monkeypatch.setattr(w.repository_settings, "github", denied)
    assert not w.repository_settings.policy()["enabled"]
    assert not w.repository_settings.settings()["self_improvement"]
    assert (
        client.delete("/workplace/api/repositories/github/connection").status_code
        == 200
    )
    env = w.repository_settings.environment()
    assert env["GITHUB_MCP_TOKEN"] == "" and env["GITHUB_GIT_URL"] == ""
    assert w.repository_settings.settings()["provider"] == "github"


def test_refresh_and_disabled_account(setup, monkeypatch):
    w, _, owner = setup
    w.repository_settings._connection(
        {
            "account_id": owner.id,
            "login": "real-user",
            "access_token": "old",
            "expires_at": time.time() - 1,
            "refresh_token": "refresh",
        }
    )
    monkeypatch.setattr(
        w.repository_settings,
        "exchange",
        lambda fields: {
            "access_token": "new",
            "expires_at": time.time() + 1000,
            "refresh_token": "next",
        },
    )
    assert w.repository_settings.token() == "new"
    owner.disabled = True
    with pytest.raises(Exception, match="active Workplace administrator"):
        w.repository_settings.token()


@pytest.mark.parametrize(
    "name",
    ["../repo", "org/..", "org/repo?x", "org/repo\n", "https://github.com/org/repo"],
)
def test_invalid_repository_names(setup, name):
    _, client, _ = setup
    assert (
        client.put(
            "/workplace/api/repositories", json=body(repositories=[name])
        ).status_code
        == 422
    )


def test_duplicate_checkout_names_rejected(setup):
    _, client, _ = setup
    assert (
        client.put(
            "/workplace/api/repositories",
            json=body(repositories=["org/repo", "other/repo"]),
        ).status_code
        == 422
    )


def test_webhooks_require_signature_active_provider_and_selected_repo(
    setup, monkeypatch
):
    w, client, owner = setup
    configure(client)
    client.put(
        "/workplace/api/repositories/github/app",
        json={"client_id": "Iv1.test", "webhook_secret": "webhook"},
    )
    w.store.set_meta(KEY, body(provider="github"))
    content = json.dumps({"repository": {"full_name": "other/repo"}}).encode()
    headers = {
        "x-hub-signature-256": "sha256="
        + hmac.new(b"webhook", content, hashlib.sha256).hexdigest(),
        "x-github-event": "push",
    }
    assert client.post("/github/events", content=content).status_code == 401
    assert client.post("/github/events", content=content, headers=headers).json() == {
        "ignored": True
    }
    w.store.set_meta(KEY, body())
    assert (
        client.post("/github/events", content=content, headers=headers).status_code
        == 401
    )


def test_other_apps_do_not_receive_shared_github_credentials(setup, monkeypatch):
    w, client, owner = setup
    connect_fake(w, owner)
    monkeypatch.setattr(
        w.repository_settings,
        "github",
        lambda *a, **k: {"default_branch": "main", "permissions": {"push": True}},
    )
    assert (
        client.put(
            "/workplace/api/repositories", json=body(provider="github")
        ).status_code
        == 200
    )
    member = w.add_person(username="member", name="Member", email="member@example.test")
    app = w.apps.register(member, name="Member App", username="member-app")
    assert "real-token" not in json.dumps(w.apps.env(app))
    assert "FACTORY_SETTINGS_TOKEN" not in w.apps.env(app)
    assert (
        client.get(
            "/factory/repository-policy",
            headers={"Authorization": f"Bearer {app.memory_token}"},
        ).status_code
        == 401
    )


def test_restoring_a_backup_does_not_copy_live_github_access(setup, tmp_path):
    from omniagents_workplace.backup import backup, restore
    from omniagents_workplace.store import Store

    w, client, owner = setup
    configure(client)
    connect_fake(w, owner)
    w.store.set_meta(
        KEY,
        body(
            provider="github",
            factory_repository="initech/factory",
            self_improvement=True,
        ),
    )
    saved = backup(w.data_dir, tmp_path / "backups")
    assert (saved / "github-connection.key").is_file()
    target = tmp_path / "restored"
    restore(saved, target)
    store = Store(target / "workplace.db")
    try:
        assert store.meta("repository.github.connection") is None
        assert store.meta("repository.github.app") is None
        assert store.meta(KEY) is None
    finally:
        store.close()


def test_pat_connect_validate_encrypt_and_paginate(setup, monkeypatch):
    w, client, owner = setup
    paths = []

    def get(url, **kwargs):
        paths.append(url)
        assert kwargs["headers"]["Authorization"] == "Bearer github_pat_test"
        if url.endswith("/user"):
            data = {"login": "pat-user"}
        elif url.endswith("&page=1"):
            data = [
                {"full_name": f"org/repo{i}", "default_branch": "main"}
                for i in range(30)
            ]
        else:
            data = [{"full_name": "org/last", "default_branch": "main"}]
        return httpx.Response(200, json=data)

    monkeypatch.setattr("omniagents_workplace.repository_settings.httpx.get", get)
    w.store.set_meta(
        KEY, body(factory_repository="initech/factory", self_improvement=True)
    )
    w.store.set_meta("repository.github.oauth", {"state": "old"})
    response = client.put(
        "/workplace/api/repositories/github/token", json={"token": "github_pat_test"}
    )
    assert response.status_code == 200
    assert response.json()["connected_as"] == "pat-user"
    assert response.json()["auth_type"] == "pat"
    assert not response.json()["oauth_configured"]
    assert not response.json()["self_improvement"]
    assert response.json()["repositories"] == []
    assert response.json()["factory_repository"] is None
    assert "github_pat_test" not in response.text
    assert "github_pat_test" not in w.store.meta("repository.github.connection")
    assert w.store.meta("repository.github.oauth") is None
    assert w.repository_settings.connection()["account_id"] == owner.id
    assert w.repository_settings.environment()["GITHUB_MCP_TOKEN"] == "github_pat_test"
    available = client.get("/workplace/api/repositories/available").json()[
        "repositories"
    ]
    assert len(available) == 30
    assert not any("&page=2" in path for path in paths)
    second = client.get("/workplace/api/repositories/available?page=2").json()
    assert len(second["repositories"]) == 1
    assert not second["has_more"]
    assert not any("installations" in path for path in paths)
    # App configuration must not discard an unrelated PAT connection.
    configure(client)
    assert w.repository_settings.token() == "github_pat_test"


def test_bad_pat_preserves_connection_and_requires_admin_csrf(setup, monkeypatch):
    w, client, owner = setup
    w.repository_settings._connection(
        {"access_token": "old-token", "login": "old", "account_id": owner.id}
    )

    def get(*args, **kwargs):
        return httpx.Response(401, json={"message": "secret-should-not-echo"})

    monkeypatch.setattr("omniagents_workplace.repository_settings.httpx.get", get)
    path = "/workplace/api/repositories/github/token"
    malformed = client.put(path, json={"token": "private-token-with-invalid-format"})
    assert malformed.status_code == 422
    assert "private-token" not in malformed.text
    response = client.put(path, json={"token": "invalid"})
    assert response.status_code == 409
    assert "secret-should-not-echo" not in response.text
    assert w.repository_settings.token() == "old-token"
    assert (
        client.put(
            path, json={"token": "invalid"}, headers={"origin": "https://evil.test"}
        ).status_code
        == 403
    )
    member = w.add_person(
        username="pat-member", name="Member", email="pat@example.test"
    )
    client.cookies.set(SESSION_COOKIE, w.accounts.start_session(member))
    assert client.put(path, json={"token": "invalid"}).status_code == 403


def test_repository_search_is_remote_scoped_and_paginated(setup, monkeypatch):
    w, client, owner = setup
    w.repository_settings._connection(
        {
            "access_token": "test_token",
            "login": "user",
            "account_id": owner.id,
            "auth_type": "device",
        }
    )
    paths = []

    def github(path, **kwargs):
        paths.append(path)
        if path.startswith("/user/orgs"):
            return [{"login": "my-org"}]
        assert path.startswith("/search/repositories?")
        query = parse_qs(urlsplit(path).query)
        assert query["page"] == ["2"]
        assert query["per_page"] == ["30"]
        assert "user:user" in query["q"][0] and "user:my-org" in query["q"][0]
        assert "fork:true" in query["q"][0] and "in:name" in query["q"][0]
        return {
            "items": [{"full_name": "my-org/needle", "default_branch": "main"}],
            "total_count": 61,
            "incomplete_results": False,
        }

    monkeypatch.setattr(w.repository_settings, "github", github)
    response = client.get(
        "/workplace/api/repositories/available?provider=github&q=needle&page=2"
    )
    assert response.status_code == 200
    assert response.json()["has_more"]
    assert response.json()["repositories"][0]["full_name"] == "my-org/needle"
    assert len(paths) == 2
