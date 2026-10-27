"""Managing Linear: issues' every field, teams, cycles, projects, documents and the inbox,
from the UI and through Linear's MCP tools."""

import json

import httpx
import pytest

from conftest import signed_in
from test_linear import _json, _mcp, _text


def _ok(response: httpx.Response):
    assert response.status_code == 200, response.text
    return response.json()


def _issue(client, title, **fields):
    created = _ok(
        client.post(
            "/workplace/api/linear/issues",
            json={"team": "ENG", "title": title, **fields},
        )
    )
    return created["identifier"]


def test_an_issue_is_edited_related_planned_and_archived(workplace_server):
    workplace, base_url = workplace_server
    li = workplace.linear
    priya = signed_in(workplace, base_url, "priya")
    a, b, c = (
        _issue(priya, t) for t in ("Paginate tasks", "Cap per_page", "Document paging")
    )

    _ok(
        priya.patch(
            f"/workplace/api/linear/issues/{a}",
            json={
                "title": "Paginate GET /tasks",
                "description": "- page\n- per_page",
                "due_date": "2026-11-15",
                "estimate": 3,
                "blocks": [b],
                "related": [c],
            },
        )
    )
    _ok(priya.patch(f"/workplace/api/linear/issues/{b}", json={"parent": a}))
    _ok(priya.patch(f"/workplace/api/linear/issues/{c}", json={"duplicate_of": a}))
    detail = _ok(priya.get(f"/workplace/api/linear/issues/{a}"))
    assert (
        detail["title"] == "Paginate GET /tasks"
        and detail["description"] == "* page\n* per_page"
    )
    assert (detail["due_date"], detail["estimate"]) == ("2026-11-15", 3)
    assert [s["identifier"] for s in detail["sub_issues"]] == [b]
    assert [r["identifier"] for r in detail["relations"]["blocks"]] == [b]
    assert [r["identifier"] for r in detail["relations"]["related"]] == [c]
    assert [r["identifier"] for r in detail["relations"]["duplicates"]] == [c]
    assert (
        _ok(priya.get(f"/workplace/api/linear/issues/{b}"))["relations"]["blocked_by"][
            0
        ]["identifier"]
        == a
    )
    # An issue is not a sub-issue of its own sub-issue.
    assert (
        priya.patch(f"/workplace/api/linear/issues/{a}", json={"parent": b}).status_code
        == 422
    )

    # null clears.
    _ok(
        priya.patch(
            f"/workplace/api/linear/issues/{a}",
            json={"due_date": None, "estimate": None, "remove_blocks": [b]},
        )
    )
    issue = li.find_issue(a)
    assert issue.due_date is None and issue.estimate is None and issue.blocks == []

    _ok(priya.patch(f"/workplace/api/linear/issues/{a}", json={"archived": True}))
    assert a not in [
        i["identifier"]
        for i in _ok(priya.get("/workplace/api/linear/issues", params={"team": "ENG"}))[
            "issues"
        ]
    ]
    assert a in [
        i["identifier"]
        for i in _ok(
            priya.get(
                "/workplace/api/linear/issues", params={"team": "ENG", "archived": True}
            )
        )["issues"]
    ]
    _ok(priya.delete(f"/workplace/api/linear/issues/{c}"))
    assert li.find_issue(c) is None
    priya.close()


