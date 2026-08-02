"""End-to-end ``mcp_*`` management RPC tests over the real /ws surface.

Exercises the acceptance criteria of the MCP management surface
(tkt_kVK7F-iaL-OA):

* lifecycle CRUD + reload through the stable API, with adversarial
  secret read-back checks on every frame either socket receives and on
  server debug logging (the PR #294/#304 house pattern),
* standardized startup transitions against a REAL stdio MCP server —
  broadcast live and surviving reconnect via refetch,
* MCP server-initiated elicitations routed through the #308 protocol
  (form round-trip against a real FastMCP server),
* OAuth to the URL-handoff point over the wire and the typed
  auth-expired reauthentication cue,
* authorization: reads allowed for any principal, config/credential
  mutations refused for individual principals in enforced mode, and
  cross-user session-scoped denials.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec, MCPServerConfig
from omniagents.core.debug import Debug
from tests.fixtures.gui_ws import perform_gui_handshake
from tests.fixtures.mcp_oauth_app import (
    ACCESS_SECRET,
    AUTH_CODE,
    REFRESH_SECRET,
    SERVER_URL,
    make_fake_oauth_app,
)

from agents import Agent

PRODUCT = "mcpmgmttest"
HEADER_SECRET = "Bearer WS-HEADER-SECRET-123"
NEW_HEADER_SECRET = "WS-NEW-SECRET-456"
ENV_SECRET = "WS-ENV-SECRET-789"
ALL_SECRETS = (
    HEADER_SECRET,
    NEW_HEADER_SECRET,
    ENV_SECRET,
    ACCESS_SECRET,
    REFRESH_SECRET,
)

FIXTURE_SERVER = str(
    Path(__file__).resolve().parent.parent / "fixtures" / "mcp_elicit_server.py"
)


async def dummy_build_agent(settings, mcp, app):
    return Agent(name="mcp", tools=[], instructions="", model="gpt")


def _elicit_server_config(name: str = "elicit-fixture") -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        type="stdio",
        params={"command": sys.executable, "args": [FIXTURE_SERVER]},
        options={"client_session_timeout_seconds": 30},
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated product config dir + manifest enabling user MCP servers."""
    config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = project_root / "project.yml"
    manifest.write_text(
        f"name: {PRODUCT}\nallow_user_mcp_servers: true\n", encoding="utf-8"
    )
    return SimpleNamespace(
        manifest=manifest,
        user_mcp=config_home / PRODUCT / "mcp.json",
    )


def _make_context(env, *, mcp_servers=None, security_config=None, auth_token=None):
    spec = AgentSpec(
        name="McpMgmtAgent",
        welcome_text="hi",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
        mcp_servers=list(mcp_servers or []),
        security_config=security_config or {},
    )
    spec.project_slug = PRODUCT
    spec.manifest_path = str(env.manifest)
    app = server_app.build_app(
        spec=spec, default_host_workspace_root="/tmp", auth_token=auth_token
    )
    return SimpleNamespace(client=TestClient(app), service=app.state.agent_service)


