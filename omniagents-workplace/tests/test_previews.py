import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from omniagents_workplace.deployments import setup
from omniagents_workplace.deployments.comments import body_for
from omniagents_workplace.deployments.previews import SCOPE, Previews, repository
from omniagents_workplace.store import Store


@pytest.fixture
def bridge(tmp_path):
    store = Store(tmp_path / "previews.db")
    settings = {"provider": "github", "repositories": ["org/site"]}
    w = SimpleNamespace(
        repository_settings=SimpleNamespace(settings=lambda: settings),
        base_url="http://local/factories/one",
        internal_url="http://internal/factories/one",
        sandbox_url="http://sandbox/factories/one",
    )
    i = SimpleNamespace(store=store, instances={"one": (w, None)})
    d = SimpleNamespace(
        installation=i,
        preview_url=lambda key: "https://preview-" + key + ".example.test",
        preview_errors={},
        directory=lambda factory_id: tmp_path,
    )
    p = Previews(d, "one", None)
    calls = []

    async def api(method, path, body=None):
        calls.append((method, path, deepcopy(body)))
        if method == "GET":
            return {"status": "finished"} if path.startswith("/deployments/") else None
        if method == "POST":
            return {"preview": {}, "deployment_uuid": "deployment"}
        return {}

    p.api = api
    p.ensure_proxy = AsyncMock()
    yield p, calls, w, settings
    store.close()


def app():
    return {
        "uuid": "application",
        "repository": "https://github.com/org/site.git",
        "branch": "main",
        "enabled": True,
        "server_id": 0,
        "build_pack": "dockerfile",
    }


def pull(sha="a" * 40, state="open"):
    return {
        "number": 42,
        "state": state,
        "html_url": "https://github.com/org/site/pull/42",
        "head": {"sha": sha, "repo": {"full_name": "org/site"}},
        "base": {"ref": "main"},
    }


def test_create_repeat_update_restart_close_reopen(bridge):
    p, calls, _, _ = bridge

    async def scenario():
        await p.apply(app(), "org/site", "github", pull())
        assert [c[0] for c in calls] == ["GET", "POST", "PATCH", "POST"]
        assert calls[-1][2]["commit"] == "a" * 40
        await p.apply(app(), "org/site", "github", pull())
        count = len(calls)
        await p.apply(app(), "org/site", "github", pull())
        assert len(calls) == count
        resumed = Previews(p.d, "one", None)
        resumed.api = p.api
        resumed.ensure_proxy = p.ensure_proxy
        await resumed.apply(app(), "org/site", "github", pull())
        assert len(calls) == count
        await resumed.apply(app(), "org/site", "github", pull("b" * 40))
        assert calls[-1][2]["commit"] == "b" * 40
        await resumed.apply(app(), "org/site", "github", pull(state="closed"))
        assert calls[-1][:2] == ("DELETE", "/applications/application/previews/42")
        assert next(iter(resumed.records().values()))["hosts"] == []
        count = len(calls)
        await resumed.apply(app(), "org/site", "github", pull(state="closed"))
        assert len(calls) == count
        await resumed.apply(app(), "org/site", "github", pull("b" * 40))
        assert calls[-1][0] == "POST"

    asyncio.run(scenario())


def test_previews_begin_with_prs_opened_after_enabling_and_chosen_ones(bridge):
    p, calls, _, _ = bridge
    p.store.put(
        SCOPE, "application", {"factory": "one", "since": 1_800_000_000, "include": [7]}
    )

    def opened(number, when):
        return {**pull(), "number": number, "created_at": when}

    async def scenario():
        # Open before previews were enabled, and not chosen: left alone.
        await p.apply(app(), "org/site", "github", opened(42, "2026-11-01T00:00:00Z"))
        assert calls == []
        # Chosen when previews were enabled, though opened before.
        await p.apply(app(), "org/site", "github", opened(7, "2026-01-01T00:00:00Z"))
        # Opened after.
        await p.apply(app(), "org/site", "github", opened(43, "2027-01-20T00:00:00Z"))
        assert sorted(r["number"] for r in p.records().values()) == [7, 43]

    asyncio.run(scenario())


def test_forks_and_unowned_previews_are_not_modified(bridge):
    p, calls, _, _ = bridge
    pr = pull()
    pr["head"]["repo"]["full_name"] = "outsider/site"
    asyncio.run(p.apply(app(), "org/site", "github", pr))
    assert calls == []
    p.api = AsyncMock(return_value={"preview": {"uuid": "native"}})
    asyncio.run(p.apply(app(), "org/site", "github", pull()))
    p.api.assert_awaited_once_with("GET", "/applications/application/previews/42")
    assert not p.records()


