"""Concurrent factory isolation and shared-login authorization."""

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents_workplace.accounts import Account, Accounts, SESSION_COOKIE
from omniagents_workplace.installation import Installation
from omniagents_workplace.installation_server import create_installation_app


def project(root, name):
    path = root / name
    path.mkdir()
    (path / "project.yml").write_text("product:\n  name: test-factory\n")
    (path / "sandbox_profile_quicksand.yml").write_text(
        "client:\n  type: quicksand\noptions:\n  image: ubuntu-desktop\n"
    )
    return path


def identity(i, username, admin=False):
    return i.accounts.add(
        Account(
            id=Accounts.new_id(),
            username=username,
            name=username,
            email=f"{username}@example.test",
            slack_id="",
            github_login="",
            linear_id="",
            admin=admin,
        )
    )


@pytest.fixture
def installation(tmp_path):
    i = Installation(tmp_path / "installation", base_url="http://testserver")
    owner = identity(i, "owner", True)
    other = identity(i, "other")
    a = i.add(
        name="One",
        project=project(tmp_path, "one"),
        architecture="personal",
        owner=owner,
    )
    b = i.add(
        name="Two",
        project=project(tmp_path, "two"),
        architecture="specialist",
        owner=owner,
    )
    i.grant(a["id"], other.id, "member")
    with TestClient(create_installation_app(i, static_dir=tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
        yield i, client, owner, other, a, b


def prefix(record):
    return "/factories/" + record["id"]


def test_explicit_routes_and_data_isolation(installation, monkeypatch):
    i, client, owner, other, a, b = installation
    assert not i.errors, i.errors
    for record, mode in ((a, "personal"), (b, "specialist")):
        response = client.get(prefix(record) + "/workplace/api/session")
        assert response.status_code == 200, response.text
        assert response.json()["architecture"] == mode
        assert not response.json()["configurable"]
    wa, _ = i.instances[a["id"]]
    wb, _ = i.instances[b["id"]]
    wa.store.put("proof", "same-id", {"text": "one"})
    wb.store.put("proof", "same-id", {"text": "two"})
    assert dict(wa.store.docs("proof"))["same-id"]["text"] == "one"
    assert dict(wb.store.docs("proof"))["same-id"]["text"] == "two"
    assert wa.data_dir != wb.data_dir
    assert wa.apps.active()[0].memory_token != wb.apps.active()[0].memory_token
    assert (
        wa.factory_config.load()["factory"]["auth_token"]
        != wb.factory_config.load()["factory"]["auth_token"]
    )
    assert client.post(prefix(a) + "/workplace/api/config", json={}).status_code in {
        404,
        405,
    }
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert (
        client.get(prefix(a) + "/workplace/api/session").json()["account"]["admin"]
        is False
    )
    assert client.get(prefix(b) + "/workplace/api/session").status_code == 403
    assert (
        client.get("/workplace/api/factories").json()["factories"][0]["id"] == a["id"]
    )
    assert len(client.get("/workplace/api/factories").json()["factories"]) == 1
    assert (
        client.post(
            "/workplace/api/factories", json={"name": "bad", "project": a["project"]}
        ).status_code
        == 403
    )
    assert client.post(prefix(a) + "/workplace/api/people", json={}).status_code == 403
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(prefix(b) + "/workplace/api/personal/runtime/ws"):
            pass
    assert error.value.code == 1008
    i.grant(a["id"], other.id, None)
    assert client.get(prefix(a) + "/workplace/api/session").status_code == 403


def test_machine_tokens_are_factory_local(installation):
    i, client, _, _, a, b = installation
    wa, _ = i.instances[a["id"]]
    token = wa.apps.active()[0].memory_token
    client.cookies.clear()
    # MCP endpoint accepts only its own factory's credential, independent of login.
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    assert (
        client.post(prefix(a) + "/memory/mcp", headers=headers, json=body).status_code
        == 200
    )
    assert client.post(
        prefix(b) + "/memory/mcp", headers=headers, json=body
    ).status_code in {401, 403}


def test_project_and_runtime_isolation(installation, monkeypatch):
    i, client, owner, _, a, b = installation
    with pytest.raises(ValueError, match="non-overlapping"):
        i.add(
            name="Overlap", project=a["project"], architecture="personal", owner=owner
        )
    monkeypatch.setenv("GH_TOKEN", "ambient-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-model-secret")
    environments = []
    for record in (a, b):
        p = i.processes[record["id"]]
        w, _ = i.instances[record["id"]]
        profile = p.prepare_profile(7355)
        env = p.environment(
            w, record["architecture"], 8000, "runtime-token", "control-token", profile
        )
        assert "GH_TOKEN" not in env and "OPENAI_API_KEY" not in env
        assert prefix(record) in env["MEMORY_MCP_URL"]
        data = yaml.safe_load(profile.read_text())
        assert str(p.directory) in data["client"]["state_dir"]
        environments.append(env)
    for key in (
        "HOME",
        "OMNIAGENTS_HOME",
        "CODEX_HOME",
        "GH_CONFIG_DIR",
        "XDG_CONFIG_HOME",
        "OMNIAGENTS_SANDBOX_PROFILE",
    ):
        assert environments[0][key] != environments[1][key]
    assert client.get("/factories/not-valid/workplace/api/session").status_code == 404
    assert (
        client.post(
            "/workplace/api/factories",
            headers={"origin": "https://evil.test"},
            json={"name": "bad", "project": a["project"]},
        ).status_code
        == 403
    )


def test_registry_and_members_survive_restart(tmp_path):
    directory = tmp_path / "installation"
    i = Installation(directory, base_url="http://testserver")
    owner = identity(i, "owner", True)
    record = i.add(
        name="Persistent",
        project=project(tmp_path, "project"),
        architecture="personal",
        owner=owner,
    )
    cookie = i.accounts.start_session(owner)
    with TestClient(create_installation_app(i, static_dir=tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        before = client.get(prefix(record) + "/workplace/api/session").json()
    again = Installation(directory, base_url="http://testserver")
    with TestClient(create_installation_app(again, static_dir=tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        after = client.get(prefix(record) + "/workplace/api/session").json()
        assert before == after
        assert len(again.records) == 1


def test_create_factory_after_startup(installation, tmp_path):
    i, client, owner, _, a, b = installation
    path = project(tmp_path, "dynamic")
    response = client.post(
        "/workplace/api/factories",
        json={"name": "Dynamic", "project": str(path), "architecture": "personal"},
    )
    assert response.status_code == 200, response.text
    record = response.json()
    assert (
        client.get(prefix(record) + "/workplace/api/session").json()["organization"]
        == "Dynamic"
    )
    assert i.product_config_name(path) == "test_factory"


def test_revocation_closes_open_socket(installation):
    i, client, _, other, a, _ = installation
    # A minimal authenticated socket isolates gateway revocation from runtime state.
    _, app = i.instances[a["id"]]
    from fastapi import WebSocket

    @app.websocket("/watch-test")
    async def watch(socket: WebSocket):
        await socket.accept()
        await socket.send_text("ready")
        await socket.receive_text()

    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    with client.websocket_connect(prefix(a) + "/watch-test") as socket:
        assert socket.receive_text() == "ready"
        i.grant(a["id"], other.id, None)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_text()
        assert error.value.code == 1008


def test_socket_mode_url_contains_factory_scope(installation):
    i, client, _, _, a, b = installation
    w, _ = i.instances[a["id"]]
    app = w.apps.active()[0]
    token = w.slack.apps[app.slack_app_id].app_token
    client.cookies.clear()
    response = client.post(
        prefix(a) + "/slack/api/apps.connections.open",
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 200, response.text
    assert prefix(a) + "/slack/link/" in response.json()["url"]
    assert not client.post(
        prefix(b) + "/slack/api/apps.connections.open",
        headers={"Authorization": "Bearer " + token},
    ).json()["ok"]


def test_library_views_and_csp_are_factory_local(installation):
    i, client, _, _, a, b = installation
    wa, _ = i.instances[a["id"]]
    item = client.post(
        prefix(a) + "/workplace/api/library/artifacts",
        json={"title": "Proof", "kind": "page"},
    ).json()
    (wa.library.root / item["id"] / "index.html").write_text('<img src="proof.png">')
    client.cookies.clear()
    response = client.get(prefix(a) + item["view"])
    assert response.status_code == 200
    assert prefix(a) + "/library/view/" in response.headers["content-security-policy"]
    assert client.get(prefix(b) + item["view"]).status_code == 404


def test_managed_workflows_use_isolated_runtime_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    i = Installation(
        tmp_path / "installation",
        base_url="http://testserver",
        runner_image=str(tmp_path / "clean-image"),
    )
    owner = identity(i, "owner", True)
    record = i.add(
        name="CI",
        project=project(tmp_path, "project"),
        architecture="specialist",
        owner=owner,
    )
    with TestClient(create_installation_app(i, static_dir=tmp_path)):
        assert not i.errors
        w, _ = i.instances[record["id"]]
        runner = w.github.actions
        assert runner.quicksand.image == str(tmp_path / "clean-image")
        from omniagents_workplace.github.agent_jobs import AgentJobSpace

        job = AgentJobSpace(
            1, runner.agent_profile, environment=runner.agent_environment
        )
        env = job.runtime_env()
        assert "OPENAI_API_KEY" not in env
        assert "MEMORY_MCP_TOKEN" not in env
        assert str(i.directory / "factories" / record["id"]) in env["CODEX_HOME"]


def test_framework_redirects_keep_factory_scope(installation):
    i, client, _, _, a, _ = installation
    from fastapi.responses import RedirectResponse

    _, app = i.instances[a["id"]]

    @app.get("/redirect-test")
    async def redirect():
        return RedirectResponse("/workplace/api/session")

    app.router.routes.insert(0, app.router.routes.pop())
    response = client.get(prefix(a) + "/redirect-test", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith(prefix(a) + "/workplace/api/session")


def test_adopt_existing_factory_preserves_accounts_sessions_and_service_data(
    tmp_path, monkeypatch
):
    from test_factory_runtime import _workplace
    from omniagents_workplace.docker_factory import DockerFactoryProcess

    original = _workplace(tmp_path, "http://existing-runtime:8765")
    owner = original.accounts.by_username("bill")
    cookie = original.accounts.start_session(owner)
    original.store.put("proof", "existing", {"conversation": "keep-me"})
    original.apps.active()[0].memory_token
    config = original.factory_config.load()
    data = original.data_dir
    project_dir = original.factory_config.root
    original.close()

    async def start(self, *args):
        self.running = self.ready = True

    async def stop(self):
        self.running = self.ready = False

    monkeypatch.setattr(DockerFactoryProcess, "start", start)
    monkeypatch.setattr(DockerFactoryProcess, "stop", stop)
    i = Installation(tmp_path / "installation", base_url="http://testserver")
    record = i.adopt(
        data,
        config,
        project=project_dir,
        architecture="personal",
        runtime_container="existing-runtime",
    )
    with TestClient(create_installation_app(i, static_dir=tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        assert client.get("/workplace/api/session").json()["account"]["id"] == owner.id
        result = client.get(prefix(record) + "/workplace/api/session").json()
        assert result["account"]["id"] == owner.id
        assert result["architecture"] == "personal"
        w, _ = i.instances[record["id"]]
        assert dict(w.store.docs("proof"))["existing"] == {"conversation": "keep-me"}
        assert len(w.accounts.people()) == 1
        assert i.processes[record["id"]].name == "existing-runtime"


def test_hosted_creation_copies_template_without_credentials(tmp_path):
    template = project(tmp_path, "template")
    (template / ".env").write_text("PRIVATE=not-for-new-factories")
    (template / "config").mkdir()
    (template / "config" / "secret").write_text("secret")
    i = Installation(
        tmp_path / "installation",
        base_url="http://testserver",
        docker_image="factory:test",
        docker_network="test",
        docker_host_data="/host/installation",
        template=template,
    )
    owner = identity(i, "owner", True)
    record = i.add(name="New", project="", architecture="specialist", owner=owner)
    path = Path(record["project"])
    assert (path / "project.yml").is_file()
    assert not (path / ".env").exists() and not (path / "config").exists()
    assert path.is_relative_to(i.directory / "factories" / record["id"])
    i.store.close()


def test_preview_setup_and_mcp_require_factory_authority(installation):
    i, client, owner, other, a, b = installation
    wa = i.instances[a["id"]][0]
    token = wa.apps.active()[0].memory_token
    # Agent transport does not depend on a browser session, and cannot cross factories.
    client.cookies.clear()
    assert (
        client.post(
            prefix(b) + "/coolify/mcp",
            headers={"Authorization": "Bearer " + token},
            json={},
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix(a) + "/coolify/mcp",
            headers={"Authorization": "Bearer " + token},
            json={},
        ).status_code
        == 503
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert (
        client.post(
            f"/workplace/api/factories/{a['id']}/previews/inspect",
            json={"repository": "org/site"},
        ).status_code
        == 403
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
    assert (
        client.post(
            f"/workplace/api/factories/{a['id']}/previews/inspect",
            json={"repository": "org/site"},
        ).status_code
        == 422
    )
    env = wa.apps.env(wa.apps.active()[0])
    assert env["COOLIFY_MCP_URL"].endswith(prefix(a) + "/coolify/mcp")
    assert env["COOLIFY_MCP_TOKEN"] == token


def test_guest_share_routes_are_factory_scoped_without_login(installation):
    from urllib.parse import urlsplit, parse_qs

    i, client, _, _, a, b = installation
    wa, _ = i.instances[a["id"]]
    item = client.post(
        prefix(a) + "/workplace/api/library/artifacts",
        json={"title": "Shared", "kind": "document"},
    ).json()
    (wa.library.root / item["id"] / "index.md").write_text("# Stakeholder review")
    link = client.post(
        prefix(a) + f"/workplace/api/sharing/artifact/{item['id']}"
    ).json()
    parts = urlsplit(link["url"])
    assert parts.path.startswith(prefix(a) + "/share/")
    headers = {"Authorization": "Bearer " + parse_qs(parts.fragment)["token"][0]}
    client.cookies.clear()
    assert (
        client.get(parts.path + "/api", headers=headers).json()["item"]["markdown"]
        == "# Stakeholder review"
    )
    assert (
        client.get(
            parts.path.replace(prefix(a), prefix(b)) + "/api", headers=headers
        ).status_code
        == 404
    )
    response = client.post(
        parts.path + "/identity", headers=headers, json={"name": "Reviewer"}
    )
    assert response.status_code == 200
    assert "Path=" + parts.path in response.headers["set-cookie"]
    assert (
        client.post(
            parts.path + "/comments", headers=headers, json={"body": "Looks good"}
        ).status_code
        == 200
    )
    assert client.get(prefix(a) + "/workplace/api/library/artifacts").status_code == 403
    assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"


def test_shared_files_and_workflows_are_factory_isolated(installation):
    i, client, owner, other, a, b = installation
    wa, _ = i.instances[a["id"]]
    wb, _ = i.instances[b["id"]]
    assert wa.shared.root != wb.shared.root
    assert (
        client.post(
            prefix(a) + "/workplace/api/shared/files",
            json={"action": "mkdir", "path": "Only A"},
        ).status_code
        == 200
    )
    assert client.get(prefix(b) + "/workplace/api/shared/files").json()["entries"] == []
    r = client.post(
        prefix(a) + "/workplace/api/shared/uploads",
        json={"files": [{"path": "one.txt", "size": 3}]},
    )
    assert r.status_code == 200, r.text
    ident = r.json()["id"]
    assert (
        client.put(
            prefix(b) + f"/workplace/api/shared/uploads/{ident}/0",
            content=b"one",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 404
    )
    assert (
        client.put(
            prefix(a) + f"/workplace/api/shared/uploads/{ident}/0",
            content=b"one",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            prefix(a) + f"/workplace/api/shared/uploads/{ident}/commit", json={}
        ).status_code
        == 200
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert (
        client.get(prefix(a) + "/workplace/api/shared/content?path=one.txt").content
        == b"one"
    )
    assert (
        client.post(
            prefix(a) + "/workplace/api/shared/workflows",
            json={"name": "No", "instruction": "No"},
        ).status_code
        == 403
    )
    assert client.get(prefix(b) + "/workplace/api/shared/files").status_code == 403


def test_mcp_apps_are_factory_scoped_and_preserve_membership(installation):
    i, client, owner, other, a, b = installation
    first = prefix(a) + "/workplace/api/extensions"
    second = prefix(b) + "/workplace/api/extensions"
    assert (
        client.put(
            first + "/slack", json={"enabled": False, "permissions": {}}
        ).status_code
        == 200
    )
    assert (
        next(app for app in client.get(second).json()["apps"] if app["id"] == "slack")[
            "enabled"
        ]
        is True
    )
    wa, _ = i.instances[a["id"]]
    wa.store.put(
        "extension.installation",
        "external",
        {
            "id": "external",
            "kind": "remote",
            "owner": owner.id,
            "enabled": True,
            "token": "never-render-this",
            "entrypoints": [],
            "tools": [],
        },
    )
    assert "never-render-this" not in client.get(first).text
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert "external" not in {app["id"] for app in client.get(first).json()["apps"]}
    assert (
        client.post(
            first + "/external/tools/call", json={"name": "anything", "arguments": {}}
        ).status_code
        == 403
    )
    assert (
        client.put(
            first + "/linear", json={"enabled": False, "permissions": {}}
        ).status_code
        == 403
    )
    assert client.get(second).status_code == 403


def test_app_modules_load_without_factory_session(installation):
    import re
    from urllib.parse import urlsplit

    i, client, owner, other, a, b = installation
    static = i.static_dir
    (static / "assets").mkdir()
    (static / "assets" / "extension-test.js").write_text("export const ready = true;")
    (static / "extension.html").write_text(
        '<script type="module" crossorigin src="/assets/extension-test.js"></script>'
    )
    response = client.post(
        prefix(a) + "/workplace/api/extensions/slack/open", json={"tool": "slack.open"}
    )
    assert response.status_code == 200, response.text
    html = response.json()["resource"]["contents"][0]["text"]
    asset = re.search(r'src="([^"]+)"', html)[1]
    assert urlsplit(asset).path == "/assets/mcp-apps-v1/extension-test.js"
    assert urlsplit(asset).hostname != "testserver"
    client.cookies.clear()
    response = client.get(
        asset,
        headers={"Origin": "https://isolated-app.example"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "export const ready" in response.text
    assert (
        client.get(asset.replace("extension-test.js", "missing.js")).status_code == 404
    )
    assert (
        client.get(
            asset.replace(
                "/assets/mcp-apps-v1/extension-test.js", "/workplace/api/session"
            )
        ).status_code
        == 404
    )
    assert client.get(
        prefix(a) + "/workplace/api/extensions", follow_redirects=False
    ).status_code in {401, 403}


def test_app_navigation_is_personal_and_factory_scoped(installation):
    i, client, owner, other, a, b = installation

    def recorder(factory):
        return next(
            app
            for app in client.get(prefix(factory) + "/workplace/api/extensions").json()[
                "apps"
            ]
            if app["id"] == "recorder"
        )

    first = prefix(a) + "/workplace/api/extensions/recorder/navigation"
    assert client.put(first, json={"show_in_navigation": False}).status_code == 200
    assert recorder(a)["showInNavigation"] is False
    assert recorder(a)["enabled"] is True
    assert recorder(b)["showInNavigation"] is True
    assert (
        client.post(
            prefix(a) + "/workplace/api/extensions/recorder/tools/call",
            json={"name": "recorder.record", "arguments": {"action": "list"}},
        ).status_code
        == 200
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert recorder(a)["showInNavigation"] is True
    assert client.put(first, json={"show_in_navigation": False}).status_code == 200
    assert recorder(a)["showInNavigation"] is False
    assert (
        client.put(
            prefix(b) + "/workplace/api/extensions/recorder/navigation",
            json={"show_in_navigation": False},
        ).status_code
        == 403
    )
    assert (
        client.put(
            first, json={"show_in_navigation": True, "account_id": owner.id}
        ).status_code
        == 422
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
    assert recorder(a)["showInNavigation"] is False
    assert client.put(first, json={"show_in_navigation": True}).status_code == 200
    assert recorder(a)["showInNavigation"] is True


def test_native_dock_preferences_are_personal_and_do_not_change_access(installation):
    i, client, owner, other, a, b = installation
    endpoint = prefix(a) + "/workplace/api/extensions"
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    for name in (
        "personal",
        "factory",
        "shared",
        "library",
        "desktop",
        "coolify",
        "memory",
    ):
        assert (
            client.put(
                endpoint + "/native:" + name + "/navigation",
                json={"show_in_navigation": False},
            ).status_code
            == 200
        )
    assert not any(client.get(endpoint).json()["nativeNavigation"].values())
    assert (
        client.put(
            endpoint + "/native:apps/navigation", json={"show_in_navigation": False}
        ).status_code
        == 404
    )
    assert (
        client.put(
            endpoint + "/native:unknown/navigation", json={"show_in_navigation": False}
        ).status_code
        == 404
    )
    assert (
        client.put(
            prefix(b) + "/workplace/api/extensions/native:memory/navigation",
            json={"show_in_navigation": False},
        ).status_code
        == 403
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
    assert all(client.get(endpoint).json()["nativeNavigation"].values())
    assert (
        client.put(
            endpoint + "/native:memory/navigation", json={"show_in_navigation": False}
        ).status_code
        == 200
    )
    assert (
        client.get(prefix(b) + "/workplace/api/extensions").json()["nativeNavigation"][
            "memory"
        ]
        is True
    )
