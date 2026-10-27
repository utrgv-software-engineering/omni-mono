import asyncio
import json

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import factory_env, signed_in
from omniagents_workplace.server import Workplace, link_mentions, load_seed
from omniagents_workplace.slack.render import plain_text, to_mrkdwn
from omniagents_workplace.slack.workspace import SlackError

FACTORY = "U0ACME0FAC"
YOU = "U0ACME0PRI"  # Priya
ENG = "C0ACME0ENG"


# -- text ---------------------------------------------------------------------


def test_markdown_becomes_mrkdwn_as_slack_stores_it():
    # Captured: "Probe: can the factory add *pagination*..." reads back as _pagination_.
    assert (
        to_mrkdwn("add *pagination* to `GET /tasks`")
        == "add _pagination_ to `GET /tasks`"
    )
    assert to_mrkdwn("**bold** and ~~gone~~") == "*bold* and ~gone~"
    assert to_mrkdwn("see [the PR](https://x/1)") == "see <https://x/1|the PR>"
    assert to_mrkdwn("# Plan\n- one") == "*Plan*\n• one"
    assert to_mrkdwn("```\n**x**\n```") == "```\n**x**\n```"
    assert (
        plain_text("add _pagination_ to <https://x|link>") == "add pagination to link"
    )


def test_people_type_mentions_by_name(tmp_path):
    ws = Workplace(tmp_path, base_url="http://w", seed=load_seed()).slack
    assert link_mentions(ws, "@acme-factory see #eng, @nobody") == (
        f"<@{FACTORY}> see <#{ENG}|eng>, @nobody"
    )


# -- the workspace -------------------------------------------------------------


def test_private_conversations_are_hidden_from_non_members(tmp_path):
    ws = Workplace(tmp_path, base_url="http://w", seed=load_seed()).slack
    with pytest.raises(SlackError) as exc:
        ws.post_message("U0ACME0PRI", "C0ACME0MOB", "hi")
    assert exc.value.code == "not_in_channel"  # public to read, members post
    dm = ws.open_dm(YOU, [FACTORY])
    with pytest.raises(SlackError) as exc:
        ws.resolve_channel("U0ACME0MAR", dm.id)
    assert exc.value.code == "channel_not_found"
    assert ws.open_dm(FACTORY, [YOU]) is dm


def test_replies_join_their_thread_and_unknown_threads_fail(tmp_path):
    ws = Workplace(tmp_path, base_url="http://w", seed=load_seed()).slack
    parent = ws.post_message(YOU, ENG, "parent")
    reply = ws.post_message(FACTORY, ENG, "reply", thread_ts=parent.ts)
    nested = ws.post_message(YOU, ENG, "reply to reply", thread_ts=reply.ts)
    assert nested.thread_ts == parent.ts
    assert [m.text for m in ws.replies(ENG, parent.ts)] == ["reply", "reply to reply"]
    with pytest.raises(SlackError) as exc:
        ws.post_message(YOU, ENG, "x", thread_ts="1.000001")
    assert exc.value.code == "thread_not_found"


# -- MCP ---------------------------------------------------------------------


async def _mcp(base_url, calls, token=None):
    token = token or factory_env(base_url)["SLACK_MCP_TOKEN"]
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with http, streamable_http_client(
        f"{base_url}/slack/mcp", http_client=http
    ) as (
        read,
        write,
        _,
    ):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            results = [init]
            for name, args in calls:
                if name == "tools/list":
                    results.append(await session.list_tools())
                else:
                    results.append(await session.call_tool(name, args))
            return results


