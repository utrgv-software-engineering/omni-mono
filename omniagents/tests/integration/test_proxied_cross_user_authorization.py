"""Cross-user authorization THROUGH a real serve-proxy hop (tkt_-U4zoPDzJBUk).

``tests/integration/test_cross_user_authorization.py`` proves the
ownership model on a direct ``/ws`` connection. This module proves it
survives the proxy topology that ``remote_ws_url`` creates:

    alice / bob  ──ws──▶  proxy (token auth: alice, bob)
                              │  mints a signed identity assertion
                              ▼
                          upstream (proxy_assertion auth, real AgentService)

Both hops are **real uvicorn servers on real sockets**, and the clients
are real ``websockets`` connections — every frame in these tests crosses
the proxy. Nothing here would pass with the proxy removed from the path,
and nothing here asserts on an authorizer in isolation.

Before the fix, the proxy presented one shared ``remote_token`` upstream,
so the upstream saw a single principal for every local user and its
ownership rules had nothing to separate.
"""

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager

import pytest
import uvicorn
import websockets

from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from agents import Agent

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.providers.auth.none import NoneAuthProvider
from omniagents.core.runtime import bridge as runner_bridge
from omniagents.rpc.auth import RPC_ERROR_AUTHORIZATION_DENIED

ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}

IDENTITY_SECRET_ENV = "OMNIAGENTS_TEST_PROXY_IDENTITY_SECRET"
IDENTITY_SECRET = "proxy-identity-signing-secret-value-0123456789"
SHARED_TOKEN_ENV = "OMNIAGENTS_TEST_PROXY_SHARED_TOKEN"
SHARED_TOKEN = "one-shared-upstream-token"


# ---------------------------------------------------------------------------
# Agent stubs — no model traffic; runs just record the prompt.
# ---------------------------------------------------------------------------


async def _build_agent(settings, mcp, app):
    return Agent(name="proxy_authz", tools=[], instructions="", model="gpt-3.5-turbo")


async def _stream_agent(agent, prompt, sess, channel, max_turns=20, **kwargs):
    from omniagents.core.session import append_message

    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    append_message(sess.id, user_msg)


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _serve(app, port):
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task


def _upstream_spec() -> AgentSpec:
    return AgentSpec(
        name="Upstream",
        welcome_text="upstream",
        get_agent_instructions=lambda: "",
        build_agent=_build_agent,
        security_config={
            "providers": {
                "auth": {
                    "type": "proxy_assertion",
                    "signing_secret": f"${{{IDENTITY_SECRET_ENV}}}",
                }
            }
        },
    )


def _proxy_spec(upstream_port: int, **overrides) -> AgentSpec:
    kwargs = dict(
        name="Proxy",
        welcome_text="proxy",
        get_agent_instructions=lambda: "",
        build_agent=_build_agent,
        remote_ws_url=f"ws://127.0.0.1:{upstream_port}/ws",
        remote_identity_secret_key=IDENTITY_SECRET_ENV,
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        },
    )
    kwargs.update(overrides)
    return AgentSpec(**kwargs)


@asynccontextmanager
async def proxied_topology():
    """Start a real upstream and a real proxy in front of it."""
    os.environ[IDENTITY_SECRET_ENV] = IDENTITY_SECRET

    upstream_app = server_app.build_app(
        spec=_upstream_spec(), default_host_workspace_root="/tmp"
    )
    upstream_port = _free_port()
    upstream_server, upstream_task = await _serve(upstream_app, upstream_port)

    proxy_app = server_app.build_app(
        spec=_proxy_spec(upstream_port), default_host_workspace_root="/tmp"
    )
    proxy_port = _free_port()
    proxy_server, proxy_task = await _serve(proxy_app, proxy_port)

    try:
        yield {
            "proxy_url": f"ws://127.0.0.1:{proxy_port}/ws",
            "upstream_url": f"ws://127.0.0.1:{upstream_port}/ws",
            "upstream_service": upstream_app.state.agent_service,
            "proxy_app": proxy_app,
        }
    finally:
        proxy_server.should_exit = True
        await proxy_task
        upstream_server.should_exit = True
        await upstream_task
        os.environ.pop(IDENTITY_SECRET_ENV, None)