class RpcWs:
    """Minimal JSON-RPC helper that records EVERY frame it receives."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.frames: list[dict] = []
        self.pending: list[dict] = []

    def _receive(self):
        message = self.ws.receive_json()
        self.frames.append(message)
        return message

    def send(self, method, **params):
        self.next_id += 1
        call_id = f"c{self.next_id}"
        self.ws.send_json(
            {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        )
        return call_id

    def wait(self, call_id):
        for index, message in enumerate(self.pending):
            if message.get("id") == call_id:
                return self.pending.pop(index)
        while True:
            message = self._receive()
            if message.get("id") == call_id:
                return message
            self.pending.append(message)

    def call(self, method, **params):
        return self.wait(self.send(method, **params))

    def result(self, method, **params):
        message = self.call(method, **params)
        assert "error" not in message, message
        return message["result"]

    def error(self, method, **params):
        message = self.call(method, **params)
        assert "error" in message, message
        return message["error"]

    def wait_for(self, method):
        for index, message in enumerate(self.pending):
            if message.get("method") == method:
                return self.pending.pop(index)["params"]
        while True:
            message = self._receive()
            if message.get("method") == method:
                return message["params"]
            self.pending.append(message)


def _connect(context, headers=None, experimental=()):
    cm = context.client.websocket_connect("/ws", headers=headers or {})
    ws = cm.__enter__()
    rpc = RpcWs(ws)
    perform_gui_handshake(ws, experimental=experimental)
    return cm, rpc


def _wait_for_nudged(rpc, method, timeout=60.0):
    """Wait for a notification that originates on ANOTHER connection.

    TestClient artifact, not a product behavior: each websocket_connect
    runs its ASGI task on its own portal event loop, and an anyio
    memory-stream send from a foreign loop buffers the frame without
    waking an already-blocked receiver. (In production a single server
    loop serves every connection, so the wakeup is always same-loop.)
    Issuing a cheap same-loop RPC flushes the buffered frames.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for index, message in enumerate(rpc.pending):
            if message.get("method") == method:
                return rpc.pending.pop(index)["params"]
        rpc.result("get_agent_info")  # same-loop nudge
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {method}")


def _assert_no_secret_frames(*rpcs):
    for rpc in rpcs:
        for frame in rpc.frames:
            text = json.dumps(frame)
            for secret in ALL_SECRETS:
                assert secret not in text, f"secret leaked in frame: {frame}"


# ---------------------------------------------------------------------------
# Lifecycle CRUD + secret redaction
# ---------------------------------------------------------------------------


def test_crud_lifecycle_with_adversarial_secret_readback(env, capsys):
    context = _make_context(
        env,
        mcp_servers=[
            MCPServerConfig(
                name="proj-http",
                type="streamable_http",
                params={
                    "url": "http://proj.test/mcp",
                    "headers": {"Authorization": HEADER_SECRET},
                },
            )
        ],
    )
    Debug.set_enabled(True)
    Debug._log_file = None  # route Debug.log to stderr → captured by capsys
    try:
        cm_a, a = _connect(context)
        cm_b, b = _connect(context)
        try:
            listing = a.result("mcp_list_servers")
            by_name = {s["server_name"]: s for s in listing["servers"]}
            assert by_name["proj-http"]["source"] == "project"
            assert by_name["proj-http"]["read_only"] is True
            assert by_name["proj-http"]["params"]["headers"]["Authorization"] == {
                "is_set": True
            }
            assert listing["user_mcp_allowed"] is True

            # Create a user-layer server carrying secrets in both channels.
            created = a.result(
                "mcp_create_server",
                server_name="hub",
                type="stdio",
                params={
                    "command": "echo",
                    "env": {"API_KEY": ENV_SECRET},
                },
            )
            assert created["ok"] is True
            assert created["server"]["source"] == "user"
            assert created["server"]["params"]["env"]["API_KEY"] == {"is_set": True}
            stored = json.loads(env.user_mcp.read_text())
            assert stored["mcpServers"]["hub"]["env"]["API_KEY"] == ENV_SECRET

            # Write-only merge on update: set one, keep the (unreadable) rest.
            updated = a.result(
                "mcp_update_server",
                server_name="hub",
                params={"env": {"OTHER": NEW_HEADER_SECRET}},
            )
            assert set(updated["server"]["params"]["env"]) == {"API_KEY", "OTHER"}
            stored = json.loads(env.user_mcp.read_text())["mcpServers"]["hub"]
            assert stored["env"]["API_KEY"] == ENV_SECRET
            assert stored["env"]["OTHER"] == NEW_HEADER_SECRET

            # Typed validation error — never echoing the submitted secret.
            invalid = a.error(
                "mcp_create_server",
                server_name="bad",
                type="streamable_http",
                params={"headers": {"Authorization": NEW_HEADER_SECRET}},
            )
            assert invalid["code"] == -32071
            assert invalid["data"]["kind"] == "mcp_config_invalid"
            assert {e["code"] for e in invalid["data"]["errors"]} == {"url_required"}

            # Read-only and not-found are typed.
            read_only = a.error(
                "mcp_update_server",
                server_name="proj-http",
                params={"url": "http://x.test"},
            )
            assert read_only["code"] == -32072
            assert read_only["data"]["reason"] == "project_manifest"
            missing = a.error("mcp_get_server", server_name="ghost")
            assert missing["code"] == -32070

            # Second client sees the created server on its own read.
            b_listing = b.result("mcp_list_servers")
            assert "hub" in {s["server_name"] for s in b_listing["servers"]}

            deleted = a.result("mcp_delete_server", server_name="hub")
            assert deleted["ok"] is True
            assert "hub" not in json.loads(env.user_mcp.read_text())["mcpServers"]

            # No frame either socket received ever carried a secret.
            _assert_no_secret_frames(a, b)
        finally:
            cm_a.__exit__(None, None, None)
            cm_b.__exit__(None, None, None)
    finally:
        Debug.set_enabled(False)

    # ...nor anything the server wrote to stdout/stderr, including the
    # debug logging of inbound mcp_create_server/mcp_update_server params.
    captured = capsys.readouterr()
    assert "mcp_create_server" in captured.err
    for secret in ALL_SECRETS:
        assert secret not in captured.out
        assert secret not in captured.err


