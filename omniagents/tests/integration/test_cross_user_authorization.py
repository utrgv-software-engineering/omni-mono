"""Adversarial cross-user authorization tests over the real /ws surface.

Two authenticated principals (alice, bob) talk to one server. Every
resource class — sessions, runs, approvals, terminals, listings — must be
invisible and untouchable across users. Denials surface as JSON-RPC error
``-32014`` with ``data.kind == "authorization_denied"``.
"""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from types import SimpleNamespace

from omniagents.backends.server import app as server_app
from omniagents.execution import register_host_environment
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime import bridge as runner_bridge
from omniagents.rpc.auth import RPC_ERROR_AUTHORIZATION_DENIED
from omniagents.rpc.base import RpcMethodsBase
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from agents import Agent

ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}


async def mock_build_agent(settings, mcp, app):
    return Agent(
        name="authz_agent",
        tools=[],
        instructions="Authz",
        model="gpt-3.5-turbo",
    )


_RELEASE_RUNS = asyncio.Event()


async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20, **kwargs):
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)
    if prompt.startswith("SLOW"):
        # Keep the run active so stop_run / send_user_message have a
        # live target; released (or cancelled) at test teardown.
        try:
            await asyncio.wait_for(_RELEASE_RUNS.wait(), timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


class RpcWs:
    """Minimal JSON-RPC helper over a TestClient websocket."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.notifications = []

    def call(self, method, **params):
        if method == "start_run":
            params.setdefault("environment_selection", {"mode": "none"})
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

    def expect_denied(self, method, **params):
        message = self.call(method, **params)
        assert "error" in message, f"{method} unexpectedly succeeded: {message}"
        error = message["error"]
        assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED, error
        assert error["data"]["kind"] == "authorization_denied", error
        return error

    def handshake(self, experimental=()):
        self.result(
            "initialize",
            protocol_version="1.0.0",
            identity={"name": "authz-tests", "version": "0.0.0"},
            platform={"os": "linux", "arch": "x86_64"},
            capabilities={
                "realtime": False,
                "mcp_apps": False,
                "client_functions": False,
                "approvals": True,
                "artifacts": False,
                "replay": True,
                "terminal": True,
                "experimental_operations": list(experimental),
                "disabled_notifications": [],
            },
        )
        self.ws.send_json({"jsonrpc": "2.0", "method": "initialized", "params": {}})


@pytest.fixture
def authz_context(monkeypatch):
    # service.py does ``from ...bridge import stream_agent``, so the module
    # attribute alone is not enough — patch the bound reference as well or
    # runs hit the real agent path.
    from omniagents.core.agents import service as service_mod

    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", mock_stream_agent)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    _RELEASE_RUNS.clear()

    spec = AgentSpec(
        name="AuthzAgent",
        welcome_text="Authz",
        get_agent_instructions=lambda: "Authz",
        build_agent=mock_build_agent,
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        },
    )

    app = server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    client = TestClient(app)
    service = app.state.agent_service
    yield SimpleNamespace(client=client, service=service)
    _RELEASE_RUNS.set()


def _connect(context, headers, experimental=()):
    cm = context.client.websocket_connect("/ws", headers=headers)
    ws = cm.__enter__()
    rpc = RpcWs(ws)
    rpc.handshake(experimental=experimental)
    return cm, rpc


def test_wrong_token_rejected(authz_context):
    with pytest.raises(WebSocketDisconnect):
        with authz_context.client.websocket_connect(
            "/ws", headers={"Authorization": "Bearer mallory"}
        ):
            pass


def test_start_run_authorizes_nested_explicit_environment(authz_context):
    descriptor = register_host_environment(
        authz_context.service.agent_host_resources,
        "/tmp",
        owner_user_id="alice",
        workspace_id="workspace-alice-explicit",
        environment_id="environment-alice-explicit",
    )
    alice_cm, alice = _connect(authz_context, ALICE)
    bob_cm, bob = _connect(authz_context, BOB)
    selection = {
        "mode": "explicit",
        "environment_id": descriptor.environment_id,
        "environment_generation": descriptor.generation,
    }
    try:
        started = alice.result(
            "start_run",
            prompt="alice explicit environment",
            environment_selection=selection,
        )
        assert started["session_id"]
        bob.expect_denied(
            "start_run",
            prompt="bob environment hijack",
            environment_selection=selection,
        )
    finally:
        alice_cm.__exit__(None, None, None)
        bob_cm.__exit__(None, None, None)


def test_cross_user_session_access_denied(authz_context):
    alice_cm, alice = _connect(
        authz_context, ALICE, experimental=("fork_session", "export_session")
    )
    bob_cm, bob = _connect(
        authz_context, BOB, experimental=("fork_session", "export_session")
    )
    try:
        run = alice.result("start_run", prompt="hello from alice")
        session_id = run["session_id"]

        # Bob cannot read, resume, mutate, export, fork, archive or
        # delete alice's session.
        bob.expect_denied("get_session_history", session_id=session_id)
        bob.expect_denied("start_run", prompt="hijack", session_id=session_id)
        bob.expect_denied("enqueue_message", session_id=session_id, content="hi")
        bob.expect_denied("list_queue", session_id=session_id)
        bob.expect_denied("cancel_queued_message", session_id=session_id, item_id="x")
        bob.expect_denied("export_session", session_id=session_id)
        bob.expect_denied("fork_session", session_id=session_id)
        bob.expect_denied("archive_session", session_id=session_id)
        bob.expect_denied("delete_session", session_id=session_id)

        # Alice can still use her own session (replay path included).
        history = alice.result("get_session_history", session_id=session_id)
        assert any("hello from alice" in str(item) for item in history)
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_cross_user_replay_cursor_access_denied(authz_context):
    """Durable replay is session-owner-gated: another principal can neither
    read journaled events (resume_session) nor advance/compact cursors
    (ack_events) on a session they do not own — no unowned-session event
    leakage in enforced mode."""
    alice_cm, alice = _connect(authz_context, ALICE)
    bob_cm, bob = _connect(authz_context, BOB)
    try:
        run = alice.result("start_run", prompt="alice replayable turn")
        session_id = run["session_id"]

        bob.expect_denied("resume_session", session_id=session_id, after_seq=0)
        bob.expect_denied(
            "resume_session",
            session_id=session_id,
            stream_id="whatever",
            after_seq=0,
        )
        bob.expect_denied(
            "ack_events", session_id=session_id, stream_id="whatever", seq=1
        )
        # An unknown session is a deny too (fail-closed), never a
        # freshly created journal for the caller.
        bob.expect_denied("resume_session", session_id="no_such_session")

        # The owner's replay path still works end-to-end: the journal
        # returns her stamped events and acks advance her cursor.
        resumed = alice.result("resume_session", session_id=session_id, after_seq=0)
        assert resumed["session_id"] == session_id
        assert isinstance(resumed.get("events"), list)
        assert resumed["last_seq"] >= 1
        stream_id = resumed["stream_id"]
        acked = alice.result(
            "ack_events",
            session_id=session_id,
            stream_id=stream_id,
            seq=resumed["last_seq"],
        )
        assert acked["acknowledged_seq"] == resumed["last_seq"]
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_cross_user_run_control_denied(authz_context):
    alice_cm, alice = _connect(authz_context, ALICE)
    bob_cm, bob = _connect(authz_context, BOB)
    try:
        run = alice.result("start_run", prompt="SLOW run")
        run_id = run["run_id"]

        bob.expect_denied("stop_run", run_id=run_id)
        bob.expect_denied("send_user_message", run_id=run_id, content="stop it")

        # The owner can terminate their own run while it is live.
        assert alice.result("stop_run", run_id=run_id) in (True, False)
        _RELEASE_RUNS.set()
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_unresolvable_run_denies_rather_than_failing_open(authz_context):
    """A run_id that resolves to no live session must DENY, not allow.

    Fail-closed: the server cannot prove the caller owns an unknown run,
    so it refuses instead of treating "no owner found" as "no owner".
    """
    alice_cm, alice = _connect(authz_context, ALICE)
    try:
        alice.expect_denied("stop_run", run_id="run_does_not_exist")
    finally:
        alice_cm.__exit__(None, None, None)


def test_session_listings_are_scoped_per_user(authz_context):
    alice_cm, alice = _connect(authz_context, ALICE)
    bob_cm, bob = _connect(authz_context, BOB)
    try:
        run = alice.result("start_run", prompt="alice private message")
        session_id = run["session_id"]

        alice_sessions = {s["id"] for s in alice.result("list_sessions")}
        bob_sessions = {s["id"] for s in bob.result("list_sessions")}
        assert session_id in alice_sessions
        assert session_id not in bob_sessions

        alice_history = alice.result("get_user_history")
        bob_history = bob.result("get_user_history")
        assert any(
            "alice private message" in item.get("content", "") for item in alice_history
        )
        assert not any(
            "alice private message" in item.get("content", "") for item in bob_history
        )
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_cross_user_terminal_attach_denied(authz_context, monkeypatch):
    """A leaked terminal_token is useless without the owner's identity."""
    from omniagents.builtin.server_functions import terminal as terminal_module
    from omniagents.builtin.server_functions.terminal import terminal_create
    from omniagents.rpc.terminal import TerminalManager
    from tests.unit.rpc.test_terminal_manager import FakePtyBackend

    backend = FakePtyBackend()
    manager = TerminalManager(backend=backend)
    monkeypatch.setattr(terminal_module, "_backend_for_lease", lambda lease: backend)
    authz_context.service.terminal_manager = manager
    authz_context.service.register_server_function(
        "terminal.create",
        terminal_create,
        resource_scope=ResourceScope.THREAD,
        execution_requirements=ExecutionRequirement.PTY,
    )
    descriptor = register_host_environment(
        authz_context.service.agent_host_resources,
        "/tmp",
        owner_user_id="alice",
        workspace_id="workspace-alice-terminal",
        environment_id="environment-alice-terminal",
    )

    alice_cm, alice = _connect(authz_context, ALICE)
    try:
        run = alice.result(
            "start_run",
            prompt="terminal session",
            environment_selection={
                "mode": "explicit",
                "environment_id": descriptor.environment_id,
                "environment_generation": descriptor.generation,
            },
        )
        session_id = run["session_id"]
        created = alice.result(
            "server_call",
            function="terminal.create",
            session_id=session_id,
            environment_id=descriptor.environment_id,
        )
        terminal_id = created["terminal_id"]
        terminal_token = created["terminal_token"]

        attach = {
            "type": "attach",
            "session_id": session_id,
            "terminal_id": terminal_id,
            "terminal_token": terminal_token,
        }

        # Bob authenticates fine but cannot attach to alice's terminal
        # even with the correct one-time terminal token.
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with authz_context.client.websocket_connect(
                "/ws/terminal", headers=BOB
            ) as ws:
                ws.send_json(attach)
                ws.receive_json()
        assert excinfo.value.code == 4403

        # Alice attaches successfully with the same credentials.
        with authz_context.client.websocket_connect(
            "/ws/terminal", headers=ALICE
        ) as ws:
            ws.send_json(attach)
            ws.send_json({"type": "input", "data": base64.b64encode(b"x").decode()})
            ws.send_json({"type": "close"})
            for _ in range(5):
                try:
                    ws.receive_json()
                except WebSocketDisconnect:
                    break
    finally:
        alice_cm.__exit__(None, None, None)


def test_cross_user_approval_resolution_denied(authz_context):
    """Bob cannot approve/reject a tool approval pending on alice's session."""
    service = authz_context.service

    alice_cm, alice = _connect(authz_context, ALICE)
    bob_cm, bob = _connect(authz_context, BOB)
    try:
        run = alice.result("start_run", prompt="approval run")
        session_id = run["session_id"]
        session = service.get_session(session_id)
        assert session is not None and session.user_id == "alice"

        # Register a pending tool approval bound to alice's session, the
        # way the SDK interruption flow does.
        approval_loop = asyncio.new_event_loop()
        loop_future = approval_loop.create_future()
        service._pending_tool_approvals["call-123"] = (loop_future, session)

        bob.expect_denied(
            "tool_approval_response", call_id="call-123", decision="approve"
        )
        assert not loop_future.done()

        result = alice.call(
            "tool_approval_response", call_id="call-123", decision="approve"
        )
        assert "error" not in result
    finally:
        service._pending_tool_approvals.pop("call-123", None)
        approval_loop.close()
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_realtime_sessions_bound_to_principal():
    """RealtimeService authorize_rpc denies cross-user session access."""
    from omniagents.core.providers.auth.base import UserIdentity
    from omniagents.rpc.auth import AuthorizationDenied
    from omniagents.rpc.realtime_service import RealtimeService

    from omniagents.rpc.auth import AuthorizationPolicy

    service = RealtimeService.__new__(RealtimeService)
    service._sessions = {"rt-1": object()}
    service._session_owners = {"rt-1": "alice"}
    service.authorization_policy = AuthorizationPolicy(enforced=True)

    alice = UserIdentity(user_id="alice")
    bob = UserIdentity(user_id="bob")

    async def check():
        # Alice passes; bob is denied for every session-scoped method.
        await service.authorize_rpc(alice, "send_text", {"session_id": "rt-1"})
        for method in (
            "send_audio",
            "send_text",
            "interrupt",
            "stop_session",
            "get_session_info",
        ):
            with pytest.raises(AuthorizationDenied):
                await service.authorize_rpc(bob, method, {"session_id": "rt-1"})
        # Unknown sessions fall through to the method's own not-found error.
        await service.authorize_rpc(bob, "start_session", {"session_id": "rt-2"})

    asyncio.run(check())


def test_realtime_notifications_stay_on_the_principal_s_channel():
    """Concurrent requests retain the channel set in their own task context.

    Realtime methods are shared by every connection.  This forces two tasks
    to set different channels before either reads the value; an ordinary
    instance attribute would make both calls observe the last writer.
    """

    class Channel:
        def __init__(self, principal):
            self.principal = type("Principal", (), {"user_id": principal})()
            self.notifications = []

        async def async_call(self, method, params):
            self.notifications.append({"method": method, "params": params})

    service = RpcMethodsBase()
    alice = Channel("alice")
    bob = Channel("bob")

    async def check():
        ready = 0
        release = asyncio.Event()

        async def request(channel):
            nonlocal ready
            service.channel = channel
            ready += 1
            if ready == 2:
                release.set()
            await release.wait()
            await asyncio.sleep(0)
            principal = service.channel.principal.user_id
            await service.channel.async_call(
                "principal_probe", {"principal": principal}
            )
            return principal

        assert await asyncio.gather(request(alice), request(bob)) == ["alice", "bob"]

    asyncio.run(check())
    assert alice.notifications == [
        {"method": "principal_probe", "params": {"principal": "alice"}}
    ]
    assert bob.notifications == [
        {"method": "principal_probe", "params": {"principal": "bob"}}
    ]


# ---------------------------------------------------------------------------
# Regression tests for the defects found in adversarial review of PR #294.
# Each reproduces the reviewer's proof; all four failed before the fix.
# ---------------------------------------------------------------------------


def test_owner_lookup_failure_denies_instead_of_failing_open(
    authz_context, monkeypatch
):
    """D2: a transient owner-lookup fault let another user read the session.

    An unresolvable owner must DENY (fail closed), never read as "unowned".
    """
    service = authz_context.service

    alice_cm, alice = _connect(authz_context, ALICE)
    try:
        session_id = alice.result("start_run", prompt="alice secret")["session_id"]
    finally:
        alice_cm.__exit__(None, None, None)

    # Simulate a fresh process / evicted live session, then break the
    # persisted owner lookup.
    service._session_manager._by_id.pop(session_id, None)

    def boom(*args, **kwargs):
        raise RuntimeError("transient DB failure")

    import omniagents.core.session.history_db as hdb
    import omniagents.core.session.manager as mgr_mod

    original_hdb = hdb.get_session_user_id
    original_mgr = mgr_mod.get_session_user_id
    hdb.get_session_user_id = boom
    mgr_mod.get_session_user_id = boom

    bob_cm, bob = _connect(authz_context, BOB)
    try:
        error = bob.expect_denied("get_session_history", session_id=session_id)
        assert error["data"]["resource"] == "session"
    finally:
        bob_cm.__exit__(None, None, None)

    # The owner is denied too while the lookup is broken — availability is
    # sacrificed to confidentiality, and ownership is NOT transferred.
    alice2_cm, alice2 = _connect(authz_context, ALICE)
    try:
        alice2.expect_denied("get_session_history", session_id=session_id)
    finally:
        alice2_cm.__exit__(None, None, None)

    hdb.get_session_user_id = original_hdb
    mgr_mod.get_session_user_id = original_mgr

    # Ownership was never transferred by the denied reads.
    assert hdb.get_session_user_id(session_id) == "alice"

    # With the lookup healthy again, the real owner reads it and bob cannot.
    alice3_cm, alice3 = _connect(authz_context, ALICE)
    try:
        history = alice3.result("get_session_history", session_id=session_id)
        assert any("alice secret" in str(item) for item in history)
    finally:
        alice3_cm.__exit__(None, None, None)


def test_global_mutating_methods_denied_in_multi_user_mode(authz_context):
    """D3: any authenticated user could mutate the spec process-wide."""
    service = authz_context.service
    alice_cm, alice = _connect(
        authz_context, ALICE, experimental=("disable_tool", "disable_mcp_server")
    )
    try:
        error = alice.expect_denied("disable_tool", tool_name="bash")
        assert error["data"]["resource"] == "global"
        alice.expect_denied("disable_mcp_server", server_name="github")
        # Nothing was mutated for the other users sharing this process.
        assert "bash" not in (getattr(service.spec, "disabled_tools", None) or set())
    finally:
        alice_cm.__exit__(None, None, None)


def test_unclassified_resourceless_method_is_denied(authz_context):
    """D3: default-DENY — an unclassified global method must not slip in."""
    service = authz_context.service

    async def _rogue() -> bool:
        return True

    service.rogue_global_method = _rogue  # type: ignore[attr-defined]
    alice_cm, alice = _connect(authz_context, ALICE)
    try:
        # Reaches authorize_rpc before dispatch and is refused there
        # (rather than being allowed through to a method-not-found).
        import asyncio

        from omniagents.rpc.auth import AuthorizationDenied

        with pytest.raises(AuthorizationDenied):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                service.authorize_rpc(
                    __import__(
                        "omniagents.core.providers.auth.base",
                        fromlist=["UserIdentity"],
                    ).UserIdentity(user_id="alice"),
                    "rogue_global_method",
                    {},
                )
            )
    finally:
        alice_cm.__exit__(None, None, None)


