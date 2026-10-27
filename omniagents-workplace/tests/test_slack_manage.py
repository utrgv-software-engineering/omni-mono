"""Managing Slack from the UI and the Web API: what people (and apps) change."""

import httpx

from conftest import factory_env, signed_in

ENG, GENERAL, MOBILE = "C0ACME0ENG", "C0ACME0GEN", "C0ACME0MOB"
PRIYA, MARCUS, DANA, FACTORY, ADMIN = (
    "U0ACME0PRI",
    "U0ACME0MAR",
    "U0ACME0DEV",
    "U0ACME0FAC",
    "U0ACME0ADM",
)


def _ok(response: httpx.Response):
    assert response.status_code == 200, response.text
    return response.json()


def test_people_join_leave_and_bring_others_into_channels(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    priya = signed_in(workplace, base_url, "priya")

    # #mobile is public: Priya, not in it, sees it and joins it.
    assert PRIYA not in ws.channels[MOBILE].members
    assert (
        _ok(priya.post("/workplace/api/slack/channels/C0ACME0MOB/join"))["channel"][
            "members"
        ][-1]
        == PRIYA
    )
    _ok(
        priya.post(
            "/workplace/api/slack/channels/C0ACME0MOB/members", json={"users": [MARCUS]}
        )
    )
    assert MARCUS in ws.channels[MOBILE].members
    _ok(priya.delete(f"/workplace/api/slack/channels/{MOBILE}/members/{MARCUS}"))
    assert MARCUS not in ws.channels[MOBILE].members
    assert (
        priya.delete(f"/workplace/api/slack/channels/{MOBILE}/members/{PRIYA}").json()[
            "detail"
        ]
        == "cant_kick_self"
    )
    _ok(priya.post(f"/workplace/api/slack/channels/{MOBILE}/leave"))
    assert PRIYA not in ws.channels[MOBILE].members
    assert [m.subtype for m in ws.messages(MOBILE)][-1] == "channel_leave"
    assert (
        priya.post(f"/workplace/api/slack/channels/{GENERAL}/leave").json()["detail"]
        == "cant_leave_general"
    )

    # A private channel is joined by invitation only.
    private = ws.create_channel(MARCUS, "launch", is_private=True)
    assert (
        priya.post(f"/workplace/api/slack/channels/{private.id}/join").status_code
        == 400
    )
    priya.close()


def test_a_channel_is_renamed_described_archived_and_made_default(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    priya, admin = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url, "admin"
    )
    channel = ws.create_channel(PRIYA, "launch-plan")

    _ok(
        priya.patch(
            f"/workplace/api/slack/channels/{channel.id}",
            json={"name": "launch", "topic": "Ship Friday", "purpose": "The launch"},
        )
    )
    assert (channel.name, channel.topic, channel.purpose) == (
        "launch",
        "Ship Friday",
        "The launch",
    )
    assert [m.subtype for m in ws.messages(channel.id)][-3:] == [
        "channel_name",
        "channel_topic",
        "channel_purpose",
    ]
    # Only its creator or an admin renames it.
    ws.join(channel.id, MARCUS)
    marcus = signed_in(workplace, base_url, "marcus")
    assert (
        marcus.patch(
            f"/workplace/api/slack/channels/{channel.id}", json={"name": "x"}
        ).json()["detail"]
        == "not_authorized"
    )
    # Making it private or a default channel is an admin's.
    assert (
        priya.patch(
            f"/workplace/api/slack/channels/{channel.id}", json={"default": True}
        ).status_code
        == 400
    )
    _ok(
        admin.patch(
            f"/workplace/api/slack/channels/{channel.id}", json={"default": True}
        )
    )
    assert channel.is_default
    joiner = workplace.add_person(
        username="sam", name="Sam Park", email="sam@acme.test"
    )
    assert joiner.slack_id in channel.members

    _ok(
        priya.patch(
            f"/workplace/api/slack/channels/{channel.id}", json={"archived": True}
        )
    )
    assert channel.is_archived and not channel.is_default
    assert (
        priya.post(
            "/workplace/api/slack/messages", json={"channel": channel.id, "text": "hi"}
        ).json()["detail"]
        == "is_archived"
    )
    _ok(
        priya.patch(
            f"/workplace/api/slack/channels/{channel.id}", json={"archived": False}
        )
    )
    assert not channel.is_archived
    assert (
        priya.patch(
            f"/workplace/api/slack/channels/{GENERAL}", json={"archived": True}
        ).json()["detail"]
        == "cant_archive_general"
    )
    for client in (priya, admin, marcus):
        client.close()


def test_messages_are_edited_deleted_and_pinned(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    priya, marcus = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url, "marcus"
    )
    admin = signed_in(workplace, base_url, "admin")
    posted = ws.post_message(PRIYA, ENG, "Standup at 10")

    edited = _ok(
        priya.patch(
            f"/workplace/api/slack/messages/{ENG}/{posted.ts}",
            json={"text": "Standup at 11"},
        )
    )
    assert (
        edited["message"]["text"] == "Standup at 11"
        and edited["message"]["edited"]["user"] == PRIYA
    )
    assert (
        marcus.patch(
            f"/workplace/api/slack/messages/{ENG}/{posted.ts}", json={"text": "no"}
        ).json()["detail"]
        == "cant_update_message"
    )

    _ok(marcus.post(f"/workplace/api/slack/messages/{ENG}/{posted.ts}/pin"))
    details = _ok(priya.get(f"/workplace/api/slack/channels/{ENG}"))
    assert [m["ts"] for m in details["pins"]] == [posted.ts] and details["pins"][0][
        "pinned_by"
    ] == MARCUS
    _ok(marcus.delete(f"/workplace/api/slack/messages/{ENG}/{posted.ts}/pin"))
    assert not ws.pins(ENG)

    # A message with a thread stays as a tombstone; its thread's last reply takes it along.
    reply = ws.post_message(MARCUS, ENG, "Works for me", thread_ts=posted.ts)
    assert (
        marcus.delete(f"/workplace/api/slack/messages/{ENG}/{posted.ts}").json()[
            "detail"
        ]
        == "cant_delete_message"
    )
    _ok(priya.delete(f"/workplace/api/slack/messages/{ENG}/{posted.ts}"))
    assert ws.message(ENG, posted.ts).subtype == "tombstone"
    # Admins delete anyone's.
    _ok(admin.delete(f"/workplace/api/slack/messages/{ENG}/{reply.ts}"))
    assert all(m.ts not in (posted.ts, reply.ts) for m in ws.messages(ENG))
    for client in (priya, marcus, admin):
        client.close()


def test_unread_counts_follow_what_was_read(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    priya = signed_in(workplace, base_url, "priya")
    last = ws.messages(ENG)[-1]
    _ok(priya.post(f"/workplace/api/slack/channels/{ENG}/read", json={"ts": last.ts}))
    assert _ok(priya.get("/workplace/api/state"))["slack"]["unread"][ENG] == [0, 0]
    ws.post_message(MARCUS, ENG, "Anyone free?")
    ws.post_message(MARCUS, ENG, f"<@{PRIYA}> can you look?")
    ws.post_message(PRIYA, ENG, "On it")  # your own is never unread
    assert _ok(priya.get("/workplace/api/state"))["slack"]["unread"][ENG] == [2, 1]
    _ok(
        priya.post(
            f"/workplace/api/slack/channels/{ENG}/read",
            json={"ts": ws.messages(ENG)[-1].ts},
        )
    )
    assert _ok(priya.get("/workplace/api/state"))["slack"]["unread"][ENG] == [0, 0]
    priya.close()


def test_files_are_attached_and_only_their_conversations_see_them(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    dana, priya = signed_in(workplace, base_url, "dana"), signed_in(
        workplace, base_url, "priya"
    )
    private = ws.create_channel(DANA, "ios-secret", is_private=True)
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
    shared = _ok(
        dana.post(
            "/workplace/api/slack/files",
            data={"channel": private.id, "comment": "Build"},
            files={"file": ("shot.png", png, "image/png")},
        )
    )
    message = ws.messages(private.id)[-1]
    assert message.files == [shared["file"]["id"]] and message.text == "Build"
    url = f"/slack/files/{shared['file']['id']}/shot.png"
    assert dana.get(url).content == png
    assert priya.get(url).status_code == 404  # not in the channel
    assert httpx.get(f"{base_url}{url}").status_code == 404  # no one
    token = factory_env(base_url)["SLACK_MCP_TOKEN"]
    assert (
        httpx.get(
            f"{base_url}{url}", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 404
    )
    ws.invite(DANA, private.id, [FACTORY])
    assert (
        httpx.get(
            f"{base_url}{url}", headers={"Authorization": f"Bearer {token}"}
        ).content
        == png
    )
    dana.close()
    priya.close()


def test_search_drafts_profile_and_workspace_settings(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    priya, admin = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url, "admin"
    )
    ws.post_message(PRIYA, ENG, "The pagination bug is back")
    secret = ws.create_channel(MARCUS, "secret", is_private=True)
    ws.post_message(MARCUS, secret.id, "pagination plans")
    found = _ok(priya.get("/workplace/api/slack/search", params={"q": "pagination"}))
    assert {m["channel"] for m in found["messages"]} == {
        ENG
    }  # not the private channel she is not in
    assert any(
        u["id"] == MARCUS
        for u in _ok(priya.get("/workplace/api/slack/search", params={"q": "marcus"}))[
            "people"
        ]
    )

    draft = ws.save_draft(PRIYA, ENG, "Draft from the agent")
    import time

    scheduled = ws.schedule(PRIYA, ENG, "Reminder", int(time.time()) + 3600)
    listed = _ok(priya.get("/workplace/api/slack/drafts"))
    assert [d["id"] for d in listed["drafts"]] == [draft.id] and [
        q["id"] for q in listed["scheduled"]
    ] == [scheduled.id]
    _ok(
        priya.patch(
            f"/workplace/api/slack/drafts/{draft.id}", json={"text": "Edited draft"}
        )
    )
    sent = _ok(priya.post(f"/workplace/api/slack/drafts/{draft.id}/send"))
    assert sent["message"]["text"] == "Edited draft" and draft.id not in ws.drafts
    _ok(priya.delete(f"/workplace/api/slack/scheduled/{scheduled.id}"))
    assert not ws.scheduled

    profile = _ok(
        priya.patch(
            "/workplace/api/me/profile",
            json={
                "name": "Priya S. Shah",
                "title": "Director",
                "tz": "Europe/London",
                "status_emoji": ":palm_tree:",
                "status_text": "Vacationing",
            },
        )
    )
    assert (
        profile["user"]["real_name"] == "Priya S. Shah"
        and profile["user"]["tz"] == "Europe/London"
    )
    assert profile["user"]["profile"]["status_emoji"] == ":palm_tree:"
    assert workplace.accounts.by_username("priya").name == "Priya S. Shah"
    assert workplace.github.users["priya"].name == "Priya S. Shah"
    assert (
        priya.patch(
            "/workplace/api/me/profile", json={"tz": "Mars/Olympus"}
        ).status_code
        == 400
    )

    assert priya.patch("/workplace/api/team", json={"name": "Nope"}).status_code == 403
    _ok(
        admin.patch(
            "/workplace/api/team", json={"name": "Acme Corp", "tz": "America/New_York"}
        )
    )
    assert (ws.name, ws.tz, workplace.linear.org_name) == (
        "Acme Corp",
        "America/New_York",
        "Acme Corp",
    )
    priya.close()
    admin.close()


def test_apps_manage_channels_and_messages_with_slacks_web_api(workplace_server):
    workplace, base_url = workplace_server
    ws = workplace.slack
    api = f"{base_url}/slack/api"
    auth = {"Authorization": f"Bearer {factory_env(base_url)['SLACK_MCP_TOKEN']}"}

    def call(method, **params):
        return httpx.post(f"{api}/{method}", headers=auth, json=params).json()

    posted = call("chat.postMessage", channel=ENG, text="Working on it")
    assert (
        call("chat.update", channel=ENG, ts=posted["ts"], text="Done")["text"] == "Done"
    )
    assert call("pins.add", channel=ENG, timestamp=posted["ts"])["ok"]
    assert [i["message"]["ts"] for i in call("pins.list", channel=ENG)["items"]] == [
        posted["ts"]
    ]
    bookmark = call(
        "bookmarks.add",
        channel=ENG,
        title="Runbook",
        link="https://example.com/runbook",
    )["bookmark"]
    assert call("bookmarks.list", channel=ENG)["bookmarks"][0]["id"] == bookmark["id"]
    assert (
        call("conversations.setTopic", channel=ENG, topic="Factory requests")[
            "channel"
        ]["topic"]["value"]
        == "Factory requests"
    )
    created = ws.create_channel(PRIYA, "triage")
    assert call("conversations.join", channel=created.id)["ok"]
    assert call("conversations.invite", channel=created.id, users=MARCUS)["ok"]
    assert (
        call("conversations.leave", channel=created.id)["ok"]
        and FACTORY not in created.members
    )
    assert (
        call(
            "users.profile.set",
            profile={"status_text": "Building", "status_emoji": ":construction:"},
        )["profile"]["status_text"]
        == "Building"
    )
    assert call("chat.delete", channel=ENG, ts=posted["ts"])["ok"]
    assert call("chat.delete", channel=ENG, ts=posted["ts"]) == {
        "ok": False,
        "error": "message_not_found",
    }
