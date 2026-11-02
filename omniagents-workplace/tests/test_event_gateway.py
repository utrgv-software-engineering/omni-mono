import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from omniagents_workplace.event_gateway import EventGateway
from omniagents_workplace.repository_settings import KEY
from omniagents_workplace.server import create_app
from omniagents_workplace.store import Store
from test_factory_runtime import _workplace


@pytest.fixture
def gateway(tmp_path):
    w = _workplace(tmp_path, "http://runtime.invalid")
    w.store.set_meta(
        KEY,
        {
            "provider": "standin",
            "repositories": ["initech/app"],
            "factory_repository": None,
            "self_improvement": False,
        },
    )
    raw = w.factory_config.raw()
    raw["app"]["github_webhook"] = "http://runtime.invalid/integrations/github/events"
    import yaml

    w.factory_config.path.write_text(yaml.safe_dump(raw))
    # Apply changes after factory configuration has been attached.
    w.factory_config.apply(w)
    yield w.event_gateway
    w.close()


def event(gateway, delivery="one"):
    body = json.dumps(
        {
            "action": "labeled",
            "repository": {"full_name": "initech/app"},
            "issue": {"number": 1, "labels": []},
            "label": {"name": "probe"},
        }
    ).encode()
    secret = gateway.source("github")["secret"]
    return body, {
        "x-github-event": "issues",
        "x-github-delivery": delivery,
        "x-hub-signature-256": "sha256="
        + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
    }


def rows(g):
    return g.w.store._db.execute("SELECT * FROM factory_events ORDER BY id").fetchall()


def test_signed_inbound_commits_before_ack_and_deduplicates(gateway, tmp_path):
    g = gateway
    client = TestClient(create_app(g.w, static_dir=tmp_path))
    body, headers = event(g)
    assert client.post("/factory/events/github", content=body).status_code == 401
    assert not rows(g)
    assert client.post(
        "/factory/events/github", content=body, headers=headers
    ).json() == {"accepted": True}
    assert (
        client.post("/factory/events/github", content=body, headers=headers).status_code
        == 200
    )
    assert len(rows(g)) == 1
    reopened = Store(g.w.store.path)
    try:
        assert (
            reopened._db.execute("SELECT count(*) FROM factory_events").fetchone()[0]
            == 1
        )
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_retry_after_outage_reuses_id_and_refreshes_target(gateway):
    g = gateway
    body, headers = event(g)
    g.enqueue(g.origin("github"), "github", "one", body, headers)
    requests = []
    available = False

    def handler(request):
        requests.append(request)
        if not available:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await g.deliver_pending(client)
        assert rows(g)[0][9] is None and rows(g)[0][7] == 1
        # A process restart reconstructs the worker from the persisted queue.
        restarted = EventGateway(g.w)
        raw = g.w.factory_config.raw()
        raw["factory"]["url"] = "http://replacement.invalid"
        import yaml

        g.w.factory_config.path.write_text(yaml.safe_dump(raw))
        g.w.store._db.execute("UPDATE factory_events SET next_attempt=0")
        available = True
        await restarted.deliver_pending(client)
    assert rows(g)[0][9] is not None
    assert [r.headers["x-github-delivery"] for r in requests] == ["one", "one"]
    assert requests[-1].url.host == "replacement.invalid"


@pytest.mark.asyncio
async def test_lost_ack_retries_without_duplicate_runtime_intake(gateway):
    from omniagents.integrations.github import normalize_github_event
    from omniagents.integrations.store import EventStore

    g = gateway
    body, headers = event(g)
    g.enqueue(g.origin("github"), "github", "one", body, headers)
    store = EventStore(g.w.data_dir / "receiver.db")
    attempts = 0

    def handler(request):
        nonlocal attempts
        for incoming in normalize_github_event(
            "issues", request.headers["x-github-delivery"], json.loads(request.content)
        ):
            store.record(incoming)
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("ACK lost", request=request)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await g.deliver_pending(client)
        g.w.store._db.execute("UPDATE factory_events SET next_attempt=0")
        await g.deliver_pending(client)
    assert store._db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert rows(g)[0][9] is not None
    store.close()


@pytest.mark.asyncio
async def test_socket_ack_occurs_after_commit_and_not_on_failed_storage(
    gateway, monkeypatch
):
    g = gateway
    acks = []

    class Socket:
        async def send(self, data):
            assert len(rows(g)) == 1
            acks.append(json.loads(data))

    message = {
        "type": "events_api",
        "envelope_id": "e",
        "payload": {"event_id": "slack-one", "event": {"type": "app_mention"}},
    }
    await g.socket_message(Socket(), message, g.origin("slack"))
    assert acks == [{"envelope_id": "e"}]

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(g, "enqueue", fail)
    with pytest.raises(OSError):
        await g.socket_message(Socket(), message, g.origin("slack"))
    assert len(acks) == 1


@pytest.mark.asyncio
async def test_provider_switch_holds_pending_event(gateway):
    g = gateway
    body, headers = event(g)
    g.enqueue(g.origin("github"), "github", "one", body, headers)
    settings = g.w.store.meta(KEY)
    settings["provider"] = "github"
    g.w.store.set_meta(KEY, settings)

    def unexpected(request):
        raise AssertionError("Do not deliver old-provider events")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        await g.deliver_pending(client)
    assert rows(g)[0][9] is None
    assert "provider changed" in rows(g)[0][10]


