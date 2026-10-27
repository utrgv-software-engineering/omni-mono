import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from omniagents.integrations import EventIntake, EventStore
from omniagents.integrations.intake import RunResult, RunStep
from omniagents.integrations.linear import (
    LINEAR_EVENTS_PATH,
    LinearConfig,
    linear_signature,
    normalize_linear_event,
    verify_linear_request,
)
from omniagents.integrations.matching import trigger_matches
from omniagents.integrations.server import _mount_linear_route, parse_integrations

SECRET = "linear-secret"


def _event(action="created", **extra):
    payload = {
        "type": "AgentSessionEvent",
        "action": action,
        "agentSession": {
            "id": "sess-1",
            "url": "https://linear.app/acme/issue/ENG-1#agent-session-sess-1",
            "status": "pending",
            "creator": {"name": "Priya Shah"},
            "issue": {
                "identifier": "ENG-1",
                "title": "Tasks API gets slow",
                "url": "u",
                "description": "d",
                "team": {"key": "ENG", "name": "Engineering"},
            },
        },
        "promptContext": '<issue identifier="ENG-1">\n<title>Tasks API gets slow</title>\n</issue>',
        "webhookTimestamp": int(time.time() * 1000),
        **extra,
    }
    return payload


def _signed(payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return body, {
        "linear-signature": linear_signature(secret, body),
        "linear-delivery": "del-1",
        "linear-event": "AgentSessionEvent",
        "content-type": "application/json",
    }


def test_signatures_and_timestamps_are_checked():
    body, headers = _signed(_event())
    assert verify_linear_request(SECRET, headers, body)
    assert not verify_linear_request("other", headers, body)
    stale = json.dumps(
        _event(webhookTimestamp=int(time.time() * 1000) - 120_000)
    ).encode()
    assert not verify_linear_request(
        SECRET, {"linear-signature": linear_signature(SECRET, stale)}, stale
    )


def test_sessions_become_created_and_prompted_events():
    created = normalize_linear_event("del-1", _event())
    assert (created.event, created.thread_key, created.filters) == (
        "agent_session_created",
        "session:sess-1",
        {"teams": ["Engineering", "ENG"]},
    )
    assert created.context.startswith('<issue identifier="ENG-1">\n<title>')
    assert trigger_matches(
        {
            "provider": "linear",
            "event": "agent_session_created",
            "filter": {"teams": ["Engineering"]},
        },
        created,
    )
    assert not trigger_matches(
        {
            "provider": "linear",
            "event": "agent_session_created",
            "filter": {"teams": ["Platform"]},
        },
        created,
    )
    prompted = normalize_linear_event(
        "del-2",
        _event(
            "prompted",
            agentActivity={
                "content": {"type": "prompt", "body": "Cap per_page at 100."},
                "user": {"name": "Priya Shah"},
            },
        ),
    )
    assert (prompted.event, prompted.thread_key, prompted.payload["prompt"]) == (
        "agent_session_prompted",
        "session:sess-1",
        "Cap per_page at 100.",
    )
    assert normalize_linear_event("x", {"type": "Issue", "action": "create"}) is None
    stopped = normalize_linear_event(
        "del-3",
        _event(
            "prompted",
            agentActivity={
                "id": "act-9",
                "signal": "stop",
                "content": {"type": "prompt", "body": "Stop"},
            },
        ),
    )
    assert stopped.control == "stop" and stopped.event == "agent_session_stopped"
    assert stopped.thread_key == created.thread_key


def test_parse_integrations_needs_a_webhook_secret(monkeypatch):
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("LINEAR_ACCESS_TOKEN", "lin_oauth_x")
    [config] = parse_integrations(
        [
            {
                "type": "linear",
                "webhook_secret": "${LINEAR_WEBHOOK_SECRET}",
                "access_token": "${LINEAR_ACCESS_TOKEN}",
                "api_url": "http://x/graphql",
            }
        ]
    )
    assert config == LinearConfig(
        webhook_secret=SECRET, access_token="lin_oauth_x", api_url="http://x/graphql"
    )
    assert parse_integrations([{"type": "linear"}]) == []


@pytest.fixture
def linear_api():
    """Linear's GraphQL API as the factory reaches it: records the activities."""
    activities = []
    app = FastAPI()

    @app.post("/graphql")
    async def graphql(request: Request):
        body = await request.json()
        activities.append(
            (request.headers.get("authorization"), body["variables"]["input"])
        )
        return {
            "data": {
                "agentActivityCreate": {"success": True, "agentActivity": {"id": "a"}}
            }
        }

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}/graphql", activities
    server.should_exit = True
    thread.join(5)


@pytest.mark.parametrize(
    "outcome",
    [RunResult(output="Opened acme/api-service#15."), RunResult(error="max_turns")],
)
def test_a_session_is_acknowledged_then_answered(tmp_path, linear_api, outcome):
    url, activities = linear_api
    path = tmp_path / "automations" / "linear-agent-sessions" / "automation.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntriggers:\n  - provider: linear\n    event: agent_session_created\n---\nWork the issue.\n"
    )
    runs = []

    async def run(agent, session_id, prompt, progress):
        runs.append(prompt)
        await progress(RunStep("message", text="Checking for duplicates first."))
        await progress(RunStep("tool", name="triage"))
        await progress(RunStep("message", text=outcome.output or "Still looking."))
        return outcome

    intake = EventIntake(
        project_root=tmp_path,
        entrypoint="foreman",
        store=EventStore(tmp_path / "e.db"),
        run=run,
    )
    app = FastAPI()
    _mount_linear_route(
        app,
        intake,
        LinearConfig(webhook_secret=SECRET, access_token="lin_oauth_x", api_url=url),
    )
    body, headers = _signed(_event())
    with TestClient(app) as client:
        assert (
            client.post(
                LINEAR_EVENTS_PATH,
                content=body,
                headers={**headers, "linear-signature": "0"},
            ).status_code
            == 401
        )
        assert (
            client.post(LINEAR_EVENTS_PATH, content=body, headers=headers).status_code
            == 200
        )
        client.portal.call(intake.drain)
        for _ in range(100):
            if len(activities) >= 5 - (outcome.error is None):
                break
            time.sleep(0.02)
    [prompt] = runs
    assert prompt.startswith("Work the issue.")
    assert prompt.endswith(
        '## Context\n\n<issue identifier="ENG-1">\n<title>Tasks API gets slow</title>\n</issue>'
    )
    assert {auth for auth, _ in activities} == {"Bearer lin_oauth_x"}
    # The acknowledgement goes out beside the run, not before it in order.
    ack = {"type": "thought", "body": "Working on ENG-1."}
    contents = [a["content"] for _, a in activities]
    assert ack in contents
    steps = [c for c in contents if c != ack]
    # The run shows as it goes: what it said before more work is a thought,
    # each tool it calls an action, and its last message the response.
    shown = [
        {"type": "thought", "body": "Checking for duplicates first."},
        {"type": "action", "action": "Calling", "parameter": "triage"},
    ]
    if outcome.error:
        assert steps == shown + [
            {"type": "thought", "body": "Still looking."},
            {"type": "error", "body": "The run did not finish: max_turns"},
        ]
    else:
        assert steps == shown + [
            {"type": "response", "body": "Opened acme/api-service#15."}
        ]