def test_comments_are_edited_by_their_authors_and_attachments_added(workplace_server):
    workplace, base_url = workplace_server
    priya, marcus = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url, "marcus"
    )
    a = _issue(priya, "Slow list")
    _ok(
        priya.post(
            f"/workplace/api/linear/issues/{a}/comments",
            json={"body": "Seen at 500 tasks"},
        )
    )
    [comment] = [
        c
        for c in _ok(priya.get(f"/workplace/api/linear/issues/{a}"))["comments"]
        if c["body"]
    ]
    assert (
        marcus.patch(
            f"/workplace/api/linear/comments/{comment['id']}", json={"body": "x"}
        ).status_code
        == 403
    )
    _ok(
        priya.patch(
            f"/workplace/api/linear/comments/{comment['id']}",
            json={"body": "Seen at 5000 tasks"},
        )
    )
    assert workplace.linear.comments[comment["id"]].body == "Seen at 5000 tasks"

    _ok(
        priya.post(
            f"/workplace/api/linear/issues/{a}/attachments",
            json={"title": "PR", "url": "https://example.com/pr/1"},
        )
    )
    png = b"\x89PNG\r\n\x1a\n" + bytes(16)
    uploaded = _ok(
        priya.post(
            f"/workplace/api/linear/issues/{a}/attachments",
            files={"file": ("shot.png", png, "image/png")},
        )
    )
    assert (
        priya.get(
            uploaded["url"].replace(uploaded["url"].split("/linear/")[0], base_url)
        ).content
        == png
    )
    unsigned = (
        uploaded["url"]
        .split("?")[0]
        .replace(uploaded["url"].split("/linear/")[0], base_url)
    )
    assert httpx.get(unsigned).status_code == 404 and priya.get(unsigned).content == png
    atts = _ok(priya.get(f"/workplace/api/linear/issues/{a}"))["attachments"]
    assert [x["title"] for x in atts] == ["PR", "shot.png"]
    _ok(priya.delete(f"/workplace/api/linear/attachments/{atts[0]['id']}"))
    _ok(priya.delete(f"/workplace/api/linear/comments/{comment['id']}"))
    assert comment["id"] not in workplace.linear.comments
    priya.close()
    marcus.close()


def test_a_teams_workflow_labels_members_and_cycles_are_managed(workplace_server):
    workplace, base_url = workplace_server
    li = workplace.linear
    priya = signed_in(workplace, base_url, "priya")
    team = li.find_team("ENG")
    _ok(
        priya.patch(
            "/workplace/api/linear/teams/ENG",
            json={"name": "Engineering Core", "description": "API"},
        )
    )
    assert (team.name, team.description) == ("Engineering Core", "API")

    review = _ok(
        priya.post(
            "/workplace/api/linear/teams/ENG/states",
            json={"name": "In Review", "type": "started", "color": "#0e8a16"},
        )
    )
    a = _issue(priya, "Ship it")
    _ok(priya.patch(f"/workplace/api/linear/issues/{a}", json={"state": "In Review"}))
    _ok(
        priya.patch(
            f"/workplace/api/linear/teams/ENG/states/{review['id']}",
            json={"position": 0},
        )
    )
    assert min(team.states, key=lambda s: s.position).name == "In Review"
    # A status in use goes only with somewhere for its issues to go.
    assert (
        priya.delete(
            f"/workplace/api/linear/teams/ENG/states/{review['id']}"
        ).status_code
        == 422
    )
    done = team.state("Done")
    _ok(
        priya.delete(
            f"/workplace/api/linear/teams/ENG/states/{review['id']}",
            params={"move_to": done.id},
        )
    )
    assert li.state(li.find_issue(a)).name == "Done"

    label = _ok(
        priya.post(
            "/workplace/api/linear/teams/ENG/labels",
            json={"name": "perf", "color": "#ff0000"},
        )
    )
    _ok(priya.patch(f"/workplace/api/linear/issues/{a}", json={"labels": ["perf"]}))
    _ok(
        priya.patch(
            f"/workplace/api/linear/labels/{label['id']}",
            json={"name": "performance", "retired": True},
        )
    )
    b = _issue(priya, "Another")
    assert (
        priya.patch(
            f"/workplace/api/linear/issues/{b}", json={"labels": ["performance"]}
        ).status_code
        == 422
    )
    _ok(priya.delete(f"/workplace/api/linear/labels/{label['id']}"))
    assert li.find_issue(a).label_ids == []

    marcus = li.find_user("marcus")
    _ok(priya.delete(f"/workplace/api/linear/teams/ENG/members/{marcus.id}"))
    assert marcus.id not in team.member_ids
    _ok(priya.post("/workplace/api/linear/teams/ENG/members", json={"user": marcus.id}))
    assert marcus.id in team.member_ids

    cycle = _ok(
        priya.post(
            "/workplace/api/linear/teams/ENG/cycles",
            json={"starts_at": "2026-10-19", "ends_at": "2026-11-02"},
        )
    )
    assert (
        priya.post(
            "/workplace/api/linear/teams/ENG/cycles",
            json={"starts_at": "2026-10-25", "ends_at": "2026-11-08"},
        ).status_code
        == 422
    )  # overlaps
    _ok(priya.patch(f"/workplace/api/linear/issues/{b}", json={"cycle": cycle["id"]}))
    assert [
        i["identifier"]
        for i in _ok(
            priya.get("/workplace/api/linear/issues", params={"cycle": cycle["id"]})
        )["issues"]
    ] == [b]
    _ok(priya.delete(f"/workplace/api/linear/cycles/{cycle['id']}"))
    assert li.find_issue(b).cycle_id is None
    priya.close()