def _json(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_serves_slacks_tools_as_the_token_user(workplace_server):
    _, base_url = workplace_server
    init, tools = await _mcp(base_url, [("tools/list", None)])
    assert (init.serverInfo.name, init.serverInfo.version) == ("Slack MCP", "1.0.0")
    assert len(tools.tools) == 27
    send = next(t for t in tools.tools if t.name == "slack_send_message")
    assert f"user_id is {FACTORY}" in send.description
    assert "{{" not in json.dumps([t.model_dump() for t in tools.tools])
    assert send.annotations.title == "Send message"


@pytest.mark.asyncio
async def test_mcp_messages_read_back_in_slacks_formats(workplace_server):
    workplace, base_url = workplace_server
    _, sent = await _mcp(
        base_url,
        [
            (
                "slack_send_message",
                {"channel_id": ENG, "message": "Picking up *issue #12*."},
            )
        ],
    )
    context = _json(sent)["message_context"]
    assert context["channel_id"] == ENG
    assert _json(sent)["message_link"] == (
        f"{base_url}/archives/{ENG}/p{context['message_ts'].replace('.', '')}"
    )
    ts = context["message_ts"]
    _, reply, react, again, channel, thread, concise, missing = await _mcp(
        base_url,
        [
            (
                "slack_send_message",
                {"channel_id": ENG, "message": "PR is up.", "thread_ts": ts},
            ),
            (
                "slack_add_reaction",
                {"channel_id": ENG, "message_ts": ts, "emoji": ":eyes:"},
            ),
            (
                "slack_add_reaction",
                {"channel_id": ENG, "message_ts": ts, "emoji": "eyes"},
            ),
            ("slack_read_channel", {"channel_id": ENG, "limit": 1}),
            ("slack_read_thread", {"channel_id": ENG, "message_ts": ts}),
            (
                "slack_read_thread",
                {"channel_id": ENG, "message_ts": ts, "response_format": "concise"},
            ),
            (
                "slack_read_thread",
                {"channel_id": ENG, "message_ts": "1000000000.000001"},
            ),
        ],
    )
    assert "?thread_ts=" in _json(reply)["message_link"]
    assert _json(react) == {
        "result": "Successfully added :eyes: reaction to the message."
    }
    assert (again.isError, again.content[0].text) == (
        True,
        "execution_failed: already_reacted",
    )

    text = _json(channel)["messages"]
    assert text.startswith(
        f"Channel: #eng ({ENG})\n\n=== Message from Acme Factory <factory@acme.test> ({FACTORY}) at "
    )
    assert (
        f"Message TS: {ts}\nPicking up _issue #12_.\n*Sent using* Acme Factory\nThread: 1 replies"
        in text
    )
    assert "Reactions: eyes (1)" in text
    assert _json(channel)["pagination_info"].startswith("More messages available.")

    thread_text = _json(thread)["messages"]
    assert thread_text.startswith("=== THREAD PARENT MESSAGE ===\nFrom: Acme Factory")
    assert "=== THREAD REPLIES (1 total) ===\n\n--- Reply 1 of 1 ---" in thread_text
    assert (
        _json(thread)["pagination_info"]
        == "There are no more messages in this thread.\n"
    )
    assert _json(concise)["messages"].startswith("THREAD: Picking up _issue #12_.")
    assert (missing.isError, missing.content[0].text) == (
        True,
        "execution_failed: thread_not_found",
    )

    # What the agent did is in the workspace people see, and on the wire.
    assert [m.text for m in workplace.slack.replies(ENG, ts)] == ["PR is up."]
    calls = [e for e in workplace.wire.recent() if e.kind == "mcp"]
    assert calls[-1].title == "slack_read_thread" and calls[-1].status == "error"


@pytest.mark.asyncio
async def test_mcp_search_profile_members_and_conversations(workplace_server):
    _, base_url = workplace_server
    _, search, concise, people, profile, members, created, dm, nothing = await _mcp(
        base_url,
        [
            (
                "slack_search_public_and_private",
                {"keywords": ["pagination"], "filters": f"in:<#{ENG}>"},
            ),
            (
                "slack_search_public",
                {"keywords": ["pagination"], "response_format": "concise"},
            ),
            ("slack_search_users", {"keywords": ["priya"]}),
            ("slack_read_user_profile", {}),
            ("slack_list_channel_members", {"channel_id": "C0ACME0MOB"}),
            (
                "slack_create_conversation",
                {"channel_name": "Factory Runs", "is_private": True},
            ),
            ("slack_create_conversation", {"user_ids": [YOU]}),
            ("slack_search_channels", {"keywords": ["zzqqxx"]}),
        ],
    )
    results = _json(search)["results"]
    assert results.startswith(
        "# Search Results for: \n\n## Messages (1 results)\n### Result 1 of 1\nChannel: #eng"
    )
    assert "Reply count: 1" in results and "Context after: " in results
    assert (
        "1. #eng - Marcus Lee: Customers keep asking for pagination"
        in _json(concise)["results"]
    )
    assert "Real Name: Priya Shah" in _json(people)["results"]
    assert _json(profile)["result"].startswith(
        f"User ID: {FACTORY}\nUsername: acme-factory\n"
    )
    assert "# Channel Members (2)" in _json(members)["members"]
    assert "Type: Private Channel\nName: factory-runs" in _json(created)["result"]
    assert _json(dm)["channel_id"].startswith("D")
    assert _json(nothing)["results"] == "# Search Results for: \n\nNo results found.\n"


@pytest.mark.asyncio
async def test_mcp_refuses_unknown_tokens_and_unavailable_tools(workplace_server):
    _, base_url = workplace_server
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/slack/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Authorization": "Bearer xoxp-nope",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert response.status_code == 401
    _, canvas = await _mcp(
        base_url, [("slack_create_canvas", {"title": "x", "content": "y"})]
    )
    assert (
        canvas.isError
        and canvas.content[0].text == "execution_failed: not_available_in_workplace"
    )


# -- Web API and events ------------------------------------------------------


def test_web_api_answers_as_slack_does(workplace_server):
    workplace, base_url = workplace_server
    api = f"{base_url}/slack/api"
    env = factory_env(base_url)
    APP_TOKEN = env["SLACK_APP_TOKEN"]
    auth = {"Authorization": f"Bearer {env['SLACK_MCP_TOKEN']}"}
    assert httpx.post(f"{api}/auth.test", headers=auth).json()["user_id"] == FACTORY
    assert httpx.post(f"{api}/auth.test").json() == {"ok": False, "error": "not_authed"}
    assert httpx.post(
        f"{api}/auth.test", headers={"Authorization": f"Bearer {APP_TOKEN}"}
    ).json() == {
        "ok": False,
        "error": "not_allowed_token_type",
    }
    posted = httpx.post(
        f"{api}/chat.postMessage", headers=auth, json={"channel": ENG, "text": "hi"}
    ).json()
    assert (
        posted["ok"]
        and posted["message"]["app_id"]
        == workplace.apps.by_username("acme-factory").slack_app_id
    )
    history = httpx.post(
        f"{api}/conversations.history", headers=auth, data={"channel": ENG, "limit": 1}
    ).json()
    assert history["messages"][0]["ts"] == posted["ts"] and history["has_more"]
    assert httpx.post(
        f"{api}/conversations.history", headers=auth, data={"channel": "C404"}
    ).json() == {
        "ok": False,
        "error": "channel_not_found",
    }
    opened = httpx.post(
        f"{api}/apps.connections.open", headers={"Authorization": f"Bearer {APP_TOKEN}"}
    ).json()
    assert (
        opened["url"].startswith(f"ws://127.0.0.1:")
        and "/slack/link/?ticket=" in opened["url"]
    )


@pytest.mark.asyncio
async def test_a_mention_reaches_the_factory_over_socket_mode(workplace_server):
    from omniagents.integrations.slack import SlackConfig, SlackSocketMode

    workplace, base_url = workplace_server
    accepted = []

    async def accept(event):
        accepted.append(event)
        return True

    socket_mode = SlackSocketMode(
        SlackConfig(
            delivery="socket",
            app_token=factory_env(base_url)["SLACK_APP_TOKEN"],
            api_url=f"{base_url}/slack/api",
        ),
        accept,
    )
    socket_mode.start()
    try:
        await asyncio.wait_for(socket_mode.connected.wait(), 5)
        cookies = signed_in(workplace, base_url, "priya").cookies
        async with httpx.AsyncClient(base_url=base_url, cookies=cookies) as client:
            mention = await client.post(
                "/workplace/api/slack/messages",
                json={"channel": ENG, "text": "@acme-factory please take issue #12"},
            )
            ts = mention.json()["message"]["ts"]
            await client.post(
                "/workplace/api/slack/messages",
                json={"channel": ENG, "text": "lunch?"},
            )
        # The factory's own reply (through MCP) is delivered too, and ignored.
        await _mcp(
            base_url,
            [
                (
                    "slack_send_message",
                    {"channel_id": ENG, "message": "On it.", "thread_ts": ts},
                )
            ],
        )
        dm = workplace.slack.open_dm(YOU, [FACTORY])
        workplace.slack.post_message(YOU, dm.id, "status?")
        for _ in range(200):
            if len(accepted) >= 2:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
    finally:
        await socket_mode.stop()

    assert [(e.event, e.thread_key) for e in accepted] == [
        ("app_mention", f"T0ACME0001:{ENG}:{ts}"),
        ("message_dm", f"T0ACME0001:{dm.id}:dm"),
    ]
    assert accepted[0].payload["event"]["text"] == f"<@{FACTORY}> please take issue #12"
    events = [e for e in workplace.wire.recent() if e.kind == "event"]
    assert events and all(e.status == "ok" for e in events)
    assert {e.detail.get("delivery") for e in events} == {"socket"}


@pytest.mark.asyncio
async def test_http_delivery_is_signed_and_verifiable(workplace_server, monkeypatch):
    from fastapi import FastAPI, Request
    from omniagents.integrations.slack import verify_slack_request

    workplace, base_url = workplace_server
    received = []
    receiver = FastAPI()

    @receiver.post("/integrations/slack/events")
    async def events(request: Request):
        body = await request.body()
        received.append(
            (
                verify_slack_request(app.signing_secret, request.headers, body),
                json.loads(body),
            )
        )
        return {}

    factory = workplace.apps.by_username("acme-factory")
    workplace.apps.configure(
        factory,
        slack_delivery="http",
        slack_request_url="http://factory/integrations/slack/events",
    )
    app = workplace.slack.apps[factory.slack_app_id]
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.ASGITransport(app=receiver), **kwargs
        ),
    )
    callback = {
        "type": "event_callback",
        "event_id": "Ev1",
        "event": {"type": "message"},
    }
    assert await workplace.delivery.deliver(app, callback)
    [(verified, body)] = received
    assert verified and body["event_id"] == "Ev1"