# ---------------------------------------------------------------------------
# JSON-RPC over a real websocket
# ---------------------------------------------------------------------------


class ProxiedRpc:
    """JSON-RPC helper whose every frame traverses the proxy hop."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.notifications = []

    async def call(self, method, **params):
        self.next_id += 1
        call_id = f"c{self.next_id}"
        await self.ws.send(
            json.dumps(
                {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
            )
        )
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=20)
            message = json.loads(raw)
            if message.get("id") == call_id and (
                "result" in message or "error" in message
            ):
                return message
            self.notifications.append(message)

    async def result(self, method, **params):
        message = await self.call(method, **params)
        assert "error" not in message, message
        return message["result"]

    async def expect_denied(self, method, **params):
        """Assert -32014 came back — through the proxy — for *method*."""
        message = await self.call(method, **params)
        assert "error" in message, f"{method} unexpectedly succeeded: {message}"
        error = message["error"]
        assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED, error
        assert error["data"]["kind"] == "authorization_denied", error
        return error

    async def handshake(self, experimental=()):
        await self.result(
            "initialize",
            protocol_version="1.0.0",
            identity={"name": "proxy-authz-tests", "version": "0.0.0"},
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
        await self.ws.send(
            json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        )


@asynccontextmanager
async def connected(url, headers, experimental=()):
    ws = await server_app._connect_remote(url, dict(headers))
    try:
        rpc = ProxiedRpc(ws)
        await rpc.handshake(experimental=experimental)
        yield rpc
    finally:
        await ws.close()


def _patch_agent_runtime(monkeypatch):
    # ``service.py`` does ``from ...bridge import stream_agent``, so the
    # bound reference has to be patched as well.
    from omniagents.core.agents import service as service_mod

    monkeypatch.setattr(runner_bridge, "stream_agent", _stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", _stream_agent)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)


# ---------------------------------------------------------------------------
# The acceptance criteria
# ---------------------------------------------------------------------------


def test_proxied_users_reach_upstream_as_distinct_principals(monkeypatch):
    """Upstream authorization decisions see ``alice`` and ``bob``, not one
    shared service account. This is the whole ticket in one assertion."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        async with proxied_topology() as topo:
            async with connected(topo["proxy_url"], ALICE) as alice:
                alice_session = (
                    await alice.result("start_run", prompt="alice via proxy")
                )["session_id"]
            async with connected(topo["proxy_url"], BOB) as bob:
                bob_session = (await bob.result("start_run", prompt="bob via proxy"))[
                    "session_id"
                ]

            service = topo["upstream_service"]
            # The upstream — the process that actually runs authorize_rpc —
            # recorded two different owners for the two proxied users.
            assert service.get_session(alice_session).user_id == "alice"
            assert service.get_session(bob_session).user_id == "bob"

    asyncio.run(flow())


