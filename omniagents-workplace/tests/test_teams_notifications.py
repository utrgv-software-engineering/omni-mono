import asyncio
import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from omniagents_workplace.notifications import Notifier
from omniagents_workplace.server import create_app
from omniagents_workplace.teams_notifications import (
    TeamsNotification,
    TeamsNotifications,
)
from test_factory_runtime import _workplace


URL = "/factory/notifications/teams"
BODY = {
    "event_id": "github:pr-opened:42",
    "title": "Review complete",
    "text": "Ready for review",
    "path": "/automations/pr-opened",
}
WEBHOOK = "https://workflow.example/webhook?sig=secret-not-for-logs"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    workplace = _workplace(tmp_path, "http://runtime.invalid")
    client = TestClient(create_app(workplace, static_dir=tmp_path))
    app = workplace.apps.by_username("initech-factory")
    client.headers["Authorization"] = "Bearer " + app.memory_token
    workplace.notifier.url = WEBHOOK
    monkeypatch.setattr(
        workplace.notifier, "deliver", AsyncMock(return_value="accepted")
    )
    yield workplace, client, app
    workplace.close()


def test_factory_only_auth_and_environment(setup):
    workplace, client, app = setup
    env = workplace.apps.env(app)
    assert env["FACTORY_TEAMS_NOTIFY_URL"].endswith(URL)
    assert WEBHOOK not in json.dumps(env)
    assert "teams" not in json.dumps(workplace.factory_secrets.public()).lower()
    member_app = workplace.apps.register(
        workplace.accounts.by_username("bill"), name="Other", username="other-teams"
    )
    assert "FACTORY_TEAMS_NOTIFY_URL" not in workplace.apps.env(member_app)
    for authorization in ("", "Bearer invalid", "Bearer " + member_app.memory_token):
        assert (
            client.post(
                URL, json=BODY, headers={"Authorization": authorization}
            ).status_code
            == 401
        )
    app.removed = True
    assert client.post(URL, json=BODY).status_code == 401
    workplace.notifier.deliver.assert_not_called()


def test_card_link_deduplication_and_conflict(setup):
    workplace, client, app = setup
    response = client.post(URL, json=BODY)
    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "duplicate": False,
        "url": workplace.base_url + BODY["path"],
    }
    assert client.post(URL, json=BODY).json()["duplicate"]
    restored = TeamsNotifications(workplace)
    assert asyncio.run(restored.send(app, TeamsNotification(**BODY)))["duplicate"]
    assert client.post(URL, json={**BODY, "text": "different"}).status_code == 409
    workplace.notifier.deliver.assert_awaited_once_with(
        BODY["title"], BODY["text"], url=workplace.base_url + BODY["path"]
    )
    events = workplace.audit_log.events(event="notification.teams")
    assert len(events) == 1
    assert WEBHOOK not in json.dumps(events)
    assert BODY["text"] not in json.dumps(events)


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/a",
        "//evil.example/a",
        "/%2fexample/a",
        "/apps/../.auth/logout",
        "/apps/%252e%252e/.auth/logout",
        "/apps/\\evil",
        "/workplace/api/desktop",
        "/.auth/login/aad",
        "/apps/github?access_token=secret",
        "/apps/github?sig=secret",
        "/apps/github\n",
        "/",
    ],
)
def test_reject_unsafe_links(setup, path):
    workplace, client, _ = setup
    response = client.post(URL, json={**BODY, "path": path})
    assert response.status_code == 422
    workplace.notifier.deliver.assert_not_called()


def test_payload_limits_and_missing_configuration(setup):
    workplace, client, _ = setup
    for body in [
        {**BODY, "webhook": WEBHOOK},
        {**BODY, "title": " "},
        {**BODY, "text": "x" * 4001},
        {**BODY, "event_id": ""},
    ]:
        assert client.post(URL, json=body).status_code == 422
    workplace.notifier.url = None
    assert client.post(URL, json=BODY).status_code == 503
    workplace.notifier.deliver.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://workflow.example",
        "https://user:secret@workflow.example",
        "https://workflow.example/#secret",
        "https://workflow.example/invalid path",
    ],
)
def test_invalid_webhook_configuration(url):
    with pytest.raises(ValueError):
        Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": url})


def test_sandbox_cannot_reach_notification_action(setup, tmp_path):
    workplace, _, app = setup
    client = TestClient(create_app(workplace, static_dir=tmp_path, sandbox_ports=[80]))
    response = client.post(
        URL, json=BODY, headers={"Authorization": "Bearer " + app.memory_token}
    )
    assert response.status_code == 403
    workplace.notifier.deliver.assert_not_called()