def test_projects_have_milestones_updates_documents_and_progress(workplace_server):
    workplace, base_url = workplace_server
    li = workplace.linear
    priya = signed_in(workplace, base_url, "priya")
    project = _ok(
        priya.post(
            "/workplace/api/linear/projects",
            json={"name": "Paging", "teams": ["ENG"], "target_date": "2026-12-01"},
        )
    )
    assert project["identifier"].startswith("P-ENG-")
    beta = _ok(
        priya.post(
            f"/workplace/api/linear/projects/{project['id']}/milestones",
            json={"name": "Beta"},
        )
    )
    a, b = _issue(priya, "Paginate"), _issue(priya, "Cap")
    _ok(
        priya.patch(f"/workplace/api/linear/issues/{a}", json={"milestone": beta["id"]})
    )  # puts it in the project
    _ok(
        priya.patch(
            f"/workplace/api/linear/issues/{b}",
            json={"project": project["id"], "state": "Done"},
        )
    )
    detail = _ok(priya.get(f"/workplace/api/linear/projects/{project['id']}"))
    assert {i["identifier"] for i in detail["issues"]} == {a, b}
    assert detail["progress"] == 0.5 and detail["milestones"][0]["issues"] == 1
    _ok(
        priya.patch(
            f"/workplace/api/linear/projects/{project['id']}",
            json={"status": "started", "summary": "Pages"},
        )
    )
    _ok(
        priya.post(
            f"/workplace/api/linear/projects/{project['id']}/updates",
            json={"body": "Halfway", "health": "atRisk"},
        )
    )
    doc = _ok(
        priya.post(
            "/workplace/api/linear/documents",
            json={"title": "Spec", "content": "Use cursors", "project": project["id"]},
        )
    )
    _ok(
        priya.patch(
            f"/workplace/api/linear/documents/{doc['id']}",
            json={"content": "Use offsets"},
        )
    )
    detail = _ok(priya.get(f"/workplace/api/linear/projects/{project['id']}"))
    assert detail["status"] == "started" and detail["updates"][0]["health"] == "atRisk"
    assert [d["title"] for d in detail["documents"]] == ["Spec"]
    assert (
        _ok(priya.get(f"/workplace/api/linear/documents/{doc['id']}"))["content"]
        == "Use offsets"
    )

    _ok(priya.delete(f"/workplace/api/linear/projects/{project['id']}"))
    assert li.find_issue(a).project_id is None and li.find_issue(a).milestone_id is None
    assert not li.documents and not li.status_updates
    priya.close()


