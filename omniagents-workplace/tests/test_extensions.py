import base64
import json

import httpx
import httpx2
import pytest
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import signed_in
from omniagents_workplace.extensions.registry import (
    entrypoints,
    validate_endpoint,
    AppRegistry,
)
from omniagents_workplace.extensions.builtin import allowed_path
from omniagents_workplace.extensions.sandbox import app_origin, csp_header


def test_global_entrypoints_do_not_require_model_visibility():
    tools = [
        {
            "name": "open",
            "title": "Recorder",
            "_meta": {
                "ui": {"resourceUri": "ui://recorder", "visibility": ["app"]},
                "openai/ui": {"entrypoints": [{"type": "global"}]},
            },
        }
    ]
    assert entrypoints(tools)[0]["name"] == "Recorder"
    assert not entrypoints(
        [{**tools[0], "_meta": {"ui": {"resourceUri": "ui://recorder"}}}]
    )


@pytest.mark.parametrize(
    "path",
    [
        "https://example.com/workplace/api/slack/messages",
        "//example.com/workplace/api/slack/messages",
        "/workplace/api/slack/../me/tokens",
        "/workplace/api/slack/%2e%2e/me/tokens",
        "/workplace/api/slack/%252e%252e/me/tokens",
        "/workplace/api/me/tokens",
        "/workplace/api/github/repos",
    ],
)
def test_app_cannot_escape_service(path):
    assert not allowed_path("slack", path, "GET")


def test_sandbox_requires_separate_origin_and_valid_csp():
    assert app_origin("http://localhost:7300") == "http://127.0.0.1:7300"
    with pytest.raises(ValueError):
        app_origin("https://factory.example", "https://factory.example")
    with pytest.raises(ValueError):
        csp_header(
            {"resourceDomains": ["https://a.test; connect-src *"]},
            "https://factory.example",
        )
    assert "connect-src 'self' https://api.example" in csp_header(
        {"connectDomains": ["https://api.example"]}, "https://factory.example"
    )


def test_installation_credentials_are_not_discovered(workplace_server):
    w, url = workplace_server
    registry = AppRegistry(w)
    value = {
        "id": "external",
        "kind": "remote",
        "token": "private",
        "tools": [],
        "entrypoints": [],
        "enabled": True,
    }
    w.store.put("extension.installation", "external", value)
    with signed_in(w, url) as client:
        response = client.get("/workplace/api/extensions")
        assert response.status_code == 200
        assert "private" not in response.text
        assert {app["id"] for app in response.json()["apps"]} == {
            "slack",
            "github",
            "linear",
            "recorder",
            "external",
        }
        assert (
            client.put(
                "/workplace/api/extensions/slack",
                json={"enabled": False, "permissions": {}},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/workplace/api/extensions/slack/tools/call",
                json={
                    "name": "slack.request",
                    "arguments": {"path": "/workplace/api/state"},
                },
            ).status_code
            == 404
        )
    assert registry.get("slack", enabled=False)["enabled"] is False


def test_sandbox_origin_never_serves_authenticated_apis(workplace_server):
    w, url = workplace_server
    host = "localhost:" + url.rsplit(":", 1)[1]
    with signed_in(w, url) as client:
        assert (
            client.get("/workplace/api/session", headers={"Host": host}).status_code
            == 404
        )
        response = client.get("/mcp-app-sandbox", headers={"Host": host})
        assert response.status_code == 200
        assert "frame-ancestors " + url in response.headers["content-security-policy"]
        assert "noindex" in response.headers["x-robots-tag"]
        assert (
            client.get("/robots.txt", headers={"Host": host}).text
            == "User-agent: *\nDisallow: /\n"
        )