# ---------------------------------------------------------------------------
# Startup transitions: live broadcast + reconnect survival
# ---------------------------------------------------------------------------


def test_status_transitions_broadcast_and_survive_reconnect(env):
    context = _make_context(env, mcp_servers=[_elicit_server_config()])
    cm, a = _connect(context)
    try:
        # A management probe drives the standardized transitions against a
        # REAL stdio MCP server and populates the discovery snapshot.
        snapshot = a.result(
            "mcp_get_server", server_name="elicit-fixture", refresh=True
        )
        assert snapshot["status"]["state"] == "ready"
        assert snapshot["server_info"]["name"] == "elicit-fixture"
        assert {t["name"] for t in snapshot["tools"]} >= {"ping", "ask_name"}
        assert snapshot["counts"]["tools"] >= 2

        starting = a.wait_for("mcp_server_status_changed")
        assert (starting["server_name"], starting["status"]) == (
            "elicit-fixture",
            "starting",
        )
        ready = a.wait_for("mcp_server_status_changed")
        assert ready["status"] == "ready"
        assert ready["previous_status"] == "starting"

        # A broken server yields a FAILED transition with a typed reason.
        a.result(
            "mcp_create_server",
            server_name="broken",
            type="stdio",
            params={"command": "/nonexistent/omni-mcp-binary"},
        )
        failure = a.error("mcp_get_server", server_name="broken", refresh=True)
        assert failure["code"] == -32074
        assert failure["data"]["kind"] == "mcp_server_unavailable"
        failed_event = None
        while True:
            event = a.wait_for("mcp_server_status_changed")
            if event["server_name"] == "broken" and event["status"] == "failed":
                failed_event = event
                break
        assert failed_event["reason_code"] in ("command_not_found", "start_failed")
    finally:
        cm.__exit__(None, None, None)

    # The transitions survive the reconnect: a brand-new connection
    # refetches the current picture (broadcast-not-journaled contract).
    cm2, b = _connect(context)
    try:
        listing = b.result("mcp_list_servers")
        by_name = {s["server_name"]: s for s in listing["servers"]}
        assert by_name["elicit-fixture"]["status"]["state"] == "ready"
        assert by_name["elicit-fixture"]["status"]["origin"] == "probe"
        assert by_name["broken"]["status"]["state"] == "failed"
        assert by_name["broken"]["status"]["reason_code"] in (
            "command_not_found",
            "start_failed",
        )
        # Reload resets the standardized state to `configured`.
        reloaded = b.result("mcp_reload_server", server_name="broken")
        assert reloaded["ok"] is True
        after = b.result("mcp_get_server", server_name="broken")
        assert after["status"]["state"] == "configured"
    finally:
        cm2.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# MCP elicitations route through the #308 protocol