def test_bob_via_proxy_cannot_touch_alice_resources_upstream(monkeypatch):
    """The adversarial case: every session-scoped method bob aims at
    alice's session is refused upstream with -32014, and the denial
    travels back down through the proxy to bob's client."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        async with proxied_topology() as topo:
            async with connected(
                topo["proxy_url"], ALICE, experimental=("fork_session",)
            ) as alice:
                run = await alice.result("start_run", prompt="alice private secret")
                session_id = run["session_id"]
                run_id = run["run_id"]

                async with connected(
                    topo["proxy_url"], BOB, experimental=("fork_session",)
                ) as bob:
                    for method, params in (
                        ("get_session_history", {"session_id": session_id}),
                        (
                            "start_run",
                            {"prompt": "hijack", "session_id": session_id},
                        ),
                        (
                            "enqueue_message",
                            {"session_id": session_id, "content": "hi"},
                        ),
                        ("list_queue", {"session_id": session_id}),
                        ("archive_session", {"session_id": session_id}),
                        ("delete_session", {"session_id": session_id}),
                        ("fork_session", {"session_id": session_id}),
                        ("resume_session", {"session_id": session_id, "after_seq": 0}),
                        ("get_thread", {"thread_id": session_id}),
                        ("list_items", {"thread_id": session_id}),
                        ("stop_run", {"run_id": run_id}),
                    ):
                        error = await bob.expect_denied(method, **params)
                        assert error["data"]["resource"] in (
                            "session",
                            "thread",
                            "run",
                        ), (method, error)

                    # Bob's own listing never shows alice's session.
                    bob_sessions = {s["id"] for s in await bob.result("list_sessions")}
                    assert session_id not in bob_sessions

                # Alice's own access over the very same proxy still works —
                # the denials are about identity, not about the hop.
                history = await alice.result(
                    "get_session_history", session_id=session_id
                )
                assert any("alice private secret" in str(item) for item in history)
                alice_sessions = {s["id"] for s in await alice.result("list_sessions")}
                assert session_id in alice_sessions

    asyncio.run(flow())


def test_upstream_refuses_the_proxys_own_local_credentials(monkeypatch):
    """A local token is not an upstream credential.

    The upstream trusts signed assertions only, so a client that learns
    ``bob-token`` (or reaches the upstream directly) cannot authenticate
    there and cannot choose which identity is asserted for it."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        async with proxied_topology() as topo:
            for headers in (BOB, {"Authorization": "Bearer alice-token"}, {}):
                with pytest.raises(websockets.exceptions.WebSocketException):
                    ws = await server_app._connect_remote(
                        topo["upstream_url"], dict(headers)
                    )
                    await ws.close()

    asyncio.run(flow())


def test_identity_assertions_are_single_use(monkeypatch):
    """A captured assertion cannot be replayed to open a second socket."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        async with proxied_topology() as topo:
            from omniagents.core.providers.auth.base import UserIdentity
            from omniagents.rpc.identity_assertion import mint_identity_assertion

            assertion = mint_identity_assertion(
                UserIdentity(user_id="alice"), secret=IDENTITY_SECRET
            )
            headers = {"Authorization": f"Bearer {assertion}"}

            ws = await server_app._connect_remote(topo["upstream_url"], dict(headers))
            await ws.close()

            with pytest.raises(websockets.exceptions.WebSocketException):
                ws = await server_app._connect_remote(
                    topo["upstream_url"], dict(headers)
                )
                await ws.close()

    asyncio.run(flow())


def test_forged_assertion_is_refused_upstream(monkeypatch):
    """Without the signing secret an attacker cannot assert an identity."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        async with proxied_topology() as topo:
            from omniagents.core.providers.auth.base import UserIdentity
            from omniagents.rpc.identity_assertion import mint_identity_assertion

            forged = mint_identity_assertion(
                UserIdentity(user_id="alice", roles=["admin"]),
                secret="an-attacker-controlled-signing-secret-value",
            )
            wrong_audience = mint_identity_assertion(
                UserIdentity(user_id="alice"),
                secret=IDENTITY_SECRET,
                audience="some-other-upstream",
            )
            for token in (forged, wrong_audience):
                with pytest.raises(websockets.exceptions.WebSocketException):
                    ws = await server_app._connect_remote(
                        topo["upstream_url"], {"Authorization": f"Bearer {token}"}
                    )
                    await ws.close()

    asyncio.run(flow())


# ---------------------------------------------------------------------------
# The shared-token path is gated, not silent
# ---------------------------------------------------------------------------


def test_multi_user_proxy_refuses_to_start_with_a_shared_upstream_token(monkeypatch):
    """The regression guard for this ticket's root cause.

    A proxy that authenticates distinct local users may not present the
    upstream one shared identity — it fails at startup instead of
    silently collapsing every principal."""
    monkeypatch.setenv(SHARED_TOKEN_ENV, SHARED_TOKEN)
    monkeypatch.delenv(IDENTITY_SECRET_ENV, raising=False)

    spec = _proxy_spec(
        9999,
        remote_identity_secret_key=None,
        remote_auth_token_key=SHARED_TOKEN_ENV,
    )
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    assert "identity_secret_env" in str(excinfo.value)

    # …and also with no upstream credential at all.
    spec = _proxy_spec(9999, remote_identity_secret_key=None)
    with pytest.raises(ValueError):
        server_app.build_app(spec=spec, default_host_workspace_root="/tmp")


