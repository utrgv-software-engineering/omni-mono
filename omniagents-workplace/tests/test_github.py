import base64
import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import _free_port, factory_env, signed_in

TOOLSETS = {"X-MCP-Toolsets": "default,actions"}


def git(*args, cwd=None):
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Acme Factory",
        "GIT_AUTHOR_EMAIL": "acme-factory@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Acme Factory",
        "GIT_COMMITTER_EMAIL": "acme-factory@users.noreply.github.com",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


async def _mcp(base_url, calls, token=None, headers=TOOLSETS):
    token = token or factory_env(base_url)["GITHUB_MCP_TOKEN"]
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}", **headers})
    async with http, streamable_http_client(
        f"{base_url}/github/mcp", http_client=http
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            results = [await session.initialize()]
            for name, args in calls:
                results.append(
                    await session.list_tools()
                    if name == "tools/list"
                    else await session.call_tool(name, args)
                )
            return results


def _text(result):
    return result.content[0].text


def _json(result):
    assert not result.isError, _text(result)
    return json.loads(_text(result))


def _repos(workplace, base_url):
    with signed_in(workplace, base_url) as ui:
        return {
            r["full_name"]: r
            for r in ui.get("/workplace/api/github/repos").json()["repositories"]
        }


# -- git ----------------------------------------------------------------------


def test_a_sandbox_clones_and_pushes_with_its_github_remote(workplace_server, tmp_path):
    workplace, base_url = workplace_server
    # What a factory's sandbox does: github.com rewritten to the workplace,
    # a blobless clone, a branch pushed back.
    rewrite = ["-c", f"url.{base_url}/github/.insteadOf=https://github.com/"]
    git(
        *rewrite,
        "clone",
        "--filter=blob:none",
        "https://github.com/acme/api-service.git",
        "repo",
        cwd=tmp_path,
    )
    repo = tmp_path / "repo"
    assert "Acme's task tracker" in (repo / "README.md").read_text()
    assert (
        git("remote", "get-url", "origin", cwd=repo).strip()
        == "https://github.com/acme/api-service.git"
    )

    (repo / "NOTES.md").write_text("notes\n")
    git("checkout", "-b", "factory/notes", cwd=repo)
    git("add", "NOTES.md", cwd=repo)
    git("commit", "-m", "Add notes", cwd=repo)
    # A push needs a token, which the sandbox's git sends as a header.
    with pytest.raises(subprocess.CalledProcessError) as refused:
        git(*rewrite, "push", "origin", "factory/notes", cwd=repo)
    # (git is asked for credentials, and has none to give.)
    assert "could not read Username" in refused.value.stderr
    basic = base64.b64encode(
        f"x-access-token:{factory_env(base_url)['GITHUB_MCP_TOKEN']}".encode()
    ).decode()
    authed = [
        *rewrite,
        "-c",
        f"http.{base_url}/github/.extraHeader=Authorization: Basic {basic}",
    ]
    git(*authed, "push", "origin", "factory/notes", cwd=repo)
    # main is protected: changes reach it through pull requests.
    main = workplace.github.repos["acme/api-service"].bare.ref_sha("main")
    with pytest.raises(subprocess.CalledProcessError) as protected:
        git(*authed, "push", "origin", "factory/notes:main", cwd=repo)
    assert (
        "GH006: Protected branch update failed for refs/heads/main"
        in protected.value.stderr
    )

    branches = {
        b["name"]: b["subject"]
        for b in _repos(workplace, base_url)["acme/api-service"]["branches"]
    }
    assert branches == {"main": "Initial commit", "factory/notes": "Add notes"}
    titles = [(e.title, e.status) for e in workplace.wire.recent(50) if e.kind == "git"]
    # A blobless clone fetches the files it checks out in a second exchange.
    assert titles[0] == ("git clone or fetch", "ok")
    assert ("git push", "ok") in titles and titles[-1] == ("git push", "error")
    assert workplace.github.repos["acme/api-service"].bare.ref_sha("main") == main


def test_unknown_repositories_are_not_found(workplace_server):
    _, base_url = workplace_server
    response = httpx.get(
        f"{base_url}/github/acme/nope.git/info/refs?service=git-upload-pack"
    )
    assert response.status_code == 404


def test_clones_at_once_all_succeed(workplace_server, tmp_path):
    # A sandbox clones its repositories in parallel.
    _, base_url = workplace_server
    rewrite = ["-c", f"url.{base_url}/github/.insteadOf=https://github.com/"]
    names = ["api-service", "webapp", "ios-app"] * 3

    def clone(i_name):
        i, name = i_name
        git(
            *rewrite,
            "clone",
            "--filter=blob:none",
            f"https://github.com/acme/{name}.git",
            f"{name}-{i}",
            cwd=tmp_path,
        )

    with ThreadPoolExecutor(len(names)) as pool:
        list(pool.map(clone, enumerate(names)))
    assert all(
        (tmp_path / f"{n}-{i}" / "README.md").is_file() for i, n in enumerate(names)
    )


def test_an_apps_settings_name_where_the_factory_reaches_github(workplace_server):
    workplace, base_url = workplace_server
    with signed_in(workplace, base_url) as ui:
        [settings] = ui.get("/workplace/api/apps").json()["apps"]
    port = base_url.rsplit(":", 1)[1]
    assert (
        settings["env"]["GITHUB_GIT_URL"]
        == f"http://host.docker.internal:{port}/github"
    )
    assert settings["env"]["GITHUB_MCP_URL"] == f"{base_url}/github/mcp"
    assert (
        settings["env"]["GITHUB_MCP_TOKEN"]
        == workplace.github.users["acme-factory"].token
    )
    assert settings["env"]["GITHUB_MCP_TOKEN"].startswith("ghp_")


# -- MCP ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_serves_githubs_tools_by_toolset(workplace_server):
    _, base_url = workplace_server
    init, tools = await _mcp(base_url, [("tools/list", None)])
    assert init.serverInfo.name == "github-mcp-server"
    assert "search_issues" in init.instructions
    assert len(tools.tools) == 50
    _, default = await _mcp(base_url, [("tools/list", None)], headers={})
    assert "actions_list" not in {t.name for t in default.tools}
    _, readonly = await _mcp(
        base_url, [("tools/list", None)], headers={**TOOLSETS, "X-MCP-Readonly": "true"}
    )
    assert {t.name for t in readonly.tools} >= {"issue_read", "pull_request_read"}
    assert "issue_write" not in {t.name for t in readonly.tools}
    with pytest.raises(Exception):
        await _mcp(base_url, [("tools/list", None)], token="ghp_nobody")


@pytest.mark.asyncio
async def test_mcp_reads_issues_as_github_answers(workplace_server):
    _, base_url = workplace_server
    _, me, issue, listed, missing, searched = await _mcp(
        base_url,
        [
            ("get_me", {}),
            (
                "issue_read",
                {
                    "method": "get",
                    "owner": "acme",
                    "repo": "api-service",
                    "issue_number": 12,
                },
            ),
            ("list_issues", {"owner": "acme", "repo": "api-service"}),
            (
                "issue_read",
                {
                    "method": "get",
                    "owner": "acme",
                    "repo": "api-service",
                    "issue_number": 99,
                },
            ),
            (
                "search_issues",
                {"query": "pagination", "owner": "acme", "repo": "api-service"},
            ),
        ],
    )
    assert _json(me)["login"] == "acme-factory"
    got = _json(issue)
    assert got["title"] == "Add pagination to GET /tasks"
    assert got["user"]["login"] == "priya"
    assert got["state"] == "open" and got["assignees"] == []
    assert got["html_url"] == f"{base_url}/acme/api-service/issues/12"
    assert got["closed_by_pull_requests"] == {"total_count": 0, "references": []}
    page = _json(listed)
    assert [i["number"] for i in page["issues"]] == [14, 12]
    assert page["issues"][0]["state"] == "OPEN" and page["totalCount"] == 2
    assert missing.isError
    assert _text(missing) == (
        f"failed to get issue: GET {base_url}/github/api/repos/acme/api-service/issues/99: "
        "404 Not Found []"
    )
    assert [i["number"] for i in _json(searched)["items"]] == [12]


@pytest.mark.asyncio
async def test_mcp_files_branches_and_a_pull_request(workplace_server):
    workplace, base_url = workplace_server
    repo = {"owner": "acme", "repo": "api-service"}
    results = await _mcp(
        base_url,
        [
            ("get_file_contents", {**repo, "path": "README.md"}),
            ("get_file_contents", {**repo, "path": "/"}),
            (
                "create_branch",
                {**repo, "branch": "factory/12-pagination", "from_branch": "main"},
            ),
            (
                "push_files",
                {
                    **repo,
                    "branch": "factory/12-pagination",
                    "message": "Paginate GET /tasks (#12)",
                    "files": [
                        {"path": "docs/pagination.md", "content": "# Pagination\n"}
                    ],
                },
            ),
            (
                "create_pull_request",
                {
                    **repo,
                    "title": "Paginate GET /tasks",
                    "head": "factory/12-pagination",
                    "base": "main",
                    "body": "Closes #12.",
                },
            ),
            (
                "issue_write",
                {
                    **repo,
                    "method": "update",
                    "issue_number": 15,
                    "labels": ["factory:Acme"],
                },
            ),
            ("pull_request_read", {**repo, "method": "get", "pullNumber": 15}),
            ("pull_request_read", {**repo, "method": "get_files", "pullNumber": 15}),
            ("pull_request_read", {**repo, "method": "get_diff", "pullNumber": 15}),
            (
                "pull_request_review_write",
                {
                    **repo,
                    "method": "create",
                    "pullNumber": 15,
                    "event": "APPROVE",
                    "body": "ok",
                },
            ),
            (
                "create_pull_request",
                {
                    **repo,
                    "title": "again",
                    "head": "factory/12-pagination",
                    "base": "main",
                },
            ),
        ],
    )
    readme, root, branch, pushed, created, labeled, pr, files, diff, approve, again = (
        results[1:]
    )
    assert _text(readme).startswith("successfully downloaded text file (SHA: ")
    assert (
        str(readme.content[1].resource.uri)
        == "repo://acme/api-service/refs/heads/main/contents/README.md"
    )
    assert "Acme's task tracker" in readme.content[1].resource.text
    assert {e["name"] for e in _json(root)} >= {"README.md", "app", "tests", "docs"}
    assert _json(branch)["ref"] == "refs/heads/factory/12-pagination"
    assert _json(pushed)["object"]["sha"] == workplace.github.repos[
        "acme/api-service"
    ].bare.ref_sha("factory/12-pagination")
    assert _json(created) == {
        "id": _json(created)["id"],
        "url": f"{base_url}/acme/api-service/pull/15",
    }
    assert _json(labeled)["url"].endswith("/pull/15")
    got = _json(pr)
    assert (
        got["head"]["ref"],
        got["base"]["ref"],
        got["labels"],
        got["changed_files"],
    ) == ("factory/12-pagination", "main", ["factory:Acme"], 1)
    assert _json(files) == [
        {
            "filename": "docs/pagination.md",
            "status": "added",
            "additions": 1,
            "changes": 1,
            "patch": "@@ -0,0 +1 @@\n+# Pagination",
        }
    ]
    assert "+++ b/docs/pagination.md" in _text(diff)
    # GitHub refuses an approval from the pull request's own author.
    assert (
        approve.isError
        and _text(approve) == "Review Can not approve your own pull request"
    )
    assert (
        again.isError
        and "A pull request already exists for acme:factory/12-pagination."
        in _text(again)
    )


@pytest.mark.asyncio
async def test_mcp_review_threads_and_comments(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    state.create_branch(repo, "dana", "dana/typo", "main")
    state.write_files(
        repo,
        "dana",
        "dana/typo",
        [("README.md", b"# api-service\n\nFixed.\n")],
        "Fix README",
    )
    pr = state.create_pull(repo, "dana", "Fix README", "dana/typo", "main", "Typo.")
    args = {"owner": "acme", "repo": "api-service", "pullNumber": pr.number}
    results = await _mcp(
        base_url,
        [
            ("pull_request_review_write", {**args, "method": "create"}),
            (
                "add_comment_to_pending_review",
                {
                    **args,
                    "path": "README.md",
                    "body": "Say what was fixed.",
                    "line": 3,
                    "side": "RIGHT",
                    "subjectType": "LINE",
                },
            ),
            (
                "add_comment_to_pending_review",
                {
                    **args,
                    "path": "README.md",
                    "body": "x",
                    "line": 40,
                    "side": "RIGHT",
                    "subjectType": "LINE",
                },
            ),
            (
                "pull_request_review_write",
                {
                    **args,
                    "method": "submit_pending",
                    "event": "REQUEST_CHANGES",
                    "body": "One change.",
                },
            ),
            ("pull_request_read", {**args, "method": "get_reviews"}),
            ("pull_request_read", {**args, "method": "get_review_comments"}),
            (
                "add_issue_comment",
                {
                    "owner": "acme",
                    "repo": "api-service",
                    "issue_number": pr.number,
                    "body": "Reviewed.",
                },
            ),
        ],
    )
    created, commented, bad_line, submitted, reviews, threads, comment = results[1:]
    assert _text(created) == "pending pull request created"
    assert (
        _text(commented)
        == "pull request review comment successfully added to pending review"
    )
    assert (
        bad_line.isError
        and "The line number doesn't exist in the pull request diff" in _text(bad_line)
    )
    assert _text(submitted) == "pending pull request review successfully submitted"
    [review] = _json(reviews)
    assert (review["state"], review["user"]["login"], review["body"]) == (
        "CHANGES_REQUESTED",
        "acme-factory",
        "One change.",
    )
    [thread] = _json(threads)["review_threads"]
    assert (
        thread["comments"][0]["author"] == "acme-factory"
        and thread["comments"][0]["line"] == 3
    )
    assert _json(comment)["url"].startswith(
        f"{base_url}/acme/api-service/pull/{pr.number}#issuecomment-"
    )


@pytest.mark.asyncio
async def test_a_pull_requests_workflow_fails_and_its_logs_read_back(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    # The repository's tests workflow runs its tests, on this machine; this pull request breaks one.
    broken = b"import unittest\n\n\nclass Broken(unittest.TestCase):\n    def test_count(self):\n        self.assertEqual(3, 10)\n"
    state.create_branch(repo, "dana", "dana/x", "main")
    state.write_files(repo, "dana", "dana/x", [("tests/test_broken.py", broken)], "x")
    pr = state.create_pull(repo, "dana", "X", "dana/x", "main")
    run = next(r for r in reversed(repo.runs) if r.event == "pull_request")
    for _ in range(600):
        if run.status == "completed":
            break
        time.sleep(0.05)
    assert run.conclusion == "failure"
    args = {"owner": "acme", "repo": "api-service"}
    checks, runs, logs = (
        await _mcp(
            base_url,
            [
                (
                    "pull_request_read",
                    {**args, "method": "get_check_runs", "pullNumber": pr.number},
                ),
                ("actions_list", {**args, "method": "list_workflow_runs"}),
                (
                    "get_job_logs",
                    {
                        **args,
                        "run_id": run.id,
                        "failed_only": True,
                        "return_content": True,
                    },
                ),
            ],
        )
    )[1:]
    assert _json(checks)["check_runs"][0]["conclusion"] == "failure"
    assert _json(runs)["workflow_runs"][0]["pull_requests"] == [pr.number]
    failed = _json(logs)
    assert failed["failed_jobs"] == 1
    assert "AssertionError: 3 != 10" in failed["logs"][0]["logs_content"]


# -- people, through the UI's API -------------------------------------------------


def test_people_review_and_merge_and_the_linked_issue_closes(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    state.create_branch(repo, "acme-factory", "factory/12", "main")
    state.write_files(repo, "acme-factory", "factory/12", [("docs/x.md", b"x\n")], "x")
    pr = state.create_pull(
        repo, "acme-factory", "Paginate", "factory/12", "main", "Closes #12."
    )
    api = "/workplace/api/github/repos/acme/api-service"
    ui = signed_in(workplace, base_url, "priya")
    blocked = ui.post(f"{api}/pulls/{pr.number}/merge", json={})
    assert blocked.status_code == 409 and "approving review" in blocked.json()["detail"]
    assert (
        ui.post(
            f"{api}/pulls/{pr.number}/reviews", json={"event": "APPROVE"}
        ).status_code
        == 200
    )
    merged = ui.post(f"{api}/pulls/{pr.number}/merge", json={"method": "squash"})
    assert merged.status_code == 200
    detail = ui.get(f"{api}/issues/{pr.number}").json()
    assert (detail["merged"], detail["merged_by"]) == (True, "priya")
    assert repo.bare.blob("main", "docs/x.md") is not None
    issue = ui.get(f"{api}/issues/12").json()
    assert (issue["state"], issue["state_reason"], issue["closed_by"]) == (
        "closed",
        "completed",
        "priya",
    )


# -- webhooks ---------------------------------------------------------------------


@pytest.fixture
def receiver():
    """A factory's webhook endpoint that records what it gets."""
    received = []
    app = FastAPI()

    @app.post("/events")
    async def events(request: Request):
        received.append((dict(request.headers), await request.body()))
        return {}

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}/events", received
    server.should_exit = True
    thread.join(5)


def test_changes_reach_the_factory_as_signed_webhooks(workplace_server, receiver):
    workplace, base_url = workplace_server
    url, received = receiver
    with signed_in(workplace, base_url) as ui:
        assert (
            ui.put(
                "/workplace/api/github/webhook", json={"url": url, "secret": "s3cret"}
            ).status_code
            == 200
        )
    api = "/workplace/api/github/repos/acme/api-service"
    with signed_in(workplace, base_url, "priya") as ui:
        ui.post(
            f"{api}/issues/12/comments",
            json={"body": "@acme-factory can you take this?"},
        )
    with signed_in(workplace, base_url, "marcus") as ui:
        ui.patch(f"{api}/issues/14", json={"labels": ["needs-repro"]})
    for _ in range(200):
        if len(received) >= 2:
            break
        time.sleep(0.02)
    (h1, b1), (h2, b2) = received[:2]
    assert h1["x-github-event"] == "issue_comment"
    assert (
        h1["x-hub-signature-256"]
        == "sha256=" + hmac.new(b"s3cret", b1, hashlib.sha256).hexdigest()
    )
    comment = json.loads(b1)
    assert (
        comment["action"],
        comment["issue"]["number"],
        comment["sender"]["login"],
    ) == ("created", 12, "priya")
    assert comment["comment"]["body"] == "@acme-factory can you take this?"
    labeled = json.loads(b2)
    assert (h2["x-github-event"], labeled["action"], labeled["label"]["name"]) == (
        "issues",
        "labeled",
        "needs-repro",
    )
    assert [
        e.title
        for e in workplace.wire.recent(20)
        if e.kind == "event" and e.service == "github"
    ] == ["issue_comment.created", "issues.labeled"]


def test_an_empty_repository_takes_its_first_push_then_protects_main(
    workplace_server, tmp_path
):
    workplace, base_url = workplace_server
    workplace.github.create_repo(
        "acme", "fresh", "priya", auto_init=False, protect_default_branch=True
    )
    basic = base64.b64encode(
        f"x-access-token:{factory_env(base_url)['GITHUB_MCP_TOKEN']}".encode()
    ).decode()
    auth = ["-c", f"http.{base_url}/github/.extraHeader=Authorization: Basic {basic}"]
    work = tmp_path / "fresh"
    work.mkdir()
    git("init", "--quiet", "-b", "main", cwd=work)
    (work / "README.md").write_text("# fresh\n")
    git("add", "README.md", cwd=work)
    git("commit", "-m", "First commit", cwd=work)
    git(*auth, "push", f"{base_url}/github/acme/fresh.git", "main", cwd=work)
    assert workplace.github.repos["acme/fresh"].bare.ref_sha("main")
    (work / "README.md").write_text("# fresh, again\n")
    git("commit", "-am", "Straight to main", cwd=work)
    with pytest.raises(subprocess.CalledProcessError) as protected:
        git(*auth, "push", f"{base_url}/github/acme/fresh.git", "main", cwd=work)
    assert "GH006" in protected.value.stderr


def test_a_recent_push_is_prompted_and_becomes_a_pull_request(
    workplace_server, tmp_path
):
    workplace, base_url = workplace_server
    priya = signed_in(workplace, base_url, "priya")
    token = priya.post("/workplace/api/me/tokens/github").json()["token"]
    basic = base64.b64encode(f"priya:{token}".encode()).decode()
    auth = ["-c", f"http.{base_url}/github/.extraHeader=Authorization: Basic {basic}"]
    git("clone", f"{base_url}/github/acme/api-service.git", "repo", cwd=tmp_path)
    repo = tmp_path / "repo"
    git("checkout", "-b", "priya/notes", cwd=repo)
    (repo / "NOTES.md").write_text("notes\n")
    git("add", "NOTES.md", cwd=repo)
    git("commit", "-m", "Add notes", cwd=repo)
    git(*auth, "push", "origin", "priya/notes", cwd=repo)

    api = "/workplace/api/github/repos/acme/api-service"
    assert [
        p["branch"] for p in priya.get(f"{api}/recent-pushes").json()["pushes"]
    ] == ["priya/notes"]
    # The prompt is the pusher's.
    assert (
        signed_in(workplace, base_url, "marcus")
        .get(f"{api}/recent-pushes")
        .json()["pushes"]
        == []
    )

    compared = priya.get(
        f"{api}/compare", params={"base": "main", "head": "priya/notes"}
    ).json()
    assert [c["subject"] for c in compared["commits"]] == ["Add notes"]
    assert [f["filename"] for f in compared["files"]] == ["NOTES.md"]
    assert compared["mergeable"] and compared["existing"] is None

    made = priya.post(
        f"{api}/pulls",
        json={"title": "Add notes", "head": "priya/notes", "base": "main"},
    ).json()
    assert made["is_pull"] and made["user"] == "priya"
    assert priya.get(f"{api}/recent-pushes").json()["pushes"] == []
    assert (
        priya.get(
            f"{api}/compare", params={"base": "main", "head": "priya/notes"}
        ).json()["existing"]
        == made["number"]
    )
    same = priya.post(
        f"{api}/pulls", json={"title": "Again", "head": "priya/notes", "base": "main"}
    )
    assert same.status_code == 422 and "already exists" in same.json()["detail"]


def test_a_pull_requests_reviewers_are_requested_and_removed_from_the_ui(
    workplace_server,
):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    state.create_branch(repo, "priya", "priya/x", "main")
    state.write_files(repo, "priya", "priya/x", [("X.md", b"x\n")], "x")
    pr = state.create_pull(repo, "priya", "X", "priya/x", "main")
    ui = signed_in(workplace, base_url, "priya")
    api = f"/workplace/api/github/repos/acme/api-service/pulls/{pr.number}/reviewers"
    assert ui.put(api, json={"reviewers": ["marcus", "dana"]}).json()[
        "requested_reviewers"
    ] == ["marcus", "dana"]
    assert ui.put(api, json={"reviewers": ["dana"]}).json()["requested_reviewers"] == [
        "dana"
    ]
    # Not from the pull request's author.
    assert ui.put(api, json={"reviewers": ["dana", "priya"]}).status_code == 422


def test_an_admin_may_merge_past_a_required_review(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    state.create_branch(repo, "admin", "admin/solo", "main")
    state.write_files(repo, "admin", "admin/solo", [("SOLO.md", b"solo\n")], "solo")
    pr = state.create_pull(repo, "admin", "Solo", "admin/solo", "main")
    merge = f"/workplace/api/github/repos/acme/api-service/pulls/{pr.number}/merge"
    # Not anyone: a member is told only an admin may, and is still blocked without it.
    priya = signed_in(workplace, base_url, "priya")
    assert priya.post(merge, json={"bypass": True}).status_code == 403
    assert priya.post(merge, json={}).status_code == 409
    # The admin, author of the pull request, merges with the bypass.
    admin = signed_in(workplace, base_url)
    assert admin.post(merge, json={}).status_code == 409
    assert admin.post(merge, json={"bypass": True}).status_code == 200
    assert repo.issues[pr.number].pull.merged


def test_a_merged_branch_is_deleted_and_restored(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    state.create_branch(repo, "admin", "admin/done", "main")
    state.write_files(repo, "admin", "admin/done", [("DONE.md", b"done\n")], "done")
    pr = state.create_pull(repo, "admin", "Done", "admin/done", "main")
    admin = signed_in(workplace, base_url)
    api = "/workplace/api/github/repos/acme/api-service"
    admin.post(f"{api}/pulls/{pr.number}/merge", json={"bypass": True})
    assert admin.get(f"{api}/issues/{pr.number}").json()["head_exists"] is True

    deleted = admin.delete(f"{api}/branches/admin/done")
    assert deleted.status_code == 200
    detail = admin.get(f"{api}/issues/{pr.number}").json()
    assert detail["head_exists"] is False and repo.bare.ref_sha("admin/done") is None
    assert (
        admin.post(
            f"{api}/branches", json={"branch": "admin/done", "sha": detail["head_sha"]}
        ).status_code
        == 200
    )
    assert repo.bare.ref_sha("admin/done") == detail["head_sha"]

    # Not the default branch; and a pull request still open from a deleted branch closes.
    assert admin.delete(f"{api}/branches/main").status_code == 422
    state.create_branch(repo, "admin", "admin/open", "main")
    state.write_files(repo, "admin", "admin/open", [("OPEN.md", b"open\n")], "open")
    open_pr = state.create_pull(repo, "admin", "Open", "admin/open", "main")
    admin.delete(f"{api}/branches/admin/open")
    assert repo.issues[open_pr.number].state == "closed"


def test_a_listing_says_when_each_entry_last_changed(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    state.create_branch(repo, "admin", "admin/touch", "main")
    api_py = repo.bare.blob("main", "app/api.py")[1] + b"\n# touched\n"
    state.write_files(
        repo, "admin", "admin/touch", [("app/api.py", api_py)], "Touch the API"
    )
    ui = signed_in(workplace, base_url)
    tree = "/workplace/api/github/repos/acme/api-service/tree"
    root = {
        e["name"]: e["last_commit"]["subject"]
        for e in ui.get(tree, params={"ref": "admin/touch"}).json()["entries"]
    }
    assert root["app"] == "Touch the API" and root["README.md"] == "Initial commit"
    app = {
        e["name"]: e["last_commit"]["subject"]
        for e in ui.get(tree, params={"ref": "admin/touch", "path": "app"}).json()[
            "entries"
        ]
    }
    assert app["api.py"] == "Touch the API" and app["store.py"] == "Initial commit"


def test_the_everyday_issue_and_pull_request_actions(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    api = "/workplace/api/github/repos/acme/api-service"
    priya, marcus = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url, "marcus"
    )

    # Reactions toggle, and say which are yours.
    assert (
        priya.post(f"{api}/issues/12/reactions", json={"content": "+1"}).status_code
        == 200
    )
    issue = priya.get(f"{api}/issues/12").json()
    assert issue["reactions"] == {"+1": 1} and issue["reacted"] == ["+1"]
    assert marcus.get(f"{api}/issues/12").json()["reacted"] == []
    priya.post(f"{api}/issues/12/reactions", json={"content": "+1"})
    assert priya.get(f"{api}/issues/12").json()["reactions"] == {}
    assert (
        priya.post(f"{api}/issues/12/reactions", json={"content": "nope"}).status_code
        == 422
    )

    # Comments are edited and deleted by their author (or an admin).
    priya.post(f"{api}/issues/12/comments", json={"body": "First thoughts"})
    [comment] = [
        e
        for e in priya.get(f"{api}/issues/12").json()["timeline"]
        if e["body"] == "First thoughts"
    ]
    assert (
        marcus.patch(
            f"{api}/issues/comments/{comment['id']}", json={"body": "Hijacked"}
        ).status_code
        == 403
    )
    assert (
        priya.patch(
            f"{api}/issues/comments/{comment['id']}", json={"body": "Second thoughts"}
        ).status_code
        == 200
    )
    assert (
        priya.post(
            f"{api}/issues/comments/{comment['id']}/reactions",
            json={"content": "heart"},
        ).status_code
        == 200
    )
    [edited] = [
        e
        for e in priya.get(f"{api}/issues/12").json()["timeline"]
        if e["id"] == comment["id"]
    ]
    assert edited["body"] == "Second thoughts" and edited["reacted"] == ["heart"]
    assert marcus.delete(f"{api}/issues/comments/{comment['id']}").status_code == 403
    assert priya.delete(f"{api}/issues/comments/{comment['id']}").status_code == 200
    assert all(
        e["id"] != comment["id"]
        for e in priya.get(f"{api}/issues/12").json()["timeline"]
    )

    # Titles and descriptions are their author's (or an admin's) to edit.
    assert (
        marcus.patch(f"{api}/issues/12", json={"title": "Mine now"}).status_code == 403
    )
    assert (
        priya.patch(f"{api}/issues/12", json={"title": "Paginate GET /tasks"}).json()[
            "title"
        ]
        == "Paginate GET /tasks"
    )

    # A draft becomes ready for review, and back.
    state.create_branch(repo, "priya", "priya/wip", "main")
    state.write_files(repo, "priya", "priya/wip", [("WIP.md", b"wip\n")], "wip")
    pr = state.create_pull(repo, "priya", "WIP", "priya/wip", "main", draft=True)
    assert (
        priya.patch(f"{api}/issues/{pr.number}", json={"draft": False}).json()["draft"]
        is False
    )
    assert (
        priya.patch(f"{api}/issues/{pr.number}", json={"draft": True}).json()["draft"]
        is True
    )

    # A review thread is resolved, and unresolved.
    review = state.create_review(repo, pr, "marcus")
    thread = state.add_review_comment(
        repo, pr, review, "marcus", path="WIP.md", body="Why?", line=1, start_line=None
    )
    state.submit_review(repo, pr, "marcus", review, "COMMENT")
    assert (
        priya.put(
            f"{api}/pulls/{pr.number}/threads/{thread.id}", json={"resolved": True}
        ).status_code
        == 200
    )
    assert (
        priya.get(f"{api}/issues/{pr.number}").json()["threads"][0]["resolved"] is True
    )
    priya.put(f"{api}/pulls/{pr.number}/threads/{thread.id}", json={"resolved": False})
    assert (
        priya.get(f"{api}/issues/{pr.number}").json()["threads"][0]["resolved"] is False
    )


def test_repository_management(workplace_server):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    api = "/workplace/api/github/repos/acme/api-service"
    admin, priya = signed_in(workplace, base_url), signed_in(
        workplace, base_url, "priya"
    )

    # Branches: ahead and behind the default branch, and their pull requests.
    state.create_branch(repo, "priya", "priya/feature", "main")
    state.write_files(repo, "priya", "priya/feature", [("F.md", b"f\n")], "feature")
    pr = state.create_pull(repo, "priya", "Feature", "priya/feature", "main")
    branches = {b["name"]: b for b in priya.get(f"{api}/branches").json()["branches"]}
    assert branches["main"]["default"] and branches["main"]["protected"]
    assert (
        branches["priya/feature"]["ahead"],
        branches["priya/feature"]["behind"],
    ) == (1, 0)
    assert branches["priya/feature"]["pull"]["number"] == pr.number

    # Update branch: main moves on (a merged pull request), and the feature catches up.
    state.create_branch(repo, "priya", "priya/other", "main")
    state.write_files(repo, "priya", "priya/other", [("O.md", b"o\n")], "other")
    other = state.create_pull(repo, "priya", "Other", "priya/other", "main")
    admin.post(f"{api}/pulls/{other.number}/merge", json={"bypass": True})
    assert priya.get(f"{api}/issues/{pr.number}").json()["behind_by"] == 1
    assert priya.post(f"{api}/pulls/{pr.number}/update-branch").status_code == 200
    assert priya.get(f"{api}/issues/{pr.number}").json()["behind_by"] == 0
    assert priya.post(f"{api}/pulls/{pr.number}/update-branch").status_code == 422

    # Settings are an admin's.
    assert priya.patch(f"{api}/settings", json={"protected": []}).status_code == 403
    assert (
        admin.patch(
            f"{api}/settings", json={"protected": [], "description": "Tasks"}
        ).status_code
        == 200
    )
    assert repo.protected == [] and repo.description == "Tasks"
    admin.patch(f"{api}/settings", json={"default_branch": "priya/feature"})
    assert repo.default_branch == "priya/feature"
    assert (
        subprocess.run(
            ["git", "symbolic-ref", "HEAD"],
            cwd=repo.bare.path,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "refs/heads/priya/feature"
    )
    admin.patch(
        f"{api}/settings", json={"default_branch": "main", "protected": ["main"]}
    )

    # Labels: made, renamed (on their issues too), deleted.
    assert (
        priya.post(
            f"{api}/labels", json={"name": "perf", "color": "#ff0000"}
        ).status_code
        == 200
    )
    assert priya.post(f"{api}/labels", json={"name": "Perf"}).status_code == 422
    priya.patch(f"{api}/issues/12", json={"labels": ["perf"]})
    priya.patch(f"{api}/labels/perf", json={"name": "performance"})
    assert repo.issues[12].labels == ["performance"]
    priya.delete(f"{api}/labels/performance")
    assert repo.issues[12].labels == [] and "performance" not in repo.labels

    # Deleting a repository: an admin's, confirmed by its name.
    assert (
        admin.request("DELETE", api, json={"confirm": "api-service"}).status_code == 422
    )
    assert (
        priya.request("DELETE", api, json={"confirm": "acme/api-service"}).status_code
        == 403
    )
    assert (
        admin.request("DELETE", api, json={"confirm": "acme/api-service"}).status_code
        == 200
    )
    assert "acme/api-service" not in state.repos and not repo.bare.path.exists()