def test_the_inbox_hears_of_assignments_mentions_comments_and_agents(workplace_server):
    workplace, base_url = workplace_server
    li = workplace.linear
    priya, marcus = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url, "marcus"
    )
    a = _issue(priya, "Flaky test")
    _ok(
        priya.patch(
            f"/workplace/api/linear/issues/{a}",
            json={"assignee": li.find_user("marcus").id},
        )
    )
    _ok(
        priya.post(
            f"/workplace/api/linear/issues/{a}/comments",
            json={"body": "@marcus can you look?"},
        )
    )
    _ok(
        marcus.post(
            f"/workplace/api/linear/issues/{a}/comments", json={"body": "On it"}
        )
    )
    got = _ok(marcus.get("/workplace/api/linear/inbox"))["notifications"]
    assert [n["type"] for n in got] == ["issueCommentMention", "issueAssignedToYou"]
    assert [
        n["type"]
        for n in _ok(priya.get("/workplace/api/linear/inbox"))["notifications"]
    ] == ["issueNewComment"]

    first = got[0]
    _ok(marcus.patch(f"/workplace/api/linear/inbox/{first['id']}", json={"read": True}))
    assert _ok(marcus.get("/workplace/api/linear/workspace"))["unread"] == 1
    _ok(
        marcus.patch(
            f"/workplace/api/linear/inbox/{got[1]['id']}",
            json={"snoozed_until": 4102444800},
        )
    )
    assert len(_ok(marcus.get("/workplace/api/linear/inbox"))["notifications"]) == 1
    assert (
        priya.patch(
            f"/workplace/api/linear/inbox/{first['id']}", json={"read": False}
        ).status_code
        == 404
    )

    # Unsubscribed, Priya hears nothing more of it.
    _ok(priya.patch(f"/workplace/api/linear/issues/{a}", json={"subscribed": False}))
    _ok(
        marcus.post(
            f"/workplace/api/linear/issues/{a}/comments", json={"body": "Fixed"}
        )
    )
    assert len(_ok(priya.get("/workplace/api/linear/inbox"))["notifications"]) == 1
    priya.close()
    marcus.close()


def test_stopping_an_agent_session_signals_the_agent(workplace_server):
    workplace, base_url = workplace_server
    li = workplace.linear
    priya = signed_in(workplace, base_url, "priya")
    a = _issue(priya, "Do the thing")
    _ok(
        priya.patch(
            f"/workplace/api/linear/issues/{a}",
            json={"delegate": li.find_user("acme-factory").id},
        )
    )
    [session] = [s for s in li.sessions.values() if s.issue_id == li.find_issue(a).id]
    seen = []
    li.subscribe(lambda kind, data: seen.append((kind, data.get("activity"))))
    _ok(priya.post(f"/workplace/api/linear/sessions/{session.id}/stop"))
    [(kind, activity)] = [(k, act) for k, act in seen if k == "session_prompted"]
    assert activity.signal == "stop"
    detail = _ok(priya.get(f"/workplace/api/linear/issues/{a}"))
    assert detail["sessions"][0]["stopped"]
    # The agent's response lands in the session creator's inbox.
    li.add_activity(
        session, session.app_user_id, {"type": "response", "body": "Stopped."}
    )
    assert (
        _ok(priya.get("/workplace/api/linear/inbox"))["notifications"][0]["type"]
        == "agentSessionResponse"
    )
    priya.close()