def test_single_tenant_proxy_may_still_use_a_shared_upstream_token(monkeypatch):
    """The documented single-tenant carve-out: when the hop identifies no
    principals there is one user, so one upstream identity is honest."""
    monkeypatch.setenv(SHARED_TOKEN_ENV, SHARED_TOKEN)
    monkeypatch.delenv(IDENTITY_SECRET_ENV, raising=False)

    spec = _proxy_spec(
        9999,
        remote_identity_secret_key=None,
        remote_auth_token_key=SHARED_TOKEN_ENV,
        security_config=None,
    )
    app = server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    credential = app.state.upstream_credential
    assert isinstance(credential, server_app.SharedUpstreamToken)
    assert credential.preserves_identity is False


def test_proxy_refuses_ambiguous_upstream_credentials(monkeypatch):
    """Two schemes, no precedence rule — configure exactly one."""
    monkeypatch.setenv(SHARED_TOKEN_ENV, SHARED_TOKEN)
    monkeypatch.setenv(IDENTITY_SECRET_ENV, IDENTITY_SECRET)

    spec = _proxy_spec(9999, remote_auth_token_key=SHARED_TOKEN_ENV)
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    assert "exactly one" in str(excinfo.value)


def test_proxy_refuses_an_unresolved_identity_secret(monkeypatch):
    """A typo'd env var must not degrade to the shared-identity path."""
    monkeypatch.delenv(IDENTITY_SECRET_ENV, raising=False)

    spec = _proxy_spec(9999)
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    assert "unset or empty" in str(excinfo.value)


def test_proxy_refuses_a_weak_signing_secret(monkeypatch):
    monkeypatch.setenv(IDENTITY_SECRET_ENV, "short")

    spec = _proxy_spec(9999)
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    assert "too short" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Hardening from the PR #317 review.
# ---------------------------------------------------------------------------


class GatewayHeaderAuthProvider(NoneAuthProvider):
    """The Azure EasyAuth / oauth2-proxy shape: a provider that resolves a
    distinct principal per connection from a gateway-injected header.

    It subclasses the single-user provider (adding identity resolution to
    it) and declares that it identifies principals. A capability the
    deployment states must be honoured; inferring it from the class is
    what let this collapse silently.
    """

    identifies_principals = True

    async def authenticate(self, request):
        from omniagents.core.providers.auth.base import AuthResult, UserIdentity

        headers = getattr(request, "headers", {}) or {}
        user = headers.get("x-ms-client-principal-name")
        if not user:
            return AuthResult(authenticated=False, error="No gateway principal")
        return AuthResult(authenticated=True, identity=UserIdentity(user_id=user))


def test_gateway_provider_may_not_collapse_onto_a_shared_upstream_token(monkeypatch):
    """M2: the shared-token guard must key off a declared capability, not
    ``isinstance``. A provider that resolves distinct principals gets the
    startup refusal however it is implemented."""
    monkeypatch.setenv(SHARED_TOKEN_ENV, SHARED_TOKEN)
    monkeypatch.delenv(IDENTITY_SECRET_ENV, raising=False)

    spec = _proxy_spec(
        9999,
        remote_identity_secret_key=None,
        remote_auth_token_key=SHARED_TOKEN_ENV,
        security_config=None,
    )
    app = server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    app.state.connection_authenticator.provider = GatewayHeaderAuthProvider()

    with pytest.raises(ValueError) as excinfo:
        server_app._resolve_upstream_credential(
            spec, app.state.connection_authenticator
        )
    assert "identity_secret_env" in str(excinfo.value)


def test_assertion_branch_refuses_a_hop_that_identifies_nobody(monkeypatch):
    """M3: the guard has to be symmetric. An identity secret on a hop with
    no authentication would mint every assertion as ``anonymous`` — and
    upstream, ``enforced=True`` plus one shared owner means every user can
    read every session, while ``preserves_identity`` claims otherwise."""
    monkeypatch.setenv(IDENTITY_SECRET_ENV, IDENTITY_SECRET)

    spec = _proxy_spec(9999, security_config=None)
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    message = str(excinfo.value)
    assert "anonymous" in message


