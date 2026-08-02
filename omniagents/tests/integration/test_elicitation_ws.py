"""End-to-end elicitation tests over the real /ws surface.

A mock agent bridge triggers ``AgentService.request_elicitation`` when
the prompt starts with ``ELICIT``; the tests drive the full wire loop:
``elicitation_requested`` notification → ``elicitation_response`` RPC →
``elicitation_resolved`` notification, including multi-tab
first-response, reconnect replay, run-cancellation clearing, expiry,
persistence, and cross-user denial.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime import bridge as runner_bridge
from omniagents.rpc.auth import RPC_ERROR_AUTHORIZATION_DENIED
from agents import Agent

_HOLDER: dict = {}

ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}


async def mock_build_agent(settings, mcp, app):
    return Agent(
        name="elicit_agent",
        tools=[],
        instructions="Elicit",
        model="gpt-3.5-turbo",
    )


async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20, **kwargs):
    sess.history.append({"role": "user", "content": prompt})
    if prompt.startswith("ELICIT"):
        config = json.loads(prompt[len("ELICIT") :].strip() or "{}")
        service = _HOLDER["service"]
        outcome = await service.request_elicitation(
            session=sess,
            run_id=getattr(sess, "active_run_id", None),
            **config,
        )
        _HOLDER.setdefault("outcomes", []).append(outcome)


class RpcWs:
    """Minimal JSON-RPC helper over a TestClient websocket."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.notifications = []

    def call(self, method, **params):
        self.next_id += 1
        call_id = f"c{self.next_id}"
        self.ws.send_json(
            {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        )
        while True:
            message = self.ws.receive_json()
            if message.get("id") == call_id and (
                "result" in message or "error" in message
            ):
                return message
            self.notifications.append(message)

    def result(self, method, **params):
        message = self.call(method, **params)
        assert "error" not in message, message
        return message["result"]

    def wait_for(self, method):
        """Pop the first stored/live notification with *method*."""
        for index, message in enumerate(self.notifications):
            if message.get("method") == method:
                return self.notifications.pop(index)["params"]
        while True:
            message = self.ws.receive_json()
            if message.get("method") == method:
                return message["params"]
            self.notifications.append(message)

    def handshake(self):
        self.result(
            "initialize",
            protocol_version="1.0.0",
            identity={"name": "elicit-tests", "version": "0.0.0"},
            platform={"os": "linux", "arch": "x86_64"},
            capabilities={
                "realtime": False,
                "mcp_apps": False,
                "client_functions": True,
                "approvals": True,
                "artifacts": False,
                "replay": True,
                "terminal": False,
                "experimental_operations": [],
                "disabled_notifications": [],
            },
        )
        self.ws.send_json({"jsonrpc": "2.0", "method": "initialized", "params": {}})


def _make_context(monkeypatch, security_config=None):
    from omniagents.core.agents import service as service_mod

    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", mock_stream_agent)
    _HOLDER.clear()

    spec = AgentSpec(
        name="ElicitAgent",
        welcome_text="Elicit",
        get_agent_instructions=lambda: "Elicit",
        build_agent=mock_build_agent,
        security_config=security_config or {},
    )
    app = server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    client = TestClient(app)
    _HOLDER["service"] = app.state.agent_service
    return SimpleNamespace(client=client, service=app.state.agent_service)


@pytest.fixture
def context(monkeypatch):
    yield _make_context(monkeypatch)


@pytest.fixture
def authz_context(monkeypatch):
    yield _make_context(
        monkeypatch,
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        },
    )


def _connect(context, headers=None):
    cm = context.client.websocket_connect("/ws", headers=headers or {})
    ws = cm.__enter__()
    rpc = RpcWs(ws)
    rpc.handshake()
    return cm, rpc


def _elicit_prompt(**config):
    return "ELICIT " + json.dumps(config)


KIND_MATRIX = [
    (
        {"kind": "question", "message": "What is your name?", "title": "Intro"},
        {"text": "Ada"},
    ),
    (
        {"kind": "confirm", "message": "Deploy to production?"},
        {"confirmed": True},
    ),
    (
        {
            "kind": "select",
            "message": "Pick a color",
            "options": [
                {"value": "red", "label": "Red"},
                {"value": "blue", "label": "Blue"},
            ],
        },
        {"selected": ["blue"]},
    ),
    (
        {
            "kind": "form",
            "message": "Connection details",
            "input_schema": {
                "type": "object",
                "required": ["host"],
                "properties": {
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                },
            },
        },
        {"host": "db.internal", "port": 5432},
    ),
    (
        {
            "kind": "url",
            "message": "Finish sign-in in your browser",
            "url": "https://example.test/device",
        },
        {"completed": True},
    ),
]


