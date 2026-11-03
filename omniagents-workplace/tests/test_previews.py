import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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
