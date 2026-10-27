import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniagents.integrations import EventIntake, EventStore
from omniagents.integrations.intake import RunResult
from omniagents.integrations.server import (
    _mount_slack_route,
    mount_integrations,
    parse_integrations,
)
from omniagents.integrations.slack import (
    SLACK_EVENTS_PATH,
    SlackConfig,
    SlackSocketMode,
    normalize_slack_event,
    slack_signature,
    verify_slack_request,
)

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"


def _callback(event, event_id="Ev1"):
    return {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": event_id,
        "event_time": 1700000000,
        "event": event,
    }


def _mention(**extra):
    return {
        "type": "app_mention",
        "user": "U1",
        "channel": "C1",
        "ts": "1.0",
        "text": "<@B> hi",
        **extra,
    }


# -- signatures -------------------------------------------------------------


def test_signature_matches_slacks_documented_example():
    # https://api.slack.com/authentication/verifying-requests-from-slack
    body = (
        b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow"
        b"&channel_id=G8PSS9T3V&channel_name=foobar&user_id=U2CERLKJA"
        b"&user_name=roadrunner&command=%2Fwebhook-collect&text="
        b"&response_url=https%3A%2F%2Fhooks.slack.com%2Fcommands%2FT1DC2JH3J"
        b"%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN"
        b"&trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c"
    )
    assert (
        slack_signature(SECRET, "1531420618", body)
        == "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503"
    )


def test_verify_rejects_bad_signatures_and_stale_timestamps():
    body = b'{"a":1}'
    now = time.time()
    ts = str(int(now))
    good = {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": slack_signature(SECRET, ts, body),
    }
    assert verify_slack_request(SECRET, good, body, now=now)
    assert not verify_slack_request("other", good, body, now=now)
    assert not verify_slack_request(SECRET, good, b'{"a":2}', now=now)
    assert not verify_slack_request(SECRET, good, body, now=now + 6 * 60)
    assert not verify_slack_request(SECRET, {}, body, now=now)
    assert not verify_slack_request(
        SECRET, {**good, "X-Slack-Request-Timestamp": "soon"}, body, now=now
    )


# -- normalization ----------------------------------------------------------


def test_app_mention_threads_on_the_message_or_its_thread():
    event = normalize_slack_event(_callback(_mention()))
    assert (event.provider, event.event, event.delivery_id) == (
        "slack",
        "app_mention",
        "Ev1",
    )
    assert event.thread_key == "T1:C1:1.0"
    assert event.filters == {"users": ["U1"], "channels": ["C1"]}
    assert event.payload["event"]["text"] == "<@B> hi"
    reply = normalize_slack_event(_callback(_mention(ts="2.0", thread_ts="1.0"), "Ev2"))
    assert reply.thread_key == "T1:C1:1.0"


def test_direct_messages_and_reactions():
    dm = normalize_slack_event(
        _callback(
            {
                "type": "message",
                "channel_type": "im",
                "channel": "D1",
                "user": "U1",
                "ts": "3.0",
            }
        )
    )
    assert (dm.event, dm.thread_key) == ("message_dm", "T1:D1:dm")
    reaction = normalize_slack_event(
        _callback(
            {
                "type": "reaction_added",
                "user": "U1",
                "reaction": "ticket",
                "item": {"type": "message", "channel": "C1", "ts": "4.0"},
            }
        )
    )
    assert reaction.event == "reaction_added"
    assert reaction.thread_key == "T1:C1:4.0"
    assert reaction.filters["emojis"] == ["ticket"]
    assert reaction.filters["channels"] == ["C1"]


def _user_install(event, event_id="Ev9"):
    callback = _callback(event, event_id)
    callback["authorizations"] = [{"team_id": "T1", "user_id": "UF", "is_bot": False}]
    return callback


def test_a_factory_account_is_mentioned_and_messaged_through_user_events():
    mention = normalize_slack_event(
        _user_install(
            {
                "type": "message",
                "channel_type": "channel",
                "channel": "C1",
                "user": "U1",
                "ts": "5.0",
                "text": "<@UF> add pagination",
            }
        )
    )
    assert (mention.event, mention.thread_key) == ("app_mention", "T1:C1:5.0")
    labelled = normalize_slack_event(
        _user_install(
            {
                "type": "message",
                "channel_type": "group",
                "channel": "G1",
                "user": "U1",
                "ts": "6.0",
                "thread_ts": "5.5",
                "text": "<@UF|factory> and this",
            }
        )
    )
    assert (labelled.event, labelled.thread_key) == ("app_mention", "T1:G1:5.5")
    dm = normalize_slack_event(
        _user_install(
            {
                "type": "message",
                "channel_type": "im",
                "channel": "D1",
                "user": "U1",
                "ts": "7.0",
            }
        )
    )
    assert dm.event == "message_dm"
    # Channel chatter that does not mention the factory is not a request.
    assert (
        normalize_slack_event(
            _user_install(
                {
                    "type": "message",
                    "channel_type": "channel",
                    "channel": "C1",
                    "user": "U1",
                    "ts": "8.0",
                    "text": "lunch?",
                }
            )
        )
        is None
    )