def test_signing_secret_may_not_be_a_client_distributed_credential(monkeypatch):
    """M5: bob holds the proxy's bearer token because he needs it to reach
    the proxy. If that token is also the signing key he mints
    ``sub=alice, roles=[admin]`` and dials the upstream directly."""
    shared = "alice-token-that-is-also-the-signing-secret-0123"
    monkeypatch.setenv(IDENTITY_SECRET_ENV, shared)

    spec = _proxy_spec(
        9999,
        security_config={
            "providers": {
                "auth": {"type": "token", "tokens": {shared: "alice", "b" * 40: "bob"}}
            }
        },
    )
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    assert "credential" in str(excinfo.value).lower()


def test_upstream_close_codes_survive_the_proxy(monkeypatch):
    """S1: bob attaching to alice's terminal is refused upstream with a
    terminal 4403. Flattened to 1000 by the proxy it reads as a clean
    disconnect and the client reconnects forever — the opposite of the
    contract the 4401/4403 codes exist to express."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        from omniagents.builtin.server_functions.terminal import terminal_create
        from omniagents.rpc.terminal import TerminalManager
        from tests.unit.rpc.test_terminal_manager import FakePtyBackend

        async with proxied_topology() as topo:
            service = topo["upstream_service"]
            service.terminal_manager = TerminalManager(backend=FakePtyBackend())
            service.register_server_function(
                "terminal.create",
                terminal_create,
                resource_scope=ResourceScope.THREAD,
                execution_requirements=ExecutionRequirement.PTY,
            )

            async with connected(topo["proxy_url"], ALICE) as alice:
                session_id = (await alice.result("start_run", prompt="terminal"))[
                    "session_id"
                ]
                created = await alice.result(
                    "server_call", function="terminal.create", session_id=session_id
                )

            attach = json.dumps(
                {
                    "type": "attach",
                    "session_id": session_id,
                    "terminal_id": created["terminal_id"],
                    "terminal_token": created["terminal_token"],
                }
            )
            proxy_terminal = topo["proxy_url"] + "/terminal"

            # Bob holds a valid proxy credential and the real terminal
            # token, and is still refused — with a code that survives.
            ws = await server_app._connect_remote(proxy_terminal, dict(BOB))
            await ws.send(attach)
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                for _ in range(5):
                    await asyncio.wait_for(ws.recv(), timeout=10)
            assert ws.close_code == 4403, f"got {ws.close_code}"

            # Control: the owner attaches over the same proxy hop, so the
            # refusal above is about identity and not about the route.
            ws = await server_app._connect_remote(proxy_terminal, dict(ALICE))
            await ws.send(attach)
            await ws.send(json.dumps({"type": "close"}))
            try:
                for _ in range(5):
                    await asyncio.wait_for(ws.recv(), timeout=10)
            except (
                websockets.exceptions.ConnectionClosed,
                asyncio.TimeoutError,
            ):
                pass
            assert ws.close_code != 4403
            await ws.close()

    asyncio.run(flow())


def test_rejected_assertion_closes_the_client_socket_terminally(monkeypatch):
    """S1: when the upstream refuses the assertion the proxy must relay a
    terminal 4401, not a clean close the client will retry through."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        # Proxy and upstream signing secrets disagree, so every assertion
        # is refused upstream.
        os.environ[IDENTITY_SECRET_ENV] = IDENTITY_SECRET
        upstream_app = server_app.build_app(
            spec=_upstream_spec(), default_host_workspace_root="/tmp"
        )
        upstream_port = _free_port()
        upstream_server, upstream_task = await _serve(upstream_app, upstream_port)

        os.environ[IDENTITY_SECRET_ENV] = "a-completely-different-signing-secret-9f2c"
        proxy_app = server_app.build_app(
            spec=_proxy_spec(upstream_port), default_host_workspace_root="/tmp"
        )
        proxy_port = _free_port()
        proxy_server, proxy_task = await _serve(proxy_app, proxy_port)
        try:
            ws = await server_app._connect_remote(
                f"ws://127.0.0.1:{proxy_port}/ws", dict(ALICE)
            )
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                for _ in range(5):
                    await asyncio.wait_for(ws.recv(), timeout=10)
            assert ws.close_code == 4401, f"got {ws.close_code}"
        finally:
            proxy_server.should_exit = True
            await proxy_task
            upstream_server.should_exit = True
            await upstream_task
            os.environ.pop(IDENTITY_SECRET_ENV, None)

    asyncio.run(flow())