def test_listings_are_scoped_in_sql_not_after_pagination(authz_context):
    """D4: paging happened in SQL and filtering in Python, so a page could
    come back missing the caller's own sessions."""
    alice_cm, alice = _connect(authz_context, ALICE)
    try:
        a1 = alice.result("start_run", prompt="alice one")["session_id"]
        a2 = alice.result("start_run", prompt="alice two")["session_id"]
    finally:
        alice_cm.__exit__(None, None, None)

    bob_cm, bob = _connect(authz_context, BOB)
    try:
        for i in range(3):
            bob.result("start_run", prompt=f"bob {i}")
    finally:
        bob_cm.__exit__(None, None, None)

    alice2_cm, alice2 = _connect(authz_context, ALICE)
    try:
        page = alice2.result("list_sessions", limit=3, offset=0)
        ids = {row.get("id") for row in page}
        # Alice owns exactly two sessions; a page of three must contain both
        # and nothing of bob's.
        assert ids == {a1, a2}
        # get_user_history spends its limit on the caller's own rows.
        history = alice2.result("get_user_history", limit=3)
        contents = " ".join(item.get("content", "") for item in history)
        assert "alice one" in contents and "alice two" in contents
        assert "bob " not in contents
    finally:
        alice2_cm.__exit__(None, None, None)