def test_the_factorys_own_messages_and_reactions_are_not_events():
    for event in (
        {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "UF",
            "ts": "1",
        },
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C1",
            "user": "UF",
            "ts": "1",
            "text": "<@UF> note to self",
        },
        {
            "type": "reaction_added",
            "user": "UF",
            "reaction": "eyes",
            "item": {"type": "message", "channel": "C1", "ts": "1"},
        },
    ):
        assert normalize_slack_event(_user_install(event)) is None


@pytest.mark.parametrize(
    "callback",
    [
        {"type": "url_verification", "challenge": "x"},
        _callback(_mention(), event_id=""),
        _callback(_mention(bot_id="B1")),
        _callback(_mention(subtype="message_changed")),
        _callback(
            {
                "type": "message",
                "channel_type": "channel",
                "channel": "C1",
                "user": "U1",
                "ts": "1",
            }
        ),
        _callback({"type": "member_joined_channel", "user": "U1", "channel": "C1"}),
    ],
)
def test_events_that_are_not_requests_are_ignored(callback):
    assert normalize_slack_event(callback) is None


# -- HTTP delivery ----------------------------------------------------------


class _Runs:
    def __init__(self):
        self.calls = []

    async def __call__(self, agent, session_id, prompt, progress=None):
        self.calls.append((agent, session_id, prompt))
        return RunResult()


def _project(tmp_path):
    path = tmp_path / "automations" / "mentions" / "automation.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntriggers:\n  - provider: slack\n    event: app_mention\n---\nReply.\n",
        encoding="utf-8",
    )
    return tmp_path


def _signed(body: bytes, secret=SECRET):
    ts = str(int(time.time()))
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": slack_signature(secret, ts, body),
        "Content-Type": "application/json",
    }


def test_http_route_verifies_answers_the_challenge_and_accepts_events(tmp_path):
    runs = _Runs()
    intake = EventIntake(
        project_root=_project(tmp_path),
        entrypoint="foreman",
        store=EventStore(tmp_path / "events.db"),
        run=runs,
    )
    app = FastAPI()
    _mount_slack_route(app, intake, SlackConfig(delivery="http", signing_secret=SECRET))

    challenge = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    event = json.dumps(_callback(_mention())).encode()
    with TestClient(app) as client:
        assert client.post(
            SLACK_EVENTS_PATH, content=challenge, headers=_signed(challenge)
        ).json() == {"challenge": "abc"}
        assert (
            client.post(
                SLACK_EVENTS_PATH, content=event, headers=_signed(event, "wrong")
            ).status_code
            == 401
        )
        assert (
            client.post(
                SLACK_EVENTS_PATH, content=event, headers=_signed(event)
            ).status_code
            == 200
        )
        # Slack retries an unacknowledged delivery with the same event_id.
        assert (
            client.post(
                SLACK_EVENTS_PATH, content=event, headers=_signed(event)
            ).status_code
            == 200
        )
        client.portal.call(intake.drain)
    assert len(runs.calls) == 1
    [record] = intake.store.recent()
    assert (record["event"], record["status"]) == ("app_mention", "dispatched")


# -- Socket Mode ------------------------------------------------------------


