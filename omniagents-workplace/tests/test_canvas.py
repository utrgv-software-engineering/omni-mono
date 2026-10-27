"""Canvases: design boards people and agents make together (the Studio redesign as the fixture)."""

import json
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import assert_persisted, factory_env, signed_in
from omniagents_workplace.canvas.state import INDEX, read_canvas_dir
from omniagents_workplace.server import Workplace

FIXTURE = Path(__file__).parent / "fixtures" / "canvas" / "studio-redesign"
BOARDS = ["FHome.dc.html", "FAgents.dc.html", "FRuns.dc.html"]


async def _mcp(base_url, calls, token):
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with http, streamable_http_client(
        f"{base_url}/canvas/mcp", http_client=http
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


def _json(result):
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


def _studio(workplace, owner="priya"):
    account = workplace.accounts.by_username(owner)
    return workplace.canvas.create(
        account.id, "OmniAgents Studio redesign", files=read_canvas_dir(FIXTURE)
    )


@pytest.mark.asyncio
async def test_an_agent_reads_the_canvas_as_it_was_made(workplace_server):
    workplace, base_url = workplace_server
    canvas = _studio(workplace)
    token = factory_env(base_url)["CANVAS_MCP_TOKEN"]
    init, tools, listed, got, read = await _mcp(
        base_url,
        [
            ("tools/list", None),
            ("list_canvases", {}),
            ("get_canvas", {"canvas": canvas.id}),
            (
                "read_file",
                {"canvas": "OmniAgents Studio redesign", "paths": [INDEX, *BOARDS]},
            ),
        ],
        token,
    )
    assert init.serverInfo.name == "canvas" and len(tools.tools) == 11
    assert _json(listed)["canvases"][0]["boards"] == BOARDS
    got = _json(got)
    assert got["version"] == 1 and got["index"]["title"] == "OmniAgents Studio redesign"
    for item in _json(read)["files"]:
        assert (
            item["text"].encode() == (FIXTURE / item["path"]).read_bytes()
        )  # byte for byte


@pytest.mark.asyncio
async def test_publishing_changes_some_files_and_refuses_stale_or_broken_ones(
    workplace_server,
):
    workplace, base_url = workplace_server
    canvas = _studio(workplace)
    token = factory_env(base_url)["CANVAS_MCP_TOKEN"]
    home = (FIXTURE / "FHome.dc.html").read_text().replace("Studio", "Studio 2")
    index = json.loads((FIXTURE / INDEX).read_text())
    missing = {
        **index,
        "boards": {
            **index["boards"],
            "New.dc.html": {"x": 0, "y": 2000, "w": 1440, "h": 1040},
        },
    }
    unordered = {**missing}
    results = (
        await _mcp(
            base_url,
            [
                (
                    "publish",
                    {
                        "canvas": canvas.id,
                        "files": {"FHome.dc.html": home},
                        "base_version": 1,
                        "label": "Rename",
                    },
                ),
                (
                    "publish",
                    {
                        "canvas": canvas.id,
                        "files": {"FAgents.dc.html": "<x-dc></x-dc>"},
                        "base_version": 1,
                    },
                ),
                (
                    "publish",
                    {
                        "canvas": canvas.id,
                        "files": {INDEX: json.dumps(missing)},
                        "base_version": 2,
                    },
                ),
                (
                    "publish",
                    {
                        "canvas": canvas.id,
                        "files": {
                            INDEX: json.dumps(unordered),
                            "New.dc.html": "<x-dc></x-dc>",
                        },
                        "base_version": 2,
                    },
                ),
                (
                    "publish",
                    {
                        "canvas": canvas.id,
                        "files": {"FRuns.dc.html": None},
                        "base_version": 2,
                    },
                ),
                ("list_versions", {"canvas": canvas.id}),
            ],
            token,
        )
    )[1:]
    published, stale, no_file, no_order, removed, versions = results
    assert _json(published) == {
        "id": canvas.id,
        "version": 2,
        "changed": ["FHome.dc.html"],
        "url": f"{base_url}/canvas/{canvas.id}",
    }
    assert (
        stale.isError
        and "version 2" in stale.content[0].text
        and "merge" in stale.content[0].text
    )
    assert no_file.isError and "not a file" in no_file.content[0].text
    assert no_order.isError and '"order"' in no_order.content[0].text
    assert (
        removed.isError and "FRuns.dc.html" in removed.content[0].text
    )  # the index still lists it
    assert [v["version"] for v in _json(versions)["versions"]] == [1, 2]
    # What was not sent kept its content.
    assert (
        workplace.canvas.read(canvas, "FAgents.dc.html").content
        == (FIXTURE / "FAgents.dc.html").read_bytes()
    )
    assert (
        workplace.canvas.read(canvas, "FHome.dc.html", 1).content
        == (FIXTURE / "FHome.dc.html").read_bytes()
    )

    # A board leaves with its place in the index.
    index["boards"].pop("FRuns.dc.html")
    index["order"].remove("FRuns.dc.html")
    gone = (
        await _mcp(
            base_url,
            [
                (
                    "publish",
                    {
                        "canvas": canvas.id,
                        "base_version": 2,
                        "files": {INDEX: json.dumps(index), "FRuns.dc.html": None},
                    },
                )
            ],
            token,
        )
    )[1]
    assert _json(gone)["changed"] == ["FRuns.dc.html", INDEX]
    restored = (
        await _mcp(
            base_url, [("restore_version", {"canvas": canvas.id, "version": 1})], token
        )
    )[1]
    assert _json(restored)["version"] == 4
    assert sorted(workplace.canvas.canvases[canvas.id].files) == sorted(
        [INDEX, *BOARDS]
    )


@pytest.mark.asyncio
async def test_people_comment_on_boards_and_agents_answer(workplace_server):
    workplace, base_url = workplace_server
    canvas = _studio(workplace)
    priya = signed_in(workplace, base_url, "priya")
    token = factory_env(base_url)["CANVAS_MCP_TOKEN"]
    note = priya.post(
        f"/workplace/api/canvas/canvases/{canvas.id}/comments",
        json={
            "body": "Make the header darker",
            "board": "FHome.dc.html",
            "x": 120,
            "y": 40,
        },
    ).json()
    threads, reply, resolved, open_ = (
        await _mcp(
            base_url,
            [
                ("list_comments", {"canvas": canvas.id}),
                ("reply_comment", {"comment": note["id"], "body": "Done in version 2"}),
                ("resolve_comment", {"comment": note["id"]}),
                ("list_comments", {"canvas": canvas.id}),
            ],
            token,
        )
    )[1:]
    [thread] = _json(threads)["threads"]
    assert (thread["board"], thread["x"], thread["author"]) == (
        "FHome.dc.html",
        120,
        "Priya Shah",
    )
    assert _json(reply)["board"] == "FHome.dc.html" and _json(resolved)["resolved"]
    assert _json(open_)["threads"] == []
    detail = priya.get(f"/workplace/api/canvas/canvases/{canvas.id}").json()
    assert [c["author"] for c in detail["comments"]] == ["Priya Shah", "Acme Factory"]
    assert (
        priya.post(
            f"/workplace/api/canvas/canvases/{canvas.id}/comments",
            json={"body": "x", "board": "Nope.dc.html"},
        ).status_code
        == 422
    )
    priya.close()


def test_boards_are_served_sandboxed_to_people_who_can_see_them(workplace_server):
    workplace, base_url = workplace_server
    canvas = _studio(workplace)
    priya = signed_in(workplace, base_url, "priya")
    detail = priya.get(f"/workplace/api/canvas/canvases/{canvas.id}").json()
    assert (
        detail["files"][0]["path"] == "FAgents.dc.html"
        and detail["index"]["order"] == BOARDS
    )
    page = httpx.get(f"{base_url}{detail['view']}FHome.dc.html")
    assert (
        page.status_code == 200
        and page.content == (FIXTURE / "FHome.dc.html").read_bytes()
    )
    policy = page.headers["content-security-policy"]
    assert (
        policy.startswith("sandbox allow-scripts;") and "connect-src 'none'" in policy
    )
    runtime = httpx.get(f"{base_url}{detail['view']}support.js")
    assert runtime.status_code == 200 and b"class DCLogic" in runtime.content
    # Without the view key, nothing.
    wrong = detail["view"].replace(detail["view"].split("/")[4], "0" * 32)
    assert httpx.get(f"{base_url}{wrong}FHome.dc.html").status_code == 404
    assert httpx.get(f"{base_url}/workplace/api/canvas/canvases").status_code == 401
    assert httpx.post(f"{base_url}/canvas/mcp", json={}).status_code == 401
    priya.close()


def test_the_ui_moves_boards_renames_and_deletes(workplace_server):
    workplace, base_url = workplace_server
    canvas = _studio(workplace)
    priya, marcus = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url, "marcus"
    )
    index = priya.get(f"/workplace/api/canvas/canvases/{canvas.id}").json()["index"]
    index["boards"]["FAgents.dc.html"]["x"] = 3000
    moved = priya.post(
        f"/workplace/api/canvas/canvases/{canvas.id}/publish",
        json={
            "files": {INDEX: json.dumps(index, indent=2)},
            "base_version": 1,
            "label": "Moved",
        },
    )
    assert moved.json() == {"version": 2, "changed": [INDEX]}
    assert (
        priya.patch(
            f"/workplace/api/canvas/canvases/{canvas.id}", json={"title": "Studio"}
        ).status_code
        == 200
    )
    assert (
        workplace.canvas.index(canvas)["title"] == "Studio" and canvas.title == "Studio"
    )
    assert (
        marcus.delete(f"/workplace/api/canvas/canvases/{canvas.id}").status_code == 403
    )
    made = marcus.post(
        "/workplace/api/canvas/canvases", json={"title": "Onboarding"}
    ).json()
    assert made["version"] == 1 and made["boards"] == 0
    assert (
        priya.delete(f"/workplace/api/canvas/canvases/{canvas.id}").status_code == 200
    )
    assert [
        c["title"]
        for c in priya.get("/workplace/api/canvas/canvases").json()["canvases"]
    ] == ["Onboarding"]
    priya.close()
    marcus.close()


def test_a_seed_brings_its_canvases_and_a_restart_keeps_them(tmp_path):
    seed = {
        "slack": {
            "team": {
                "id": "T1",
                "name": "Initech",
                "domain": "initech",
                "tz": "America/Chicago",
            },
            "users": [
                {
                    "id": "U1",
                    "name": "bill",
                    "real_name": "Bill",
                    "email": "bill@initech.test",
                }
            ],
        },
        "canvases": [{"title": "Studio", "from": str(FIXTURE), "owner": "bill"}],
    }
    workplace = Workplace(
        tmp_path / "data", base_url="http://w", seed=seed, seed_dir=tmp_path
    )
    try:
        [canvas] = workplace.canvas.canvases.values()
        assert canvas.title == "OmniAgents Studio redesign" and sorted(
            canvas.files
        ) == sorted([INDEX, *BOARDS])
        assert workplace.canvas.members[canvas.owner_id].name == "Bill"
        assert_persisted(workplace)
    finally:
        workplace.close()