def test_cross_user_model_catalog_access_denied(authz_context):
    """Model-catalog RPCs inherit the resource-scoping rules: naming a
    session (even on the otherwise global-safe ``list_models``) gates the
    request on session ownership, and the global-mutating voice default
    is refused per-principal in multi-user mode."""
    alice_cm, alice = _connect(authz_context, ALICE)
    bob_cm, bob = _connect(authz_context, BOB)
    try:
        session_id = alice.result("start_run", prompt="hello from alice")["session_id"]

        # list_models is global-safe without a session, but supplying a
        # session_id auto-scopes it — bob must not learn alice's active
        # selection through the catalog read.
        error = bob.expect_denied("list_models", session_id=session_id)
        assert error["data"]["resource"] == "session"
        bob.expect_denied("set_session_model", session_id=session_id, model="gpt-5.1")
        bob.expect_denied("set_session_reasoning", session_id=session_id, effort="high")

        # The voice default is process-wide state: refused per-principal.
        error = alice.expect_denied("set_voice_model", model="gpt-realtime")
        assert error["data"]["resource"] == "global"

        # Resourceless catalog reads stay available to every principal.
        assert "models" in alice.result("list_models")
        assert "providers" in alice.result("list_providers")
        assert "found" in bob.result("get_model", model="anything")
        # Alice keeps catalog access to her own session (this deployment
        # has no model store, so the result degrades to the typed
        # no_model_store shape — the point is that she is not denied).
        assert "models" in alice.result("list_models", session_id=session_id)
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_legacy_unowned_session_is_not_readable_and_not_claimed(authz_context):
    """D5: an unowned legacy session was world-readable and was silently
    transferred to whoever touched it first, locking out its real owner."""
    from omniagents.core.session.history_db import get_session_user_id, register_session

    register_session("legacy-session")

    bob_cm, bob = _connect(authz_context, BOB)
    try:
        bob.expect_denied("get_session_history", session_id="legacy-session")
    finally:
        bob_cm.__exit__(None, None, None)

    # Bob's attempt did not make him the owner.
    assert get_session_user_id("legacy-session") is None

    alice_cm, alice = _connect(authz_context, ALICE)
    try:
        alice.expect_denied("get_session_history", session_id="legacy-session")
    finally:
        alice_cm.__exit__(None, None, None)
    assert get_session_user_id("legacy-session") is None