@pytest.mark.asyncio
async def test_socket_mode_acks_envelopes_accepts_events_and_reconnects():
    from websockets.asyncio.server import serve

    acks = []
    connections = []

    async def handler(socket):
        connections.append(socket)
        await socket.send(json.dumps({"type": "hello", "num_connections": 1}))
        if len(connections) == 1:
            await socket.send(
                json.dumps(
                    {
                        "type": "events_api",
                        "envelope_id": "env-1",
                        "accepts_response_payload": False,
                        "payload": _callback(_mention()),
                    }
                )
            )
            acks.append(json.loads(await socket.recv()))
            await socket.send(
                json.dumps({"type": "disconnect", "reason": "refresh_requested"})
            )
        await socket.wait_closed()

    accepted = []

    async def accept(event):
        accepted.append(event)
        return True

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        socket_mode = SlackSocketMode(
            SlackConfig(delivery="socket", app_token="xapp-test"),
            accept,
            retry_seconds=0,
        )

        async def open_url():
            return f"ws://127.0.0.1:{port}/"

        socket_mode._open_url = open_url
        socket_mode.start()
        try:
            for _ in range(200):
                if len(connections) >= 2 and socket_mode.connected.is_set():
                    break
                await asyncio.sleep(0.01)
        finally:
            await socket_mode.stop()

    assert acks == [{"envelope_id": "env-1"}]
    assert [e.event for e in accepted] == ["app_mention"]
    assert len(connections) == 2


@pytest.mark.asyncio
async def test_socket_mode_opens_its_url_with_the_app_token(monkeypatch):
    import httpx

    seen = {}

    def respond(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True, "url": "wss://example/link"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    socket_mode = SlackSocketMode(
        SlackConfig(delivery="socket", app_token="xapp-1", api_url="http://sim/api/"),
        None,
    )
    assert await socket_mode._open_url() == "wss://example/link"
    assert seen == {
        "url": "http://sim/api/apps.connections.open",
        "auth": "Bearer xapp-1",
    }


# -- project.yml ------------------------------------------------------------


def test_parse_integrations_reads_credentials_from_the_environment(monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-1")
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("SLACK_API_URL", raising=False)
    [config] = parse_integrations(
        [
            {"type": "github"},
            {
                "type": "slack",
                "app_token": "${SLACK_APP_TOKEN}",
                "api_url": "${SLACK_API_URL:-http://localhost:7300/api}",
            },
        ]
    )
    assert config == SlackConfig(
        delivery="socket", app_token="xapp-1", api_url="http://localhost:7300/api"
    )


def test_parse_integrations_leaves_off_slack_without_credentials(monkeypatch):
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    assert parse_integrations([{"type": "slack"}]) == []
    assert (
        parse_integrations([{"type": "slack", "app_token": "${SLACK_APP_TOKEN}"}]) == []
    )
    assert parse_integrations([{"type": "slack", "delivery": "socket"}]) == []
    assert (
        parse_integrations([{"type": "slack", "delivery": "pigeon", "app_token": "x"}])
        == []
    )
    [http] = parse_integrations([{"type": "slack", "signing_secret": "s"}])
    assert http.delivery == "http"


def test_mount_integrations_without_a_manifest_or_integrations(tmp_path):
    class Spec:
        manifest_path = None

    app = FastAPI()
    assert mount_integrations(app, Spec(), None) is None
    Spec.manifest_path = str(tmp_path / "project.yml")
    (tmp_path / "project.yml").write_text("name: x\n", encoding="utf-8")
    assert mount_integrations(app, Spec(), None) is None


def test_a_run_counts_as_done_only_when_it_completed():
    from omniagents.integrations.server import run_failure

    assert run_failure({"end_reason": "completed"}) is None
    assert (
        run_failure({"end_reason": "error", "error": {"message": "no provider"}})
        == "error: no provider"
    )
    assert run_failure({"end_reason": "max_turns"}) == "max_turns"
    assert run_failure(None) == "the run ended without reporting how"


@pytest.mark.asyncio
async def test_with_a_token_an_events_channel_filter_has_its_name(monkeypatch):
    import httpx

    from omniagents.integrations import IncomingEvent
    from omniagents.integrations.slack import ChannelNames, SlackConfig

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(
            200, json={"ok": True, "channel": {"id": "C7", "name": "factory-intake"}}
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    names = ChannelNames(
        SlackConfig(delivery="socket", api_url="http://w/slack/api", token="xoxp-1")
    )
    event = IncomingEvent(
        provider="slack",
        event="reaction_added",
        delivery_id="E1",
        payload={},
        filters={"channels": ["C7"], "emojis": ["ticket"]},
    )
    named = await names.named(event)
    assert named.filters == {"channels": ["C7", "factory-intake"], "emojis": ["ticket"]}
    await names.named(event)
    assert calls == [
        ("http://w/slack/api/conversations.info?channel=C7", "Bearer xoxp-1")
    ]  # once
    # Without a token, ids only.
    bare = ChannelNames(SlackConfig(delivery="socket"))
    assert (await bare.named(event)) is event