@pytest.mark.parametrize("outcome", ["unknown", "failed"])
def test_delivery_outcomes(setup, outcome):
    workplace, client, _ = setup
    workplace.notifier.deliver.return_value = outcome
    assert client.post(URL, json=BODY).json()["status"] == outcome
    workplace.notifier.deliver.return_value = "accepted"
    result = client.post(URL, json=BODY).json()
    assert result["status"] == ("accepted" if outcome == "failed" else "unknown")
    assert workplace.notifier.deliver.await_count == (2 if outcome == "failed" else 1)


@pytest.mark.asyncio
async def test_concurrent_delivery_reserved_once(setup):
    workplace, _, app = setup
    gate = asyncio.Event()
    started = asyncio.Event()

    async def deliver(*args, **kwargs):
        started.set()
        await gate.wait()
        return "accepted"

    workplace.notifier.deliver.side_effect = deliver
    body = TeamsNotification(**BODY)
    first = asyncio.create_task(workplace.teams_notifications.send(app, body))
    await started.wait()
    second = await workplace.teams_notifications.send(app, body)
    assert second["status"] == "sending"
    assert second["duplicate"]
    gate.set()
    assert (await first)["status"] == "accepted"
    workplace.notifier.deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_sender_payload_retries_and_redaction(monkeypatch, caplog):
    posted = []

    def handler(request):
        posted.append(json.loads(request.content))
        return (
            httpx.Response(429, headers={"Retry-After": "0"})
            if len(posted) == 1
            else httpx.Response(202)
        )

    notifier = Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": WEBHOOK})
    caplog.set_level(logging.DEBUG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await notifier.deliver(
            "Done",
            "Summary",
            url="https://workplace.example/automations/pr-opened",
            client=client,
        )
    assert result == "accepted"
    assert len(posted) == 2
    assert posted[0] == posted[1]
    assert posted[0]["attachments"][0]["content"]["actions"] == [
        {
            "type": "Action.OpenUrl",
            "title": "Open in Workplace",
            "url": "https://workplace.example/automations/pr-opened",
        }
    ]
    assert "secret-not-for-logs" not in caplog.text


@pytest.mark.parametrize(
    "code,count", [(400, 1), (401, 1), (403, 1), (407, 1), (302, 1), (429, 3), (503, 3)]
)
@pytest.mark.asyncio
async def test_retries_bounded_and_no_redirect(monkeypatch, caplog, code, count):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            code, headers={"location": "https://evil.example/", "Retry-After": "0"}
        )

    notifier = Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": WEBHOOK})
    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await notifier.deliver("Done", "Summary", client=client) == "failed"
    assert len(requests) == count
    assert "secret-not-for-logs" not in caplog.text


@pytest.mark.asyncio
async def test_timeout_not_retried_and_logs_no_url(caplog):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(WEBHOOK, request=request)

    notifier = Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": WEBHOOK})
    caplog.set_level(logging.DEBUG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await notifier.deliver("Done", "Summary", client=client) == "unknown"
    assert len(calls) == 1
    assert "secret-not-for-logs" not in caplog.text


@pytest.mark.asyncio
async def test_sender_uses_factory_proxy(setup, monkeypatch):
    workplace, _, _ = setup
    workplace.egress.proxy = "http://egress:3128"
    options = {}
    real = httpx.AsyncClient

    def factory(**kwargs):
        options.update(kwargs)
        return real(transport=httpx.MockTransport(lambda request: httpx.Response(202)))

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    assert await Notifier.deliver(workplace.notifier, "Done", "Summary") == "accepted"
    assert options["proxy"] == workplace.egress.runtime_environment([])["HTTPS_PROXY"]
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False


@pytest.mark.asyncio
async def test_connect_failures_retry_with_bounded_backoff(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("omniagents_workplace.notifications.asyncio.sleep", sleep)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("failed", request=request)

    notifier = Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": WEBHOOK})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await notifier.deliver("Done", "Summary", client=client) == "failed"
    assert len(calls) == 3
    assert [call.args for call in sleep.await_args_list] == [(1,), (2,)]


@pytest.mark.asyncio
async def test_long_retry_after_does_not_retry_early(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("omniagents_workplace.notifications.asyncio.sleep", sleep)
    notifier = Notifier({"WORKPLACE_TEAMS_WEBHOOK_URL": WEBHOOK})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "120"})
        )
    ) as client:
        assert await notifier.deliver("Done", "Summary", client=client) == "failed"
    sleep.assert_not_called()
