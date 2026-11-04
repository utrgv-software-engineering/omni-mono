import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents_workplace.deployments import setup
from omniagents_workplace.github.state import GitHubState

RAW = """services:
  web:
    build: .
    expose: [3000]
    environment:
      PASSWORD: ${PREVIEW_DB_PASSWORD:?required}
      KEY: ${PREVIEW_SECRET_KEY_BASE}
      API: ${TEST_API_KEY}
      OPTIONAL: ${OPTIONAL:-default}
volumes:
  database:
"""


def test_compose_inspection_and_isolation():
    result = setup.inspect_compose(RAW)
    assert result["services"] == [{"name": "web", "ports": [3000]}]
    assert result["variables"] == [
        "PREVIEW_DB_PASSWORD",
        "PREVIEW_SECRET_KEY_BASE",
        "TEST_API_KEY",
    ]
    assert result["generated"] == ["PREVIEW_DB_PASSWORD", "PREVIEW_SECRET_KEY_BASE"]
    for field in [
        'ports: ["3000:3000"]',
        "privileged: true",
        "network_mode: host",
        'volumes: ["/var/run/docker.sock:/var/run/docker.sock"]',
    ]:
        with pytest.raises(ValueError):
            setup.inspect_compose("services:\n  web:\n    " + field)
    with pytest.raises(ValueError):
        setup.inspect_compose(RAW + "  shared:\n    external: true\n")


def test_compose_declares_the_sign_in_its_previews_seed():
    assert setup.inspect_compose(RAW)["login"] is None
    login = "x-preview:\n  login:\n    email: demo@example.com\n    password: {}\n"
    declared = setup.inspect_compose(login.format("PREVIEW_DB_PASSWORD") + RAW)
    assert declared["login"] == {
        "email": "demo@example.com",
        "variable": "PREVIEW_DB_PASSWORD",
    }
    # The password must name a variable the file requires, never a literal one.
    for password in ["hunter2", "OPTIONAL"]:
        with pytest.raises(ValueError, match="x-preview.login"):
            setup.inspect_compose(login.format(password) + RAW)


def test_setup_is_repeatable_and_keeps_generated_secrets(tmp_path, monkeypatch):
    github = GitHubState(tmp_path / "git", "example.test")
    github.add_user("org")
    github.add_user("factory", token="standin-token")
    repo = github.create_repo("org", "site", "factory")
    repo.bare.write_files(
        "main",
        [("compose.preview.yml", RAW.encode())],
        "preview",
        github._who("factory"),
    )
    repo.bare.write_files("fix", [("fix.txt", b"fix")], "fix", github._who("factory"))
    pull = github.create_pull(repo, "factory", "Fix the page", "fix", "main")
    from omniagents_workplace.github.serialize import Serializer
    from omniagents_workplace.store import Store

    w = SimpleNamespace(
        github=github,
        github_json=Serializer(github, lambda: "http://local/factories/one"),
        base_url="http://local/factories/one",
        internal_url="http://internal/factories/one",
        sandbox_url="http://sandbox/factories/one",
        apps=SimpleNamespace(
            active=lambda: [SimpleNamespace(username="factory", github_login="factory")]
        ),
        factory_config=SimpleNamespace(load=lambda: {"app": {"username": "factory"}}),
        repository_settings=SimpleNamespace(
            settings=lambda: {"provider": "standin", "repositories": ["org/site"]}
        ),
    )
    calls = []
    apps = []

    async def api(method, path, body=None):
        calls.append((method, path, body))
        if path == "/projects":
            return [{"name": "Factory previews", "uuid": "project"}]
        if path == "/servers":
            return [{"name": "localhost", "uuid": "server"}]
        if path == "/applications/public":
            apps.append(
                {
                    "uuid": "app",
                    "branch": "main",
                    "repository": body["git_repository"],
                    "build_pack": "dockercompose",
                    "enabled": True,
                }
            )
            return {"uuid": "app"}
        return {}

    bridge = SimpleNamespace(
        token="native",
        api=api,
        applications=AsyncMock(side_effect=lambda: apps),
        runner=None,
        wake=asyncio.Event(),
    )
    deployments = SimpleNamespace(
        previews={"one": bridge},
        prepare=lambda _: (tmp_path, {}),
        url=lambda _: "http://coolify",
    )
    store = Store(tmp_path / "installation.db")
    i = SimpleNamespace(
        instances={"one": (w, None)}, deployments=deployments, store=store
    )
    operation = setup.PreviewSetup(i, "one")
    monkeypatch.setattr(setup, "compose", AsyncMock(return_value="LOADED"))

    async def scenario():
        plan = await operation.inspect("org/site")
        # Open now, so previewed only if chosen.
        assert plan["open_pulls"] == [{"number": pull.number, "title": "Fix the page"}]
        request = {
            **plan,
            "services": {"web": 3000},
            "variables": {"TEST_API_KEY": "test-only"},
            "include": [pull.number],
        }
        result = await operation.enable(request)
        assert result["status"] == "enabled"
        [(key, scope)] = store.docs(setup.SCOPE)
        assert key == "app" and scope["include"] == [pull.number]
        since = scope["since"]
        first = [c[2] for c in calls if c[1].endswith("/envs/bulk")]
        await operation.enable(request)
        assert len([c for c in calls if c[1] == "/applications/public"]) == 1
        # Enabling again keeps when previews began.
        assert store.docs(setup.SCOPE)[0][1]["since"] == since
        assert first == [c[2] for c in calls if c[1].endswith("/envs/bulk")][2:]
        assert bridge.wake.is_set()
        with pytest.raises(ValueError, match="changed"):
            await operation.enable({**request, "revision": "outdated"})
        with pytest.raises(ValueError, match="selection"):
            await operation.inspect("org/other")

    asyncio.run(scenario())


def test_github_token_connection_replaces_anonymous_coolify_source(
    tmp_path, monkeypatch
):
    import json

    (tmp_path / "preview-setup-example.json").write_text(
        json.dumps(
            {
                "provider": "github",
                "repository": "org/private",
                "application": "app",
            }
        )
    )
    operations = []

    async def execute(*args):
        operations.append(("source", args[-1]))

    async def api(method, path, body):
        operations.append(("url", body["git_repository"]))

    monkeypatch.setattr(setup, "compose", execute)
    bridge = SimpleNamespace(
        runner=None,
        factory_id="one",
        api=api,
        d=SimpleNamespace(prepare=lambda _: (tmp_path, {})),
    )
    w = SimpleNamespace(
        repository_settings=SimpleNamespace(
            settings=lambda: {"provider": "github"},
            token=lambda: "private-token",
        )
    )

    async def scenario():
        await setup.refresh_git_credentials(bridge, w)
        assert operations[0][0] == "source"
        assert "$a->source_id=null" in operations[0][1]
        assert "$a->source_type=null" in operations[0][1]
        assert "private-token" not in operations[0][1]
        assert operations[1] == (
            "url",
            "https://x:private-token@github.com/org/private.git",
        )
        await setup.refresh_git_credentials(bridge, w)
        assert len(operations) == 2
        w.repository_settings.token = lambda: "refreshed-token"
        await setup.refresh_git_credentials(bridge, w)
        assert (
            operations[-1][1] == "https://x:refreshed-token@github.com/org/private.git"
        )

    asyncio.run(scenario())