@pytest.mark.asyncio
async def test_a_screenshot_uploads_into_a_thread(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    post = ws.post_message(
        FACTORY, "C0ACME0PRS", "Opened acme/api-service#15: Paginate GET /tasks"
    )
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    _, started = await _mcp(
        base_url,
        [
            (
                "slack_get_file_upload_url",
                {
                    "filename": "step-2.png",
                    "content_length": len(png),
                    "alt_txt": "Page 2",
                },
            )
        ],
    )
    got = _json(started)
    # The URL is the one a sandbox reaches; post the bytes where the workplace listens.
    path = got["upload_url"].split("/slack/upload/", 1)[1]
    assert got["upload_url"].startswith("http://host.docker.internal:")
    assert (
        httpx.post(f"{base_url}/slack/upload/{path}", content=png[:-1]).status_code
        == 400
    )
    assert httpx.post(f"{base_url}/slack/upload/{path}", content=png).status_code == 200
    _, done, thread, read = await _mcp(
        base_url,
        [
            (
                "slack_complete_file_upload",
                {
                    "file_id": got["file_id"],
                    "channel_id": "C0ACME0PRS",
                    "thread_ts": post.ts,
                    "initial_comment": "Page 2 of the task list.",
                },
            ),
            ("slack_read_thread", {"channel_id": "C0ACME0PRS", "message_ts": post.ts}),
            ("slack_read_file", {"file_id": got["file_id"]}),
        ],
    )
    assert _json(done)["permalink"].endswith(
        f"/slack/files/{got['file_id']}/step-2.png"
    )
    [reply] = ws.replies("C0ACME0PRS", post.ts)
    assert reply.files == [got["file_id"]] and reply.text == "Page 2 of the task list."
    assert (
        f"File: step-2.png ({got['file_id']}, image/png)" in _json(thread)["messages"]
    )
    assert _json(read)["mimeType"] == "image/png"
    # As Slack's url_private, for whoever can see it: the uploader's token, not anyone.
    url = f"{base_url}/slack/files/{got['file_id']}/step-2.png"
    assert httpx.get(url).status_code == 404
    token = factory_env(base_url)["SLACK_MCP_TOKEN"]
    assert httpx.get(url, headers={"Authorization": f"Bearer {token}"}).content == png