def test_documented_backfill_assigns_legacy_sessions(authz_context):
    """D5 migration path: the explicit backfill is how legacy sessions get
    an owner — never an implicit claim-on-access."""
    from omniagents.core.session.backfill import (
        backfill_session_owners,
        find_unowned_sessions,
    )
    from omniagents.core.session.history_db import get_session_user_id, register_session

    register_session("legacy-a")
    register_session("legacy-b")
    assert set(find_unowned_sessions()) >= {"legacy-a", "legacy-b"}

    planned = backfill_session_owners("alice", dry_run=True)
    assert set(planned) >= {"legacy-a", "legacy-b"}
    assert get_session_user_id("legacy-a") is None  # dry run wrote nothing

    backfill_session_owners("alice")
    assert get_session_user_id("legacy-a") == "alice"
    assert get_session_user_id("legacy-b") == "alice"

    alice_cm, alice = _connect(authz_context, ALICE)
    try:
        assert alice.result("get_session_history", session_id="legacy-a") == []
    finally:
        alice_cm.__exit__(None, None, None)

    bob_cm, bob = _connect(authz_context, BOB)
    try:
        bob.expect_denied("get_session_history", session_id="legacy-a")
    finally:
        bob_cm.__exit__(None, None, None)


def test_single_user_mode_keeps_permissive_behavior(monkeypatch):
    """No-auth deployments are unaffected by ownership enforcement: legacy
    sessions stay readable and global methods still work."""
    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    spec = AgentSpec(
        name="SingleUser",
        welcome_text="Single",
        get_agent_instructions=lambda: "Single",
        build_agent=mock_build_agent,
    )
    app = server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    client = TestClient(app)
    from omniagents.core.session.history_db import register_session

    register_session("legacy-single")

    with client.websocket_connect("/ws") as ws:
        rpc = RpcWs(ws)
        rpc.handshake(experimental=("disable_tool",))
        assert rpc.result("get_session_history", session_id="legacy-single") == []
        assert rpc.result("disable_tool", tool_name="bash") in (True, False, None)