@pytest.mark.parametrize("config,accept_value", KIND_MATRIX)
def test_each_request_kind_end_to_end(context, config, accept_value):
    cm, rpc = _connect(context)
    try:
        rpc.result(
            "start_run",
            prompt=_elicit_prompt(**config, persist_response=True),
        )
        prompt = rpc.wait_for("elicitation_requested")
        assert prompt["kind"] == config["kind"]
        assert prompt["message"] == config["message"]
        assert prompt["elicitation_id"].startswith("elc_")
        assert isinstance(prompt["seq"], int)
        assert prompt["session_id"]
        assert prompt["run_id"]
        for key in ("title", "url", "options", "input_schema"):
            if key in config:
                assert prompt[key] == config[key]

        result = rpc.result(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value=accept_value,
        )
        assert result == {
            "elicitation_id": prompt["elicitation_id"],
            "status": "accepted",
        }
        resolved = rpc.wait_for("elicitation_resolved")
        assert resolved["elicitation_id"] == prompt["elicitation_id"]
        assert resolved["status"] == "accepted"
        assert resolved["value"] == accept_value  # persist_response=True
        end = rpc.wait_for("run_end")
        assert end["end_reason"] == "completed"
        assert _HOLDER["outcomes"][-1]["action"] == "accept"
        assert _HOLDER["outcomes"][-1]["value"] == accept_value
    finally:
        cm.__exit__(None, None, None)


def test_multi_tab_first_response_wins(context):
    tab1_cm, tab1 = _connect(context)
    tab2_cm, tab2 = _connect(context)
    try:
        run = tab1.result(
            "start_run", prompt=_elicit_prompt(kind="confirm", message="Sure?")
        )
        prompt = tab1.wait_for("elicitation_requested")

        # Tab 2 attaches via the replay path and sees the pending prompt.
        resumed = tab2.result(
            "resume_session", session_id=run["session_id"], after_seq=0
        )
        replayed = [e["method"] for e in resumed["events"]]
        assert "elicitation_requested" in replayed
        assert "elicitation_resolved" not in replayed

        # Tab 2 answers first and wins.
        tab2.result(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value={"confirmed": True},
        )
        # Tab 1's later answer gets the typed already-resolved rejection.
        message = tab1.call(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value={"confirmed": False},
        )
        assert message["error"]["code"] == -32051
        assert message["error"]["data"] == {
            "kind": "elicitation_already_resolved",
            "elicitation_id": prompt["elicitation_id"],
            "status": "accepted",
        }
        # Both tabs converge on the resolution event.
        assert tab1.wait_for("elicitation_resolved")["status"] == "accepted"
        assert tab2.wait_for("elicitation_resolved")["status"] == "accepted"
        assert _HOLDER["outcomes"][-1]["value"] == {"confirmed": True}
    finally:
        tab2_cm.__exit__(None, None, None)
        tab1_cm.__exit__(None, None, None)


def test_reconnect_replays_pending_elicitation(context):
    """A connection that attaches mid-prompt replays the pending
    elicitation from the journal and can answer it; a connection that
    reconnects after the prompt's run died converges on the terminal
    resolution instead of a dangling prompt.

    (The TestClient harness cancels the run task when the initiating
    socket closes — its portal owns the task — so the true
    survive-disconnect path is exercised by the fresh connection's
    ``resume_session`` while the run is still live, and the
    disconnect-then-reconnect leg asserts terminal convergence.)
    """
    cm, rpc = _connect(context)
    run = rpc.result(
        "start_run", prompt=_elicit_prompt(kind="question", message="Still there?")
    )
    prompt = rpc.wait_for("elicitation_requested")

    # Fresh connection, no shared state: the journal replays the pending
    # prompt (no resolution yet) and the prompt is answerable from here.
    cm2, rpc2 = _connect(context)
    try:
        resumed = rpc2.result(
            "resume_session", session_id=run["session_id"], after_seq=0
        )
        replayed = {e["method"]: e["params"] for e in resumed["events"]}
        assert replayed["elicitation_requested"]["elicitation_id"] == (
            prompt["elicitation_id"]
        )
        assert "elicitation_resolved" not in replayed
        rpc2.result(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value={"text": "yes"},
        )
        assert rpc2.wait_for("elicitation_resolved")["status"] == "accepted"
        # The run's own connection sees the dismissal and the run finish.
        assert rpc.wait_for("elicitation_resolved")["status"] == "accepted"
        assert rpc.wait_for("run_end")["end_reason"] == "completed"
        assert _HOLDER["outcomes"][-1]["value"] == {"text": "yes"}
    finally:
        cm2.__exit__(None, None, None)
        cm.__exit__(None, None, None)

    # Second leg: a prompt whose run dies while no tab is connected.
    cm3, rpc3 = _connect(context)
    run2 = rpc3.result(
        "start_run", prompt=_elicit_prompt(kind="confirm", message="Orphaned")
    )
    prompt2 = rpc3.wait_for("elicitation_requested")
    cm3.__exit__(None, None, None)  # run task dies with the socket's portal

    cm4, rpc4 = _connect(context)
    try:
        resumed = rpc4.result(
            "resume_session", session_id=run2["session_id"], after_seq=0
        )
        replayed = {e["method"]: e["params"] for e in resumed["events"]}
        assert replayed["elicitation_requested"]["elicitation_id"] == (
            prompt2["elicitation_id"]
        )
        # The journal closed the prompt out: replaying clients converge on
        # a resolved state, never a prompt nothing backs.
        assert replayed["elicitation_resolved"]["status"] == "cancelled"
        assert replayed["run_end"]["end_reason"] == "cancelled"
    finally:
        cm4.__exit__(None, None, None)