def test_upstream_does_not_offer_browser_connect_tickets(monkeypatch):
    """S4: browsers never reach a proxy_assertion upstream, and the ticket
    it would mint rides the query string with a fresh TTL — extending a
    late-captured assertion into a longer-lived credential."""
    from fastapi.testclient import TestClient

    os.environ[IDENTITY_SECRET_ENV] = IDENTITY_SECRET
    try:
        upstream = server_app.build_app(
            spec=_upstream_spec(), default_host_workspace_root="/tmp"
        )
        from omniagents.core.providers.auth.base import UserIdentity
        from omniagents.rpc.identity_assertion import mint_identity_assertion

        assertion = mint_identity_assertion(
            UserIdentity(user_id="alice"), secret=IDENTITY_SECRET
        )
        response = TestClient(upstream).post(
            "/auth/ws-ticket", headers={"Authorization": f"Bearer {assertion}"}
        )
        assert response.status_code == 404, response.text

        # A hop that does authenticate browsers still offers them.
        proxy = server_app.build_app(
            spec=_proxy_spec(9999), default_host_workspace_root="/tmp"
        )
        assert (
            TestClient(proxy).post("/auth/ws-ticket", headers=ALICE).status_code == 200
        )
    finally:
        os.environ.pop(IDENTITY_SECRET_ENV, None)


def test_composite_upstream_serves_direct_and_proxied_clients(monkeypatch):
    """S3: without this, an operator whose upstream also serves direct
    clients cannot adopt proxy_assertion — and the cheapest way out is
    deleting the proxy's own authentication. Make the supported path
    exist instead."""
    _patch_agent_runtime(monkeypatch)

    async def flow():
        os.environ[IDENTITY_SECRET_ENV] = IDENTITY_SECRET
        upstream_spec = AgentSpec(
            name="Upstream",
            welcome_text="upstream",
            get_agent_instructions=lambda: "",
            build_agent=_build_agent,
            security_config={
                "providers": {
                    "auth": {
                        "type": "composite",
                        "accept": [
                            {
                                "type": "proxy_assertion",
                                "signing_secret": f"${{{IDENTITY_SECRET_ENV}}}",
                            },
                            {"type": "token", "tokens": {"carol-token": "carol"}},
                        ],
                    }
                }
            },
        )
        upstream_app = server_app.build_app(
            spec=upstream_spec, default_host_workspace_root="/tmp"
        )
        upstream_port = _free_port()
        upstream_server, upstream_task = await _serve(upstream_app, upstream_port)

        proxy_app = server_app.build_app(
            spec=_proxy_spec(upstream_port), default_host_workspace_root="/tmp"
        )
        proxy_port = _free_port()
        proxy_server, proxy_task = await _serve(proxy_app, proxy_port)
        try:
            upstream_url = f"ws://127.0.0.1:{upstream_port}/ws"
            proxy_url = f"ws://127.0.0.1:{proxy_port}/ws"

            # A direct client authenticates with its own token…
            async with connected(
                upstream_url, {"Authorization": "Bearer carol-token"}
            ) as carol:
                carol_session = (await carol.result("start_run", prompt="carol"))[
                    "session_id"
                ]
            # …and a proxied client with an assertion, on the same server.
            async with connected(proxy_url, ALICE) as alice:
                alice_session = (await alice.result("start_run", prompt="alice"))[
                    "session_id"
                ]

            service = upstream_app.state.agent_service
            assert service.get_session(carol_session).user_id == "carol"
            assert service.get_session(alice_session).user_id == "alice"

            # And they are still separated from each other.
            async with connected(
                upstream_url, {"Authorization": "Bearer carol-token"}
            ) as carol:
                await carol.expect_denied(
                    "get_session_history", session_id=alice_session
                )
        finally:
            proxy_server.should_exit = True
            await proxy_task
            upstream_server.should_exit = True
            await upstream_task
            os.environ.pop(IDENTITY_SECRET_ENV, None)

    asyncio.run(flow())