def test_builtin_bridge_reuses_user_authorization(workplace_server):
    w, url = workplace_server
    with signed_in(w, url) as client:
        response = client.post(
            "/workplace/api/extensions/slack/tools/call",
            json={
                "name": "slack.request",
                "arguments": {"path": "/workplace/api/state"},
            },
        )
        assert response.status_code == 200, response.text
        value = response.json()["structuredContent"]
        state = json.loads(base64.b64decode(value["body"]))
        assert state["me"]["username"] == "admin"
        assert state["github"] == {"users": [], "repositories": []}
        response = client.post(
            "/workplace/api/extensions/slack/tools/call",
            json={
                "name": "github.request",
                "arguments": {"path": "/workplace/api/github/repos"},
            },
        )
        assert response.status_code == 403
        response = client.post(
            "/workplace/api/extensions/recorder/tools/call",
            json={
                "name": "recorder.request",
                "arguments": {
                    "path": "/workplace/api/shared/uploads",
                    "method": "POST",
                    "body": json.dumps(
                        {"files": [{"path": "Elsewhere/a.webm", "size": 0}]}
                    ),
                },
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_builtin_is_a_real_mcp_server(workplace_server):
    w, url = workplace_server
    with signed_in(w, url) as client:
        cookie = "; ".join(k + "=" + v for k, v in client.cookies.items())
    async with httpx2.AsyncClient(headers={"Cookie": cookie}) as http:
        async with streamable_http_client(
            url + "/workplace/api/extensions/mcp", http_client=http
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert {
                    "slack.open",
                    "github.open",
                    "linear.open",
                    "recorder.open",
                } <= {t.name for t in listed.tools}
                opened = await session.call_tool("recorder.open", {})
                assert opened.structured_content["app"] == "recorder"
                assert opened.structured_content["account"]["username"] == "admin"


def test_recording_chunks_are_durable_and_finalize_once(workplace_server):
    from omniagents_workplace.extensions.recordings import Recordings

    w, url = workplace_server
    account = w.accounts.by_username("admin")
    recorder = Recordings(w)
    recording = recorder.call(
        {"action": "start", "title": "Notes", "mime": "audio/webm"}, account
    )
    args = {
        "action": "write",
        "id": recording["id"],
        "offset": 0,
        "data": base64.b64encode(b"audio").decode(),
    }
    assert recorder.call(args, account)["bytes"] == 5
    assert recorder.call(args, account)["bytes"] == 5
    assert not w.store.docs("shared.event")
    reopened = Recordings(w)
    assert reopened.call({"action": "list"}, account)["recordings"][0]["bytes"] == 5
    with pytest.raises(HTTPException):
        reopened.call({**args, "data": base64.b64encode(b"other").decode()}, account)
    completed = reopened.call({"action": "finish", "id": recording["id"]}, account)
    assert completed["status"] == "saved"
    assert (w.shared.root / completed["path"]).read_bytes() == b"audio"
    reopened.call({"action": "finish", "id": recording["id"]}, account)
    assert len(w.store.docs("shared.event")) == 1


def test_recorder_workflow_receives_only_completed_audio(workplace_server):
    from omniagents_workplace.extensions.recordings import Recordings

    w, url = workplace_server
    account = w.accounts.by_username("admin")
    recorder = Recordings(w)
    (w.shared.root / "Recordings").mkdir(exist_ok=True)
    w.shared.rule(
        {
            "name": "Transcribe",
            "folder": "Recordings",
            "instruction": "Transcribe the completed recording",
            "patterns": ["*.webm"],
            "events": ["shared.files_uploaded"],
            "recursive": True,
            "include_agent": False,
            "enabled": True,
        }
    )
    recording = recorder.call({"action": "start", "mime": "audio/webm"}, account)
    recorder.call(
        {
            "action": "write",
            "id": recording["id"],
            "offset": 0,
            "data": base64.b64encode(b"audio").decode(),
        },
        account,
    )
    assert (
        w.store._db.execute(
            "SELECT count(*) FROM factory_events WHERE provider='shared'"
        ).fetchone()[0]
        == 0
    )
    recorder.call({"action": "finish", "id": recording["id"]}, account)
    recorder.call({"action": "finish", "id": recording["id"]}, account)
    assert (
        w.store._db.execute(
            "SELECT count(*) FROM factory_events WHERE provider='shared'"
        ).fetchone()[0]
        == 1
    )


def test_apps_and_factories_get_distinct_origins(workplace_server):
    from omniagents_workplace.extensions.sandbox import isolated_origin

    w, url = workplace_server
    assert isolated_origin(url, "slack") != isolated_origin(url, "recorder")
    assert isolated_origin(url + "/factories/a", "slack") != isolated_origin(
        url + "/factories/b", "slack"
    )
    with signed_in(w, url) as client:
        apps = client.get("/workplace/api/extensions").json()["apps"]
        origins = [app["sandboxOrigin"] for app in apps]
        assert len(set(origins)) == len(origins)
        from urllib.parse import urlsplit

        host = urlsplit(origins[0]).netloc
        assert client.get("/mcp-app-sandbox", headers={"Host": host}).status_code == 200
        assert (
            client.get("/workplace/api/session", headers={"Host": host}).status_code
            == 404
        )


def test_recording_label_is_applied_before_shared_event(workplace_server):
    from omniagents_workplace.extensions.recordings import Recordings

    w, _ = workplace_server
    account = w.accounts.by_username("admin")
    recorder = Recordings(w)
    item = recorder.call({"action": "start", "title": "Friday, 7:58AM"}, account)
    recorder.call(
        {
            "action": "write",
            "id": item["id"],
            "offset": 0,
            "data": base64.b64encode(b"audio").decode(),
        },
        account,
    )
    assert not w.store.docs("shared.event")
    saved = recorder.call(
        {"action": "finish", "id": item["id"], "title": "Stakeholder interview"},
        account,
    )
    assert saved["title"] == "Stakeholder interview"
    assert saved["path"].startswith("Recordings/Stakeholder interview-")
    assert (w.shared.root / saved["path"]).read_bytes() == b"audio"
    assert saved["path"] in json.dumps(w.store.docs("shared.event"))
    assert (
        recorder.call(
            {"action": "finish", "id": item["id"], "title": "Retry"}, account
        )["path"]
        == saved["path"]
    )
    assert len(w.store.docs("shared.event")) == 1