def test_run_cancellation_clears_pending_elicitation(context):
    cm, rpc = _connect(context)
    try:
        run = rpc.result(
            "start_run", prompt=_elicit_prompt(kind="confirm", message="Blocked")
        )
        prompt = rpc.wait_for("elicitation_requested")

        assert rpc.result("stop_run", run_id=run["run_id"]) is True
        resolved = rpc.wait_for("elicitation_resolved")
        assert resolved["elicitation_id"] == prompt["elicitation_id"]
        assert resolved["status"] == "cancelled"
        assert rpc.wait_for("run_end")["end_reason"] == "cancelled"

        # The pending maps are clean and late answers are duplicates.
        service = context.service
        assert service._pending_elicitations == {}
        sess = service.get_session(run["session_id"])
        assert sess.pending_elicitations == {}
        message = rpc.call(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value={"confirmed": True},
        )
        assert message["error"]["code"] == -32051
        assert message["error"]["data"]["status"] == "cancelled"
    finally:
        cm.__exit__(None, None, None)


def test_expiry_resolves_without_response(context):
    cm, rpc = _connect(context)
    try:
        rpc.result(
            "start_run",
            prompt=_elicit_prompt(kind="question", message="Quick!", timeout_ms=200),
        )
        prompt = rpc.wait_for("elicitation_requested")
        resolved = rpc.wait_for("elicitation_resolved")
        assert resolved["elicitation_id"] == prompt["elicitation_id"]
        assert resolved["status"] == "expired"
        assert rpc.wait_for("run_end")["end_reason"] == "completed"
        assert _HOLDER["outcomes"][-1]["action"] == "expire"
    finally:
        cm.__exit__(None, None, None)


def test_sensitive_response_never_enters_replayable_history(context):
    cm, rpc = _connect(context)
    try:
        run = rpc.result(
            "start_run",
            prompt=_elicit_prompt(
                kind="question", message="API key?", persist_response=False
            ),
        )
        prompt = rpc.wait_for("elicitation_requested")
        rpc.result(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value={"text": "sk-very-secret"},
        )
        resolved = rpc.wait_for("elicitation_resolved")
        assert "value" not in resolved
        rpc.wait_for("run_end")
        # The in-process caller received the value...
        assert _HOLDER["outcomes"][-1]["value"] == {"text": "sk-very-secret"}
        # ...but the durable journal never did.
        resumed = rpc.result(
            "resume_session", session_id=run["session_id"], after_seq=0
        )
        assert "sk-very-secret" not in json.dumps(resumed)
    finally:
        cm.__exit__(None, None, None)


def test_cross_user_elicitation_response_denied(authz_context):
    alice_cm, alice = _connect(authz_context, ALICE)
    bob_cm, bob = _connect(authz_context, BOB)
    try:
        alice.result(
            "start_run", prompt=_elicit_prompt(kind="confirm", message="Alice only")
        )
        prompt = alice.wait_for("elicitation_requested")

        # Bob cannot answer alice's elicitation (pending or unknown ids
        # both deny — fail closed).
        message = bob.call(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value={"confirmed": True},
        )
        assert message["error"]["code"] == RPC_ERROR_AUTHORIZATION_DENIED
        assert message["error"]["data"]["kind"] == "authorization_denied"
        message = bob.call(
            "elicitation_response",
            elicitation_id="elc_unknown",
            action="accept",
            value={"confirmed": True},
        )
        assert message["error"]["code"] == RPC_ERROR_AUTHORIZATION_DENIED

        # Alice herself can still answer.
        alice.result(
            "elicitation_response",
            elicitation_id=prompt["elicitation_id"],
            action="accept",
            value={"confirmed": True},
        )
        assert alice.wait_for("elicitation_resolved")["status"] == "accepted"
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)