@pytest.mark.asyncio
async def test_agents_plan_with_projects_milestones_cycles_and_documents(
    workplace_server,
):
    workplace, base_url = workplace_server
    li = workplace.linear
    team = li.find_team("ENG")
    li.create_cycle(team, "2026-10-19", "2099-11-02")
    results = (
        await _mcp(
            base_url,
            [
                (
                    "save_project",
                    {
                        "name": "Paging",
                        "addTeams": ["ENG"],
                        "summary": "Pages",
                        "state": "Planned",
                    },
                ),
                (
                    "save_milestone",
                    {"project": "Paging", "name": "Beta", "targetDate": "2026-11-20"},
                ),
                (
                    "save_issue",
                    {
                        "team": "ENG",
                        "title": "Paginate",
                        "project": "Paging",
                        "milestone": "Beta",
                        "cycle": "1",
                        "dueDate": "2026-11-10",
                        "estimate": 2,
                    },
                ),
                (
                    "save_document",
                    {
                        "title": "Spec",
                        "content": "Use cursors.\n\nMore later.",
                        "project": "Paging",
                    },
                ),
                (
                    "save_status_update",
                    {
                        "type": "project",
                        "project": "Paging",
                        "body": "Started",
                        "health": "onTrack",
                    },
                ),
                ("list_projects", {}),
                ("list_milestones", {"project": "Paging"}),
                ("list_cycles", {"teamId": team.id, "type": "current"}),
                ("get_status_updates", {"type": "project", "project": "Paging"}),
            ],
        )
    )[1:]
    project, milestone, issue, doc, update, projects, milestones, cycles, updates = map(
        _json, results
    )
    assert project["id"].startswith("P-ENG-") and project["status"]["type"] == "planned"
    assert project["teams"][0]["key"] == "ENG" and milestone["name"] == "Beta"
    assert (issue["project"], issue["projectMilestone"]["name"], issue["dueDate"]) == (
        "Paging",
        "Beta",
        "2026-11-10",
    )
    assert issue["cycle"]["number"] == 1 and issue["estimate"]["value"] == 2
    assert [p["name"] for p in projects["projects"]] == ["Paging"] and milestones[0][
        "progress"
    ] == 0
    assert cycles[0]["number"] == 1 and cycles[0]["issueCountHistory"] == [1]
    assert updates["statusUpdates"][0]["id"] == update["id"]

    patched, cleared, relations = (
        await _mcp(
            base_url,
            [
                (
                    "save_document",
                    {
                        "id": doc["id"],
                        "patch": [
                            {
                                "op": "replace",
                                "old_string": "cursors",
                                "new_string": "offsets",
                            }
                        ],
                    },
                ),
                (
                    "save_issue",
                    {
                        "id": issue["id"],
                        "dueDate": None,
                        "cycle": None,
                        "parentId": None,
                    },
                ),
                (
                    "save_issue",
                    {
                        "id": issue["id"],
                        "patch": [
                            {"op": "append", "text": "\n\nDone when tests pass."}
                        ],
                    },
                ),
            ],
        )
    )[1:]
    assert _json(patched)["content"].startswith("Use offsets.")
    assert _json(cleared)["dueDate"] is None and "cycle" not in _json(cleared)
    assert li.find_issue(issue["id"]).description.endswith("Done when tests pass.")

    missing = (
        await _mcp(
            base_url,
            [
                (
                    "save_document",
                    {
                        "id": doc["id"],
                        "patch": [
                            {
                                "op": "replace",
                                "old_string": "nowhere",
                                "new_string": "x",
                            }
                        ],
                    },
                )
            ],
        )
    )[1]
    assert missing.isError and "not found" in _text(missing)

    # The inbox, as the agent's own.
    factory = li.find_user("acme-factory")
    li._notify(
        [factory.id],
        "issueAssignedToYou",
        None,
        issue=li.find_issue(issue["id"]),
        text="x",
    )
    assert not li.notifications or all(
        n.user_id != factory.id for n in li.notifications.values()
    )
    priya = li.find_user("priya")
    li._notify(
        [priya.id],
        "issueAssignedToYou",
        factory.id,
        issue=li.find_issue(issue["id"]),
        text="assigned",
    )
    notes = _json((await _mcp(base_url, [("get_notifications", {})]))[1])
    assert notes["notifications"] == []  # the factory's inbox: Priya's is hers
    blocked = (
        await _mcp(
            base_url,
            [("mark_notification", {"id": next(iter(li.notifications)), "read": True})],
        )
    )[1]
    assert blocked.isError


@pytest.mark.asyncio
async def test_agents_label_and_attachment_changes_are_kept(workplace_server):
    workplace, base_url = workplace_server
    li = workplace.linear
    team = li.find_team("ENG")
    label = li.find_label("Bug", team.id)
    priya = li.find_user("priya")
    one, two, three = (
        li.create_issue(team.id, priya.id, t) for t in ("One", "Two", "Three")
    )
    results = (
        await _mcp(
            base_url,
            [
                ("save_issue_label", {"id": label.id, "name": "Defect"}),
                ("retire_issue_label", {"id": label.id}),
                (
                    "save_issue",
                    {
                        "id": one.identifier,
                        "links": [{"url": "https://example.com/pr/2", "title": "PR"}],
                        "blocks": [two.identifier],
                        "relatedTo": [three.identifier],
                    },
                ),
            ],
        )
    )[1:]
    assert _json(results[1])["retiredAt"] is not None
    _json(results[2])
    issue = one
    att = issue.attachments[-1]
    deleted = (await _mcp(base_url, [("delete_attachment", {"id": att.id})]))[1]
    assert json.loads(_text(deleted)) == {"success": True}
    assert label.name == "Defect" and att not in issue.attachments
    assert two.id in issue.blocks and three.id in issue.related
