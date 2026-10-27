import base64
import hashlib
import hmac
import json
import threading
import time
from importlib import resources

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import _free_port, factory_env, signed_in


async def _mcp(base_url, calls, token=None):
    token = token or factory_env(base_url)["LINEAR_MCP_TOKEN"]
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with http, streamable_http_client(
        f"{base_url}/linear/mcp", http_client=http
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


def _real(tool, arguments_like=None):
    """The JSON a real call of ``tool`` returned (reference/linear_mcp_responses.json)."""
    calls = json.loads(
        resources.files("omniagents_workplace")
        .joinpath("reference/linear_mcp_responses.json")
        .read_text()
    )["calls"]
    for c in calls:
        if (
            c["tool"] == tool
            and not c["isError"]
            and (arguments_like is None or arguments_like(c["arguments"]))
        ):
            return json.loads(c["content"][0]["text"])
    raise AssertionError(f"no captured call of {tool}")


@pytest.mark.asyncio
async def test_the_tools_are_linears(workplace_server):
    _, base_url = workplace_server
    init, tools = await _mcp(base_url, [("tools/list", None)])
    assert (init.serverInfo.name, init.serverInfo.version) == ("linear", "1.0.2")
    assert len(tools.tools) == 60
    with pytest.raises(Exception):
        await _mcp(base_url, [("tools/list", None)], token="lin_api_nobody")


@pytest.mark.asyncio
async def test_results_have_the_real_servers_shape(workplace_server):
    _, base_url = workplace_server
    results = await _mcp(
        base_url,
        [
            (
                "save_issue",
                {
                    "team": "Engineering",
                    "title": "Add pagination to GET /tasks",
                    "description": "GET /tasks returns every task.\n\n- Add `page`.",
                    "priority": 2,
                },
            ),
            ("get_issue", {"id": "ENG-3"}),
            ("list_issues", {"team": "Engineering", "limit": 5}),
            (
                "save_comment",
                {"issueId": "ENG-3", "body": "Triage: clear and bounded."},
            ),
            ("list_comments", {"issueId": "ENG-3"}),
            (
                "save_issue",
                {
                    "id": "ENG-3",
                    "state": "In Progress",
                    "addLabels": ["factory:Acme"],
                    "assignee": "me",
                },
            ),
            ("list_issue_statuses", {"team": "Engineering"}),
            ("get_user", {"query": "me"}),
            ("list_teams", {}),
        ],
    )
    created, got, listed, comment, comments, updated, statuses, me, teams = results[1:]
    assert list(_json(created)) == list(_real("save_issue", lambda a: "team" in a))
    assert (
        _json(created)["description"]
        == "GET /tasks returns every task.\n\n* Add `page`."
    )
    assert (
        _json(created)["url"]
        == f"{base_url}/linear/acme/issue/ENG-3/add-pagination-to-get-tasks"
    )
    assert list(_json(got)) == list(_real("get_issue", lambda a: a == {"id": "FSB-1"}))
    assert list(_json(listed)["issues"][0]) == list(_real("list_issues")["issues"][0])
    assert list(_json(comment)) == list(_real("save_comment"))
    assert list(_json(comments)["comments"][0]) == list(
        _real("list_comments")["comments"][0]
    )
    real_update = _real("save_issue", lambda a: a.get("assignee") == "me")
    assert list(_json(updated)) == list(real_update)
    assert (
        _json(updated)["status"],
        _json(updated)["labels"],
        _json(updated)["assignee"],
    ) == ("In Progress", ["factory:Acme"], "Acme Factory")
    assert [s["type"] for s in _json(statuses)] == [
        s["type"] for s in _real("list_issue_statuses")
    ]
    assert _json(me)["displayName"] == "acme-factory"
    assert list(_json(teams)["teams"][0]) == list(_real("list_teams")["teams"][0])


@pytest.mark.asyncio
async def test_failures_read_as_linears(workplace_server):
    _, base_url = workplace_server
    missing, bad_team, no_parent, no_id, bad_state = (
        await _mcp(
            base_url,
            [
                ("get_issue", {"id": "ENG-999"}),
                ("save_issue", {"team": "No Such Team", "title": "x"}),
                ("save_comment", {"body": "orphan"}),
                ("get_issue_status", {"name": "Done", "team": "Engineering"}),
                (
                    "save_issue",
                    {"team": "Engineering", "title": "x", "state": "Triage"},
                ),
            ],
        )
    )[1:]
    assert json.loads(_text(missing))["message"] == "Could not find referenced Issue."
    assert _text(bad_team) == 'Error: Could not find team "No Such Team"'
    assert _text(no_parent).startswith("Error: Provide exactly one of issueId")
    assert _text(no_id).startswith(
        "Input validation error: Invalid arguments for tool get_issue_status: id: Invalid input"
    )
    assert _text(bad_state) == 'Error: Could not find state "Triage"'
    assert all(r.isError for r in (missing, bad_team, no_parent, no_id, bad_state))


@pytest.mark.asyncio
async def test_a_file_uploads_to_an_issue(workplace_server):
    _, base_url = workplace_server
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    (prepared,) = (
        await _mcp(
            base_url,
            [
                (
                    "prepare_attachment_upload",
                    {
                        "issue": "ENG-1",
                        "filename": "step-2.png",
                        "contentType": "image/png",
                        "size": len(png),
                        "title": "Page 2",
                    },
                )
            ],
        )
    )[1:]
    slot = _json(prepared)
    path = slot["uploadUrl"].split("/linear/uploads/put/", 1)[1]
    assert (
        httpx.put(f"{base_url}/linear/uploads/put/{path}", content=png).status_code
        == 200
    )
    attached, inline = (
        await _mcp(
            base_url,
            [
                (
                    "create_attachment_from_upload",
                    {"issue": "ENG-1", "assetUrl": slot["assetUrl"], "title": "Page 2"},
                ),
                (
                    "create_attachment",
                    {
                        "issue": "ENG-1",
                        "base64Content": base64.b64encode(png).decode(),
                        "filename": "a.png",
                        "contentType": "image/png",
                        "sha256": hashlib.sha256(png).hexdigest(),
                    },
                ),
            ],
        )
    )[1:]
    url = _json(attached)["url"]
    assert httpx.get(url.replace(url.split("/linear/")[0], base_url)).content == png
    assert _json(inline)["title"] == "a.png"


@pytest.fixture
def receiver():
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


def _wait(received, n):
    for _ in range(250):
        if len(received) >= n:
            return
        time.sleep(0.02)
    raise AssertionError(f"{len(received)} deliveries, wanted {n}")


def test_delegating_starts_a_session_the_factory_reports_in(workplace_server, receiver):
    workplace, base_url = workplace_server
    url, received = receiver
    workplace.apps.configure(
        workplace.apps.by_username("acme-factory"), linear_webhook_url=url
    )
    api = "/workplace/api/linear"
    priya = signed_in(workplace, base_url, "priya")
    assert (
        priya.patch(
            f"{api}/issues/ENG-1", json={"delegate": "acme-factory"}
        ).status_code
        == 200
    )
    _wait(received, 1)
    headers, body = received[0]
    assert headers["linear-event"] == "AgentSessionEvent"
    secret = factory_env(base_url)["LINEAR_WEBHOOK_SECRET"]
    assert (
        headers["linear-signature"]
        == hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    )
    event = json.loads(body)
    assert (event["type"], event["action"]) == ("AgentSessionEvent", "created")
    session = event["agentSession"]
    assert (
        session["issue"]["identifier"],
        session["creator"]["name"],
        session["status"],
    ) == ("ENG-1", "Priya Shah", "pending")
    assert event["promptContext"].startswith(
        '<issue identifier="ENG-1">\n<title>Tasks API gets slow for big teams</title>'
    )
    assert '<team name="Engineering"/>' in event["promptContext"]

    graphql = f"{base_url}/linear/graphql"
    create = "mutation AgentActivityCreate($input: AgentActivityCreateInput!) { agentActivityCreate(input: $input) { success agentActivity { id } } }"
    ack = httpx.post(
        graphql,
        headers={
            "Authorization": f"Bearer {factory_env(base_url)['LINEAR_ACCESS_TOKEN']}"
        },
        json={
            "query": create,
            "variables": {
                "input": {
                    "agentSessionId": session["id"],
                    "content": {"type": "thought", "body": "Looking into ENG-1."},
                }
            },
        },
    )
    assert ack.json()["data"]["agentActivityCreate"]["success"] is True
    assert workplace.linear.sessions[session["id"]].status == "active"
    # Only the session's own agent reports in it.
    assert (
        httpx.post(graphql, json={"query": create, "variables": {}}).status_code == 401
    )

    # A person's reply in the session's thread prompts the agent.
    detail = priya.get(f"{api}/issues/ENG-1").json()
    [sess] = detail["sessions"]
    priya.post(
        f"{api}/issues/ENG-1/comments",
        json={"body": "Also cap per_page at 100.", "parent_id": sess["comment_id"]},
    )
    _wait(received, 2)
    prompted = json.loads(received[1][1])
    assert (
        prompted["action"] == "prompted"
        and prompted["agentActivity"]["content"]["body"] == "Also cap per_page at 100."
    )

    done = httpx.post(
        graphql,
        headers={
            "Authorization": f"Bearer {factory_env(base_url)['LINEAR_ACCESS_TOKEN']}"
        },
        json={
            "query": create,
            "variables": {
                "input": {
                    "agentSessionId": session["id"],
                    "content": {
                        "type": "response",
                        "body": "Opened acme/api-service#15.",
                    },
                }
            },
        },
    )
    assert done.status_code == 200
    detail = priya.get(f"{api}/issues/ENG-1").json()
    assert detail["sessions"][0]["status"] == "complete"
    assert [a["type"] for a in detail["sessions"][0]["activities"]] == [
        "thought",
        "prompt",
        "response",
    ]


def test_mentioning_the_agent_in_a_comment_starts_a_session(workplace_server, receiver):
    workplace, base_url = workplace_server
    url, received = receiver
    workplace.apps.configure(
        workplace.apps.by_username("acme-factory"), linear_webhook_url=url
    )
    with signed_in(workplace, base_url, "marcus") as marcus:
        marcus.post(
            "/workplace/api/linear/issues/ENG-2/comments",
            json={"body": "@acme-factory can you take a first pass at this?"},
        )
    _wait(received, 1)
    event = json.loads(received[0][1])
    assert (
        event["agentSession"]["comment"]["body"]
        == "@acme-factory can you take a first pass at this?"
    )
    assert "<primary-directive-thread" in event["promptContext"]
    assert '<user id="' in event["promptContext"]


def test_an_unacknowledged_session_goes_stale(workplace_server, monkeypatch):
    workplace, base_url = workplace_server
    issue = workplace.linear.find_issue("ENG-2")
    app = next(u for u in workplace.linear.users.values() if u.is_app)
    session = workplace.linear.start_session(issue, app.id, issue.creator_id)
    session.created_at -= 11
    workplace.linear.mark_stale()
    assert session.status == "stale"
