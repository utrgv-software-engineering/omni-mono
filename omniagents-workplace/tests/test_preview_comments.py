import asyncio
import json
from types import SimpleNamespace

import httpx

from omniagents_workplace.deployments import comments
from omniagents_workplace.github.state import GitHubState
from omniagents_workplace.store import Store


def setup(tmp_path, provider):
    store = Store(tmp_path / "state.db")
    record = dict(
        application="app",
        repository="org/site",
        number=1,
        provider=provider,
        state="queued",
        build_status="queued",
        sha="a" * 40,
        urls=["https://preview.example.test"],
    )
    w = SimpleNamespace(
        repository_settings=SimpleNamespace(
            settings=lambda: {"provider": provider}, token=lambda: "test-token"
        )
    )
    p = SimpleNamespace(
        factory_id="factory",
        store=store,
        sign_in=lambda record: None,
        records=lambda: {"a": record},
        i=SimpleNamespace(instances={"factory": (w, None)}),
    )
    return p, w, record


def test_standin_updates_one_comment_and_recovers_after_lost_save(tmp_path):
    p, w, record = setup(tmp_path, "standin")
    w.github = GitHubState(tmp_path / "git", "example.test")
    w.github.add_user("org")
    w.github.add_user("author")
    repo = w.github.create_repo("org", "site", "author")
    issue = w.github.create_issue(repo, "author", "PR")

    async def scenario():
        await comments.publish(p)
        assert len(issue.comments) == 1
        first = issue.comments[0].id
        for status, label in [
            ("in_progress", "Building"),
            ("finished", "Ready"),
            ("failed", "Failed"),
        ]:
            record["build_status"] = status
            await comments.publish(p)
            assert label in issue.comments[0].body
        # Reconciliation after a crash between writing GitHub and saving SQLite.
        p.store.delete_kind(comments.KIND)
        await comments.publish(p)
        assert len(issue.comments) == 1 and issue.comments[0].id == first
        record.update(state="closed", urls=[])
        await comments.publish(p)
        assert (
            "Removed" in issue.comments[0].body
            and "Open preview" not in issue.comments[0].body
        )
        await comments.publish(p)
        assert len(issue.comments) == 1

    asyncio.run(scenario())
    p.store.close()


def test_github_pagination_author_check_retry_and_no_duplicate(tmp_path, monkeypatch):
    p, w, record = setup(tmp_path, "github")
    posted = []
    writes = []
    fail = [True]

    def handle(request):
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 10})
        if request.method == "GET":
            if request.url.params["page"] == "1":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": i,
                            "user": {"id": 99},
                            "body": posted[0]["body"] if posted else "unrelated",
                        }
                        for i in range(100)
                    ],
                )
            return httpx.Response(200, json=posted)
        writes.append(request.method)
        body = json.loads(request.content)["body"]
        if request.method == "POST":
            posted.append({"id": 500, "user": {"id": 10}, "body": body})
            if fail[0]:
                fail[0] = False
                raise httpx.ReadTimeout("response lost", request=request)
        else:
            posted[0]["body"] = body
        return httpx.Response(200, json=posted[0])

    client = httpx.AsyncClient
    monkeypatch.setattr(
        comments.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(handle), **kw),
    )

    async def scenario():
        await comments.publish(p)
        assert dict(p.store.docs(comments.KIND)).popitem()[1]["error"]
        await comments.publish(p)
        assert writes == ["POST"]  # Recover the accepted comment after a timeout.
        record["build_status"] = "finished"
        await comments.publish(p)
        assert writes == ["POST", "PATCH"]
        assert "[Open preview]" in posted[0]["body"]
        await comments.publish(p)
        assert writes == ["POST", "PATCH"]
        w.repository_settings.settings = lambda: {"provider": "standin"}
        record["state"] = "closed"
        await comments.publish(p)
        assert writes == ["POST", "PATCH"]  # Never cross providers.

    asyncio.run(scenario())
    p.store.close()


def test_multiple_applications_share_one_pr_comment(tmp_path):
    p, w, record = setup(tmp_path, "standin")
    w.github = GitHubState(tmp_path / "git", "example.test")
    w.github.add_user("org")
    w.github.add_user("author")
    repo = w.github.create_repo("org", "site", "author")
    issue = w.github.create_issue(repo, "author", "PR")
    other = dict(record, application="second", build_status="finished")
    p.records = lambda: {"a": record, "b": other}
    asyncio.run(comments.publish(p))
    assert len(issue.comments) == 1
    assert "Queued" in issue.comments[0].body and "Ready" in issue.comments[0].body
    p.store.close()