def test_cleanup_failure_is_retried_and_route_stays_removed(bridge):
    p, _, _, settings = bridge

    async def scenario():
        await p.apply(app(), "org/site", "github", pull())
        original = p.api
        p.api = AsyncMock(side_effect=RuntimeError("temporarily unavailable"))
        await p.apply(app(), "org/site", "github", pull(state="closed"))
        record = next(iter(p.records().values()))
        assert record["state"] == "closing" and not record["hosts"]
        p.api = original
        p.applications = AsyncMock(return_value=[app()])
        settings["provider"] = "standin"
        await p.reconcile()
        assert next(iter(p.records().values()))["state"] == "closed"

    asyncio.run(scenario())


def test_paginated_open_list_checks_tracked_pr_before_cleanup(bridge):
    p, _, w, _ = bridge

    async def scenario():
        await p.apply(app(), "org/site", "github", pull())
        paths = []

        def github(path):
            paths.append(path)
            if "&page=1" in path:
                return [pull() | {"number": n + 100} for n in range(100)]
            if "&page=2" in path:
                return []
            return pull(state="closed")

        w.repository_settings.github = github
        result = await p.pulls(w, "github", "org/site")
        assert len(result) == 101
        assert paths[-1] == "/repos/org/site/pulls/42"

    asyncio.run(scenario())


def test_repository_selection_and_factory_boundaries(bridge):
    p, calls, w, settings = bridge
    p.applications = AsyncMock(return_value=[app()])
    p.pulls = AsyncMock(return_value=[pull()])
    settings["repositories"] = []
    asyncio.run(p.reconcile())
    assert not calls
    assert repository("https://evil.test/org/site", "github", w) is None
    assert repository("git@github.com:org/site.git", "github", w) == "org/site"
    assert (
        repository("http://local/factories/two/github/org/site.git", "standin", w)
        is None
    )
    assert (
        repository("http://local/factories/one/github/org/site.git", "standin", w)
        == "org/site"
    )


PREVIEW_COMPOSE = """
x-preview:
  login:
    email: demo@example.com
    password: PREVIEW_ADMIN_PASSWORD
services:
  web:
    environment:
      DATABASE_URL: postgres://preview:${PREVIEW_DB_PASSWORD:?}@postgres/preview
      PREVIEW_ADMIN_PASSWORD: ${PREVIEW_ADMIN_PASSWORD:?Set it}
    expose: ["3000"]
"""


def test_a_pr_that_adds_a_generated_variable_gets_it_and_shows_its_sign_in(
    bridge, tmp_path, monkeypatch
):
    p, calls, _, _ = bridge
    state = tmp_path / "preview-setup-site.json"
    state.write_text(
        json.dumps(
            {"application": "application", "variables": {"PREVIEW_DB_PASSWORD": "db"}}
        )
    )
    read = []

    async def read_compose(w, provider, repo, ref, path):
        read.append((repo, ref, path))
        return PREVIEW_COMPOSE

    monkeypatch.setattr(setup, "read_compose", read_compose)
    plain = p.api

    async def api(method, path, body=None):
        if (method, path) == ("GET", "/applications/application"):
            return {"docker_compose_location": "/compose.preview.yml"}
        return await plain(method, path, body)

    p.api = api

    async def scenario():
        await p.apply(app(), "org/site", "github", pull())
        await p.apply(app(), "org/site", "github", pull("b" * 40))

    asyncio.run(scenario())
    # The compose file is read at each PR commit, not the default branch.
    assert read == [
        ("org/site", "a" * 40, "compose.preview.yml"),
        ("org/site", "b" * 40, "compose.preview.yml"),
    ]
    envs = [c for c in calls if c[1] == "/applications/application/envs/bulk"]
    # Only the missing value, once, for both the base and preview environments.
    assert [[e["key"] for e in c[2]["data"]] for c in envs] == [
        ["PREVIEW_ADMIN_PASSWORD"],
        ["PREVIEW_ADMIN_PASSWORD"],
    ]
    assert [c[2]["data"][0]["is_preview"] for c in envs] == [False, True]
    saved = json.loads(state.read_text())["variables"]
    assert saved["PREVIEW_DB_PASSWORD"] == "db"
    password = saved["PREVIEW_ADMIN_PASSWORD"]
    assert envs[0][2]["data"][0]["value"] == password and len(password) == 48
    (record,) = p.records().values()
    assert p.sign_in(record) == ("demo@example.com", password)


def test_the_preview_comment_shows_a_declared_sign_in():
    record = {
        "application": "application",
        "state": "queued",
        "build_status": "finished",
        "sha": "a" * 40,
        "urls": ["https://preview.example.test"],
    }
    assert "Sign in" not in body_for("<!-- m -->", [record])
    shown = body_for("<!-- m -->", [{**record, "sign_in": ("demo@example.com", "pw")}])
    assert shown.endswith(
        "[Open preview](https://preview.example.test) · Sign in as `demo@example.com` with `pw`"
    )
    # Nothing to sign in to until it is ready.
    building = {
        **record,
        "build_status": "in_progress",
        "sign_in": ("demo@example.com", "pw"),
    }
    assert "Sign in" not in body_for("<!-- m -->", [building])