# ---------------------------------------------------------------------------


def test_mcp_form_elicitation_roundtrip_via_308_protocol(env):
    context = _make_context(env, mcp_servers=[_elicit_server_config()])
    session_id = "mcp-elicit-sess"
    cm_a, a = _connect(context)  # answers the elicitation
    cm_b, b = _connect(context, experimental=("mcp_call_tool",))  # calls the tool
    try:
        # Attach client A to the session so it receives session events.
        a.result("server_call", function="session.ensure", session_id=session_id)

        # Client B invokes the MCP tool that elicits; its socket blocks on
        # the call while client A answers the prompt.
        call_id = b.send(
            "mcp_call_tool",
            server_name="elicit-fixture",
            tool_name="ask_name",
            session_id=session_id,
        )

        requested = _wait_for_nudged(a, "elicitation_requested")
        assert requested["kind"] == "form"
        assert requested["source"] == "mcp:elicit-fixture"
        assert requested["message"] == "What is your name?"
        assert requested["input_schema"]["required"] == ["name"]
        assert requested["session_id"] == session_id

        answer = a.result(
            "elicitation_response",
            elicitation_id=requested["elicitation_id"],
            action="accept",
            value={"name": "Ada"},
        )
        assert answer["status"] == "accepted"
        resolved = a.wait_for("elicitation_resolved")
        assert resolved["status"] == "accepted"

        tool_result = b.wait(call_id)
        assert "error" not in tool_result, tool_result
        content = tool_result["result"]["result"]["content"]
        assert content[0]["text"] == "hello Ada"
    finally:
        cm_a.__exit__(None, None, None)
        cm_b.__exit__(None, None, None)


