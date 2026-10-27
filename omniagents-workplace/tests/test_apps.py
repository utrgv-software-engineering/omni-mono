import asyncio
import json
import subprocess
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import signed_in


async def _who(base_url, service, token):
    """Who ``token`` acts as on ``service``'s MCP server."""
    tool, args = {
        "github": ("get_me", {}),
        "linear": ("get_user", {"query": "me"}),
        "slack": ("slack_read_user_profile", {}),
    }[service]
    headers = {"Authorization": f"Bearer {token}"}
    if service == "github":
        headers["X-MCP-Toolsets"] = "default"
    http = httpx.AsyncClient(headers=headers)
    async with http, streamable_http_client(
        f"{base_url}/{service}/mcp", http_client=http
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            assert not result.isError, result.content[0].text
            return result.content[0].text


def _refused(base_url, service, token):
    try:
        asyncio.run(_who(base_url, service, token))
    except Exception:
        return True
    return False


def test_an_admin_registers_an_app_that_acts_in_every_service(workplace_server):
    workplace, base_url = workplace_server
    priya, owner = signed_in(workplace, base_url, "priya"), signed_in(
        workplace, base_url
    )
    # An app's webhooks are requests the workplace sends: only admins register apps.
    assert (
        priya.post(
            "/workplace/api/apps",
            json={"name": "Priya's Agent", "username": "priya-agent"},
        ).status_code
        == 403
    )
    made = owner.post(
        "/workplace/api/apps", json={"name": "Priya's Agent", "username": "priya-agent"}
    )
    assert made.status_code == 200, made.text
    app = made.json()
    env = app["env"]
    assert env["GITHUB_MCP_TOKEN"].startswith("ghp_") and env[
        "LINEAR_MCP_TOKEN"
    ].startswith("lin_oauth_")
    assert env["SLACK_MCP_TOKEN"].startswith("xoxp-") and env[
        "SLACK_APP_TOKEN"
    ].startswith("xapp-")
    assert env["CANVAS_MCP_TOKEN"].startswith("cnv_")
    assert (
        json.loads(asyncio.run(_who(base_url, "github", env["GITHUB_MCP_TOKEN"])))[
            "login"
        ]
        == "priya-agent"
    )
    assert (
        json.loads(asyncio.run(_who(base_url, "linear", env["LINEAR_MCP_TOKEN"])))[
            "displayName"
        ]
        == "priya-agent"
    )
    assert "priya-agent" in asyncio.run(_who(base_url, "slack", env["SLACK_MCP_TOKEN"]))

    # Its webhooks are its settings, an admin's to set; secrets are made with them.
    assert (
        priya.patch(
            f"/workplace/api/apps/{app['id']}", json={"github_webhook_url": "http://x"}
        ).status_code
        == 403
    )
    configured = owner.patch(
        f"/workplace/api/apps/{app['id']}",
        json={
            "github_webhook_url": "http://agent.test/github",
            "linear_webhook_url": "http://agent.test/linear",
        },
    ).json()
    assert configured["github"]["webhook_url"] == "http://agent.test/github"
    assert (
        configured["env"]["GITHUB_WEBHOOK_SECRET"]
        and configured["env"]["LINEAR_WEBHOOK_SECRET"]
    )

    # Others see the app, not its credentials.
    marcus = signed_in(workplace, base_url, "marcus")
    listed = {
        a["username"]: a for a in marcus.get("/workplace/api/apps").json()["apps"]
    }
    assert "env" not in listed["priya-agent"]
    assert marcus.get(f"/workplace/api/apps/{app['id']}").status_code == 403

    # A rotated token replaces the old one.
    rotated = owner.post(f"/workplace/api/apps/{app['id']}/rotate/github_token").json()
    assert rotated["env"]["GITHUB_MCP_TOKEN"] != env["GITHUB_MCP_TOKEN"]
    assert _refused(base_url, "github", env["GITHUB_MCP_TOKEN"])

    # Removing it revokes everything.
    assert owner.delete(f"/workplace/api/apps/{app['id']}").status_code == 200
    assert _refused(base_url, "github", rotated["env"]["GITHUB_MCP_TOKEN"])
    assert _refused(base_url, "slack", env["SLACK_MCP_TOKEN"])
    assert _refused(base_url, "linear", env["LINEAR_MCP_TOKEN"])
    assert workplace.canvas.member_for_token(env["CANVAS_MCP_TOKEN"]) is None
    assert not any(h.url == "http://agent.test/github" for h in workplace.github.hooks)


def test_people_make_tokens_to_connect_as_themselves(workplace_server):
    workplace, base_url = workplace_server
    dana = signed_in(workplace, base_url, "dana")
    assert dana.get("/workplace/api/me/tokens").json()["tokens"] == {
        "github": False,
        "linear": False,
        "slack": False,
        "canvas": False,
        "memory": False,
    }
    tokens = {
        s: dana.post(f"/workplace/api/me/tokens/{s}").json()["token"]
        for s in ("github", "linear", "slack")
    }
    assert (
        json.loads(asyncio.run(_who(base_url, "github", tokens["github"])))["login"]
        == "dana"
    )
    assert (
        json.loads(asyncio.run(_who(base_url, "linear", tokens["linear"])))[
            "displayName"
        ]
        == "dana"
    )
    assert "dana" in asyncio.run(_who(base_url, "slack", tokens["slack"]))
    # A personal token is not an app people see in Slack.
    assert all(
        not a["name"].startswith("Dana")
        for a in dana.get("/workplace/api/state").json()["slack"]["apps"]
    )
    dana.delete("/workplace/api/me/tokens/github")
    assert _refused(base_url, "github", tokens["github"])


def test_env_prints_what_a_factory_connects_with(workplace_server):
    workplace, base_url = workplace_server
    workplace.store.set_meta(
        "workplace.served_at",
        {"base_url": base_url, "sandbox_url": workplace.sandbox_url},
    )
    printed = subprocess.run(
        [
            sys.executable,
            "-m",
            "omniagents_workplace.cli",
            "env",
            "--data",
            str(workplace.data_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    env = dict(line[len("export ") :].split("=", 1) for line in printed.splitlines())
    expected = workplace.apps.env(workplace.apps.by_username("acme-factory"))
    assert {k: v.strip("'") for k, v in env.items()} == expected


def test_removed_apps_and_disabled_people_act_nowhere_and_are_offered_nowhere(
    workplace_server,
):
    workplace, base_url = workplace_server
    admin = signed_in(workplace, base_url)
    # A person with their own tokens, then disabled.
    dana = signed_in(workplace, base_url, "dana")
    tokens = {
        s: dana.post(f"/workplace/api/me/tokens/{s}").json()["token"]
        for s in ("github", "linear", "slack")
    }
    people = {
        p["username"]: p for p in admin.get("/workplace/api/people").json()["people"]
    }
    admin.patch(
        f"/workplace/api/people/{people['dana']['id']}", json={"disabled": True}
    )
    for service, token in tokens.items():
        assert _refused(base_url, service, token), service

    # An app, removed.
    app = admin.post(
        "/workplace/api/apps", json={"name": "Old Bot", "username": "old-bot"}
    ).json()
    admin.delete(f"/workplace/api/apps/{app['id']}")

    logins = {
        u["login"] for u in admin.get("/workplace/api/github/repos").json()["users"]
    }
    assert "dana" not in logins and "old-bot" not in logins and "acme-factory" in logins
    linear = {
        u["display_name"]
        for u in admin.get("/workplace/api/linear/workspace").json()["users"]
    }
    assert "dana" not in linear and "old-bot" not in linear and "acme-factory" in linear
    # A removed agent takes no delegation, and a mention of it starts no session.
    assert (
        admin.patch(
            "/workplace/api/linear/issues/ENG-1", json={"delegate": "old-bot"}
        ).status_code
        == 422
    )
    sessions = len(workplace.linear.sessions)
    admin.post(
        "/workplace/api/linear/issues/ENG-1/comments",
        json={"body": "@old-bot are you there?"},
    )
    assert len(workplace.linear.sessions) == sessions
    # Their history stays.
    assert (
        workplace.github.users["dana"].suspended
        and workplace.github.users["old-bot"].suspended
    )

    # Enabled again, the person acts again (with new tokens).
    admin.patch(
        f"/workplace/api/people/{people['dana']['id']}", json={"disabled": False}
    )
    assert "dana" in {
        u["login"] for u in admin.get("/workplace/api/github/repos").json()["users"]
    }