def test_managed_app_routes_to_gateway_and_other_apps_unchanged(gateway):
    g = gateway
    app = g.app()
    assert g.w.apps._github_hook(app).url == g.endpoint("github")
    assert g.w.apps.env(app)["OMNIAGENTS_EVENT_GATEWAY"] == "1"
    other = g.w.apps.register(None, name="Other", username="other")
    assert "OMNIAGENTS_EVENT_GATEWAY" not in g.w.apps.env(other)


def test_real_github_is_stored_without_reaching_runtime(gateway, tmp_path, monkeypatch):
    g = gateway
    settings = g.w.store.meta(KEY)
    settings["provider"] = "github"
    g.w.store.set_meta(KEY, settings)
    monkeypatch.setattr(
        g.w.repository_settings,
        "oauth_config",
        lambda: {"webhook_secret": "real-secret"},
    )
    body = json.dumps({"repository": {"full_name": "initech/app"}}).encode()
    headers = {
        "x-github-event": "push",
        "x-github-delivery": "real-one",
        "x-hub-signature-256": "sha256="
        + hmac.new(b"real-secret", body, hashlib.sha256).hexdigest(),
    }
    client = TestClient(create_app(g.w, static_dir=tmp_path))
    assert client.post("/github/events", content=body, headers=headers).json() == {
        "ok": True
    }
    assert rows(g)[0][1] == "real-github" and rows(g)[0][7] == 0


@pytest.mark.asyncio
async def test_slack_retry_refreshes_signature_and_preserves_event_id(gateway):
    from omniagents.integrations.slack import verify_slack_request

    g = gateway
    callback = {"event_id": "E-one", "event": {"type": "app_mention"}}
    g.enqueue(g.origin("slack"), "slack", "E-one", json.dumps(callback).encode(), {})

    def handler(request):
        assert verify_slack_request(
            g.source("slack")["secret"], dict(request.headers), request.content
        )
        assert json.loads(request.content)["event_id"] == "E-one"
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await g.deliver_pending(client)
    assert rows(g)[0][9] is not None


def test_staging_restore_never_replays_production_queue(gateway, tmp_path):
    from omniagents_workplace.backup import backup, restore

    g = gateway
    body, headers = event(g)
    g.enqueue(g.origin("github"), "github", "one", body, headers)
    saved = backup(g.w.data_dir, tmp_path / "backups")
    production = tmp_path / "production-restore"
    restore(saved, production, mode="production")
    restored = Store(production / "workplace.db")
    try:
        assert (
            restored._db.execute(
                "SELECT count(*) FROM factory_events WHERE delivered IS NULL"
            ).fetchone()[0]
            == 1
        )
    finally:
        restored.close()
    target = tmp_path / "staging"
    restore(saved, target, mode="staging")
    reopened = Store(target / "workplace.db")
    try:
        assert not reopened._db.execute(
            "SELECT name FROM sqlite_master WHERE name='factory_events'"
        ).fetchall()
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_real_http_receiver_restart_does_not_lose_accepted_events(
    gateway, tmp_path
):
    import socket
    import uvicorn
    from fastapi import FastAPI
    from omniagents.integrations.server import _mount_github_route
    from omniagents.integrations.github import GitHubWebhookConfig
    from omniagents.integrations.intake import EventIntake
    from omniagents.integrations.store import EventStore
    from omniagents.core.runtime.release_gate import ReleaseGate

    g = gateway
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    raw = g.w.factory_config.raw()
    raw["factory"]["url"] = f"http://127.0.0.1:{port}"
    import yaml

    g.w.factory_config.path.write_text(yaml.safe_dump(raw))
    project = tmp_path / "receiver"
    project.mkdir()
    (project / "automations").mkdir()
    (project / "project.yml").write_text("product:\n  name: receiver\n")

    async def unexpected(*args):
        raise AssertionError("Do not run an agent")

    async def start(paused):
        store = EventStore(project / "events.db")
        intake = EventIntake(
            project_root=project, entrypoint="generalist", store=store, run=unexpected
        )
        intake.release_gate = ReleaseGate(lambda: 0)
        if paused:
            intake.release_gate.acquire()
        app = FastAPI()
        _mount_github_route(
            app,
            intake,
            GitHubWebhookConfig(webhook_secret=g.source("github")["secret"]),
        )
        await intake.resume_received()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        task = asyncio.create_task(server.serve())
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        return store, intake, server, task

    async def stop(parts):
        store, intake, server, task = parts
        server.should_exit = True
        await task
        pending = list(intake._tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        store.close()

    client = TestClient(create_app(g.w, static_dir=tmp_path))
    parts = await start(True)
    try:
        async with httpx.AsyncClient() as outgoing:
            body, headers = event(g, "before")
            assert (
                client.post(
                    "/factory/events/github", content=body, headers=headers
                ).status_code
                == 200
            )
            await g.deliver_pending(outgoing)
            assert len(parts[0].received_events()) == 1
            await stop(parts)
            parts = None
            body, headers = event(g, "during")
            assert (
                client.post(
                    "/factory/events/github", content=body, headers=headers
                ).status_code
                == 200
            )
            await g.deliver_pending(outgoing)
            assert rows(g)[-1][9] is None
            parts = await start(False)
            restarted = EventGateway(g.w)
            g.w.store._db.execute("UPDATE factory_events SET next_attempt=0")
            await restarted.deliver_pending(outgoing)
            await parts[1].drain()
            assert (
                parts[0]._db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
            )
            assert (
                parts[0]
                ._db.execute("SELECT count(*) FROM events WHERE status='ignored'")
                .fetchone()[0]
                == 2
            )
            assert all(row[9] is not None for row in rows(g))
    finally:
        if parts:
            await stop(parts)