def test_mcp_elicitation_decline_maps_back_to_server(env):
    context = _make_context(env, mcp_servers=[_elicit_server_config()])
    session_id = "mcp-decline-sess"
    cm_a, a = _connect(context)
    cm_b, b = _connect(context, experimental=("mcp_call_tool",))
    try:
        a.result("server_call", function="session.ensure", session_id=session_id)
        call_id = b.send(
            "mcp_call_tool",
            server_name="elicit-fixture",
            tool_name="ask_name",
            session_id=session_id,
        )
        requested = _wait_for_nudged(a, "elicitation_requested")
        a.result(
            "elicitation_response",
            elicitation_id=requested["elicitation_id"],
            action="decline",
        )
        tool_result = b.wait(call_id)
        content = tool_result["result"]["result"]["content"]
        assert content[0]["text"] == "no answer (decline)"
    finally:
        cm_a.__exit__(None, None, None)
        cm_b.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# OAuth over the wire: URL handoff, completion, reauth-on-expiry cue
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def oauth_rs():
    """The fake MCP resource + authorization server on a real local port.

    The OAuth probes (both the broker's flow driver and the SDK's
    streamable-http transport during ``mcp_get_server refresh``) create
    their own HTTP clients, so the fixture must be reachable over real
    loopback HTTP rather than an in-memory ASGI transport.
    """
    import socket
    import threading

    import uvicorn

    with socket.socket() as probe_sock:
        probe_sock.bind(("127.0.0.1", 0))
        port = probe_sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    state: dict = {}
    config = uvicorn.Config(
        make_fake_oauth_app(state, base=base),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "fake OAuth server failed to start"
    yield SimpleNamespace(base=base, server_url=f"{base}/mcp", state=state)
    server.should_exit = True
    thread.join(timeout=5)


def test_oauth_flow_to_url_handoff_and_completion_over_ws(env, oauth_rs):
    context = _make_context(env)
    cm, a = _connect(context)
    try:
        a.result(
            "mcp_create_server",
            server_name="oauth-srv",
            type="streamable_http",
            params={"url": oauth_rs.server_url},
        )
        started = a.result("mcp_auth_start", server_name="oauth-srv")
        assert started["state"] == "pending"
        assert started["auth_url"].startswith(f"{oauth_rs.base}/authorize?")
        auth_id = started["auth_id"]

        snapshot = a.result("mcp_get_server", server_name="oauth-srv")
        assert snapshot["auth"] == {"kind": "oauth", "state": "pending"}

        done = a.result("mcp_auth_complete", auth_id=auth_id, code=AUTH_CODE)
        assert done == {
            "state": "completed",
            "server_name": "oauth-srv",
            "auth_state": "authenticated",
        }
        snapshot = a.result("mcp_get_server", server_name="oauth-srv")
        assert snapshot["auth"] == {"kind": "oauth", "state": "authenticated"}

        # stdio transports have no OAuth — typed capability error.
        a.result(
            "mcp_create_server",
            server_name="stdio-srv",
            type="stdio",
            params={"command": "echo"},
        )
        unsupported = a.error("mcp_auth_start", server_name="stdio-srv")
        assert unsupported["code"] == -32073

        # Unknown flow id is typed too.
        stale = a.error("mcp_auth_complete", auth_id=auth_id, code=AUTH_CODE)
        assert stale["code"] == -32075
        assert stale["data"]["reason"] == "unknown_auth"

        _assert_no_secret_frames(a)
    finally:
        cm.__exit__(None, None, None)


def test_expired_credentials_emit_typed_reauth_event(env, oauth_rs):
    """A failed session/probe start on expired tokens must produce the
    typed cue (`auth_expired` + `auth_state: "expired"`) that tells the
    client to begin `mcp_auth_start`, live AND across a reconnect."""
    context = _make_context(env)
    cm, a = _connect(context)
    a.result(
        "mcp_create_server",
        server_name="oauth-srv",
        type="streamable_http",
        params={"url": oauth_rs.server_url},
    )
    # Seed stale stored tokens (the RS rejects them and the fake AS
    # refuses the refresh grant — see test_mcp_oauth for the raw raise),
    # then drive the standardized failure chokepoint via a real probe.
    broker = context.service.mcp_manager.oauth
    storage = broker.storage_for("oauth-srv")
    storage._write(
        {
            "tokens": {
                "access_token": "stale-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "stale-refresh",
            },
            "client_info": {
                "client_id": "test-client-id",
                "redirect_uris": ["http://localhost:53535/mcp/oauth/callback"],
                "token_endpoint_auth_method": "none",
            },
        }
    )
    try:
        failure = a.error("mcp_get_server", server_name="oauth-srv", refresh=True)
        assert failure["code"] == -32074
        assert failure["data"]["reason_code"] == "auth_expired"
        while True:
            event = a.wait_for("mcp_server_status_changed")
            if event["server_name"] == "oauth-srv" and event["status"] == "failed":
                break
        assert event["reason_code"] == "auth_expired"
        assert event["auth_state"] == "expired"
        _assert_no_secret_frames(a)
    finally:
        cm.__exit__(None, None, None)

    # Reconnect: the expired-auth state is refetchable, not lost.
    cm2, b = _connect(context)
    try:
        snapshot = b.result("mcp_get_server", server_name="oauth-srv")
        assert snapshot["auth"] == {"kind": "oauth", "state": "expired"}
        assert snapshot["status"]["state"] == "failed"
        assert snapshot["status"]["reason_code"] == "auth_expired"
    finally:
        cm2.__exit__(None, None, None)


def test_url_elicitation_completes_auth_in_session(env, oauth_rs):
    """`mcp_auth_start(session_id=…)` surfaces the URL handoff as a
    url-kind elicitation through the #308 protocol; accepting with the
    redirect URL completes the flow with no separate mcp_auth_complete."""
    context = _make_context(env)
    session_id = "mcp-auth-sess"
    cm, a = _connect(context)
    try:
        a.result("server_call", function="session.ensure", session_id=session_id)
        a.result(
            "mcp_create_server",
            server_name="oauth-srv",
            type="streamable_http",
            params={"url": oauth_rs.server_url},
        )
        started = a.result(
            "mcp_auth_start", server_name="oauth-srv", session_id=session_id
        )
        assert started["state"] == "pending"

        requested = a.wait_for("elicitation_requested")
        assert requested["kind"] == "url"
        assert requested["source"] == "mcp:oauth-srv"
        assert requested["url"] == started["auth_url"]

        a.result(
            "elicitation_response",
            elicitation_id=requested["elicitation_id"],
            action="accept",
            value={"url": f"{started['redirect_uri']}?code={AUTH_CODE}"},
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            snapshot = a.result("mcp_get_server", server_name="oauth-srv")
            if snapshot["auth"]["state"] == "authenticated":
                break
            time.sleep(0.05)
        assert snapshot["auth"] == {"kind": "oauth", "state": "authenticated"}
        _assert_no_secret_frames(a)
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Authorization: enforced-mode denials & cross-user scoping
# ---------------------------------------------------------------------------


def test_enforced_mode_refuses_mutations_but_allows_reads(env):
    context = _make_context(env, auth_token="secret")
    cm, a = _connect(context, headers={"Authorization": "Bearer secret"})
    try:
        listing = a.result("mcp_list_servers")
        assert "servers" in listing

        for method, params in [
            (
                "mcp_create_server",
                {"server_name": "x", "type": "stdio", "params": {"command": "echo"}},
            ),
            ("mcp_update_server", {"server_name": "x"}),
            ("mcp_delete_server", {"server_name": "x"}),
            ("mcp_reload_server", {}),
            ("mcp_auth_start", {"server_name": "x"}),
            ("mcp_auth_complete", {"auth_id": "a", "code": "c"}),
            ("mcp_auth_cancel", {"auth_id": "a"}),
        ]:
            denied = a.error(method, **params)
            assert denied["code"] == -32014, method
            assert denied["data"]["kind"] == "authorization_denied", method
    finally:
        cm.__exit__(None, None, None)


def test_cross_user_scoping_and_session_id_cannot_rescope_auth(env, monkeypatch):
    # Sessions in enforced mode are created through start_run (which owns
    # them to the caller); mock the agent bridge so no model is invoked.
    from omniagents.core.agents import service as service_mod
    from omniagents.core.runtime import bridge as runner_bridge

    async def mock_stream_agent(agent, prompt, sess, channel, **kwargs):
        sess.history.append({"role": "user", "content": prompt})

    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", mock_stream_agent)

    context = _make_context(
        env,
        mcp_servers=[
            MCPServerConfig(
                name="oauth-srv",
                type="streamable_http",
                params={"url": SERVER_URL},
            )
        ],
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        },
    )
    alice_headers = {"Authorization": "Bearer alice-token"}
    bob_headers = {"Authorization": "Bearer bob-token"}
    cm_a, alice = _connect(context, headers=alice_headers)
    cm_b, bob = _connect(context, headers=bob_headers, experimental=("mcp_call_tool",))
    try:
        started = alice.result("start_run", prompt="hi")
        alice_session = started["session_id"]

        # Bob cannot act on Alice's session through the MCP ops lane.
        denied = bob.error(
            "mcp_call_tool",
            server_name="oauth-srv",
            tool_name="ping",
            session_id=alice_session,
        )
        assert denied["code"] == -32014
        assert denied["data"]["resource"] == "session"

        # The optional session_id on mcp_auth_start cannot re-scope the
        # credential mutation to session ownership in enforced mode —
        # even for the session's own owner.
        rescope = alice.error(
            "mcp_auth_start", server_name="oauth-srv", session_id=alice_session
        )
        assert rescope["code"] == -32014
        assert rescope["data"]["kind"] == "authorization_denied"
    finally:
        cm_a.__exit__(None, None, None)
        cm_b.__exit__(None, None, None)
