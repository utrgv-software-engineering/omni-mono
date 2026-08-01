"""Round-3 review blockers for the serve-proxy identity path (tkt_-U4zoPDzJBUk).

Each test here reproduces a defect the round-2 fixes introduced or left
reachable. They are deliberately *intersection* tests: the round-2 suite
probed each control alone, and every one of these failures lives where
two controls meet (composite × ticket route, composite × capability
aggregation, token provider × the proxy's startup guard).
"""

import asyncio
import json
import os
import socket

import pytest
import uvicorn
from agents import Agent
from fastapi.testclient import TestClient

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity
from omniagents.core.providers.auth.composite import CompositeAuthProvider
from omniagents.core.providers.auth.proxy_assertion import ProxyAssertionAuthProvider
from omniagents.core.providers.auth.token import TokenAuthProvider
from omniagents.rpc.identity_assertion import mint_identity_assertion

SECRET_ENV = "OMNIAGENTS_TEST_R3_IDENTITY_SECRET"
SECRET = "round-three-identity-signing-secret-0123456789"


async def _build_agent(settings, mcp, app):
    return Agent(name="r3", tools=[], instructions="", model="gpt-3.5-turbo")


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


def _spec(**kwargs):
    base = dict(
        name="R3",
        welcome_text="r3",
        get_agent_instructions=lambda: "",
        build_agent=_build_agent,
    )
    base.update(kwargs)
    return AgentSpec(**base)


# ---------------------------------------------------------------------------
# B1 -- composite × the ticket route: the S3 and S4 fixes cancelled out.
# ---------------------------------------------------------------------------


def test_composite_with_proxy_assertion_offers_no_connect_tickets(monkeypatch):
    """The mixed deployment the CHANGELOG recommends must not resurrect
    ``/auth/ws-ticket``.

    ``supports_connect_tickets`` aggregated with ``any()`` let the token
    member's True win, so the route came back — and it authenticates
    through the *composite*, so a 30 s single-use header assertion buys a
    60 s single-use query-string ticket redeemable at ``/ws?ticket=``.
    That is verbatim the harm S4 exists to prevent.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    spec = _spec(
        security_config={
            "providers": {
                "auth": {
                    "type": "composite",
                    "accept": [
                        {"type": "proxy_assertion", "signing_secret_env": SECRET_ENV},
                        {"type": "token", "tokens": {"carol-token": "carol"}},
                    ],
                }
            }
        }
    )
    app = server_app.build_app(spec=spec, default_workspace_root="/tmp")
    client = TestClient(app)

    assertion = mint_identity_assertion(UserIdentity(user_id="alice"), secret=SECRET)
    exchanged = client.post(
        "/auth/ws-ticket", headers={"Authorization": f"Bearer {assertion}"}
    )
    assert exchanged.status_code == 404, exchanged.text
    # And the direct member's own credential buys nothing either — the
    # route is absent, not merely selective.
    assert (
        client.post(
            "/auth/ws-ticket", headers={"Authorization": "Bearer carol-token"}
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# B2 -- capability aggregation must be a conjunction.
# ---------------------------------------------------------------------------


class _BlindProvider(AuthProvider):
    """Authenticates anything as one shared identity."""

    identifies_principals = False

    async def authenticate(self, request):
        return AuthResult(authenticated=True, identity=UserIdentity(user_id="shared"))

    async def get_user_identity(self, request):
        return UserIdentity(user_id="shared")


def test_composite_capabilities_are_conjunctions(monkeypatch):
    """A composite is only as discriminating as its weakest member.

    With ``any()`` a composite holding one blind member reported
    ``identifies_principals=True``: the deployment ran in enforced mode
    while every user arriving on the blind member collapsed onto one
    principal and could read each other's sessions — M2's exact shape,
    now hidden behind the composite's own claim.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    composite = CompositeAuthProvider.__new__(CompositeAuthProvider)
    composite._members = [
        TokenAuthProvider({"tokens": {"a" * 40: "alice"}}),
        _BlindProvider({}),
    ]
    assert composite.identifies_principals is False

    composite._members = [
        ProxyAssertionAuthProvider({"signing_secret_env": SECRET_ENV}),
        TokenAuthProvider({"tokens": {"a" * 40: "alice"}}),
    ]
    assert composite.identifies_principals is True
    # proxy_assertion declines tickets, so the composite must too.
    assert composite.supports_connect_tickets is False
    # Identity assertions are header-only even when legacy query auth remains
    # enabled for a direct token member.
    assert composite.supports_legacy_query_auth is False


def test_composite_capability_guard_survives_a_registered_none_alike(monkeypatch):
    """N3: the `none`/nested-composite blacklist is a type-string check, so
    an alias registered through the supported extension path slips past
    it. The capability conjunction is the control that actually holds."""
    from omniagents.core.providers import register_provider
    from omniagents.core.providers.auth.none import NoneAuthProvider

    class _AliasedNone(NoneAuthProvider):
        pass

    register_provider("auth", "r3_none_alike", _AliasedNone)
    monkeypatch.setenv(SECRET_ENV, SECRET)

    spec = _spec(
        security_config={
            "providers": {
                "auth": {
                    "type": "composite",
                    "accept": [
                        {"type": "proxy_assertion", "signing_secret_env": SECRET_ENV},
                        {"type": "r3_none_alike"},
                    ],
                }
            }
        }
    )
    app = server_app.build_app(spec=spec, default_workspace_root="/tmp")
    # The blacklist admitted it; the capability check must still refuse to
    # treat this deployment as identifying principals.
    assert app.state.connection_authenticator.identifies_principals is False


# ---------------------------------------------------------------------------
# B4 -- a single shared token is ONE principal, whatever it claims.
# ---------------------------------------------------------------------------


def test_single_shared_token_does_not_identify_principals():
    """Every holder of a single ``token:`` authenticates as ``token_user``."""
    assert TokenAuthProvider({"token": "x" * 40}).identifies_principals is False
    # A tokens: map really is per-user.
    assert (
        TokenAuthProvider(
            {"tokens": {"a" * 40: "alice", "b" * 40: "bob"}}
        ).identifies_principals
        is True
    )
    # Mixed: refused outright at construction (see the dedicated test).
    with pytest.raises(ValueError):
        TokenAuthProvider({"token": "x" * 40, "tokens": {"a" * 40: "alice"}})


def test_shared_token_still_enforces_authorization():
    """The two capabilities must not be collapsed into one boolean.

    Fixing B4 by simply reporting ``identifies_principals=False`` for a
    single shared token would ALSO have switched those deployments to a
    permissive ``AuthorizationPolicy`` — silently re-allowing
    process-wide mutators (``disable_tool``, ``write_config``) and, worse,
    turning off ``can_call_resourceless``'s default-DENY for unclassified
    methods. Ownership is vacuous under a shared token, but those two
    controls are not.
    """
    shared = TokenAuthProvider({"token": "x" * 40})
    assert shared.identifies_principals is False
    assert shared.enforces_authorization is True

    from omniagents.core.providers.auth.none import NoneAuthProvider

    anonymous = NoneAuthProvider()
    assert anonymous.identifies_principals is False
    assert anonymous.enforces_authorization is False

    app = server_app.build_app(
        spec=_spec(), auth_token="x" * 40, default_workspace_root="/tmp"
    )
    authenticator = app.state.connection_authenticator
    assert authenticator.identifies_principals is False
    assert authenticator.authorization_policy.enforced is True
    # The control this would have silently removed.
    assert authenticator.authorization_policy.can_call_resourceless("disable_tool") is (
        False
    )
    assert (
        authenticator.authorization_policy.can_call_resourceless("get_agent_info")
        is True
    )


def test_composite_enforcement_aggregates_as_a_disjunction(monkeypatch):
    """A blind member must not relax enforcement for the whole surface."""
    composite = CompositeAuthProvider.__new__(CompositeAuthProvider)
    composite._members = [
        TokenAuthProvider({"tokens": {"a" * 40: "alice"}}),
        _BlindProvider({}),
    ]
    assert composite.identifies_principals is False  # weakest member wins
    assert composite.enforces_authorization is True  # strictest member wins


def test_shared_token_proxy_cannot_claim_per_user_identity(monkeypatch):
    """The whole PR, reachable through the migration path the CHANGELOG names.

    ``omniagents serve --token X`` behind a ``remote_agent:`` config used
    to satisfy the M3 guard, boot with ``preserves_identity=True``, and
    then mint ``sub=token_user`` for every user — one owner upstream, in
    enforced mode, for everybody's sessions.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    spec = _spec(
        remote_ws_url="ws://127.0.0.1:9/ws",
        remote_identity_secret_key=SECRET_ENV,
    )
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(
            spec=spec, auth_token="s" * 40, default_workspace_root="/tmp"
        )
    assert "anonymous" in str(excinfo.value) or "distinct principals" in str(
        excinfo.value
    )


# ---------------------------------------------------------------------------
# B3 -- a flood must cost the flooder, not everyone.
# ---------------------------------------------------------------------------


def test_replay_cache_flood_is_contained_to_the_flooding_principal(monkeypatch):
    """One low-privilege user filling the cache must not disconnect
    everyone else. Measured at 635 conn/s, the global cap fell in 25.8 s
    — inside the assertion TTL — and every other user was refused."""
    monkeypatch.setenv(SECRET_ENV, SECRET)
    provider = ProxyAssertionAuthProvider({"signing_secret_env": SECRET_ENV})
    loop = asyncio.new_event_loop()
    try:

        def auth(user):
            return loop.run_until_complete(
                provider.authenticate(
                    mint_identity_assertion(UserIdentity(user_id=user), secret=SECRET)
                )
            )

        refused = 0
        for _ in range(4000):
            if not auth("mallory").authenticated:
                refused += 1
        assert refused > 0, "the flooding principal was never throttled"

        # Alice is unaffected.
        for _ in range(20):
            assert auth("alice").authenticated, "a flood locked out another user"
    finally:
        loop.close()


def test_capacity_refusal_is_retryable_not_a_terminal_auth_failure(monkeypatch):
    """A 30-second capacity condition reported as terminal 4401 permanently
    disconnects clients that (correctly) never retry on 4401."""
    monkeypatch.setenv(SECRET_ENV, SECRET)
    provider = ProxyAssertionAuthProvider({"signing_secret_env": SECRET_ENV})
    loop = asyncio.new_event_loop()
    try:
        result = None
        for _ in range(4000):
            result = loop.run_until_complete(
                provider.authenticate(
                    mint_identity_assertion(
                        UserIdentity(user_id="mallory"), secret=SECRET
                    )
                )
            )
            if not result.authenticated:
                break
        assert result is not None and not result.authenticated
        assert result.retryable is True, "capacity refusal must not look terminal"

        # A genuinely bad credential is still terminal.
        bad = loop.run_until_complete(provider.authenticate("oaia1.nope.nope"))
        assert bad.authenticated is False
        assert bad.retryable is False
    finally:
        loop.close()


def test_retryable_refusal_closes_the_socket_with_try_again_later(monkeypatch):
    """1013 (try again later), not 4401, reaches the client."""
    import websockets

    monkeypatch.setenv(SECRET_ENV, SECRET)

    async def flow():
        app = server_app.build_app(
            spec=_spec(
                security_config={
                    "providers": {
                        "auth": {
                            "type": "proxy_assertion",
                            "signing_secret_env": SECRET_ENV,
                        }
                    }
                }
            ),
            default_workspace_root="/tmp",
        )
        provider = app.state.connection_authenticator.provider
        # Simulate a full cache without minting 16k assertions.
        monkeypatch.setattr(
            type(provider), "_consume", lambda self, *a, **k: "capacity"
        )
        port = _free_port()
        server, task = await _serve(app, port)
        try:
            assertion = mint_identity_assertion(
                UserIdentity(user_id="alice"), secret=SECRET
            )
            with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
                ws = await server_app._connect_remote(
                    f"ws://127.0.0.1:{port}/ws",
                    {"Authorization": f"Bearer {assertion}"},
                )
                await ws.close()
            # 503, not 403: the proxy in front maps this to a retryable
            # 1013 close rather than a terminal 4401.
            assert (
                excinfo.value.response.status_code == 503
            ), excinfo.value.response.status_code
            assert server_app._upstream_handshake_close_code(excinfo.value) == 1013
        finally:
            server.should_exit = True
            await task

    asyncio.run(flow())


# ---------------------------------------------------------------------------
# N5 -- the signing-secret guard must see `build_app(auth_token=...)`.
# ---------------------------------------------------------------------------


def test_signing_secret_may_not_be_the_serve_token(monkeypatch):
    """`serve --token X` hands X to clients; X must not also sign assertions."""
    shared = "serve-token-that-is-also-the-signing-secret-01"
    monkeypatch.setenv(SECRET_ENV, shared)
    spec = _spec(
        remote_ws_url="ws://127.0.0.1:9/ws",
        remote_identity_secret_key=SECRET_ENV,
        security_config={
            "providers": {
                "auth": {"type": "token", "tokens": {"a" * 40: "a", "b" * 40: "b"}}
            }
        },
    )
    with pytest.raises(ValueError) as excinfo:
        server_app.build_app(
            spec=spec, auth_token=shared, default_workspace_root="/tmp"
        )
    assert "client" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# M4's bug class, one file over: unauthenticated 500 on a token server.
# ---------------------------------------------------------------------------


def test_non_ascii_bearer_does_not_crash_a_token_authenticated_server():
    """``hmac.compare_digest`` raises TypeError on a non-ASCII str, and ASGI
    decodes request headers as latin-1 — so any anonymous client can 500 a
    token-authenticated server. Driven over a raw socket because an HTTP
    client library will not even encode such a header.

    The guard belongs at the single credential chokepoint
    (``ConnectionAuthenticator._authenticate_token``) so it covers every
    provider, not just the one that happened to be audited.
    """

    async def flow():
        app = server_app.build_app(
            spec=_spec(
                security_config={
                    "providers": {
                        "auth": {"type": "token", "tokens": {"a" * 40: "alice"}}
                    }
                }
            ),
            default_workspace_root="/tmp",
        )
        port = _free_port()
        server, task = await _serve(app, port)
        try:
            upgrade = (
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n"
            )
            for target, extra in (
                (b"POST /auth/ws-ticket HTTP/1.1", b"Content-Length: 0\r\n"),
                (b"GET /ws HTTP/1.1", upgrade),
            ):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(
                    target
                    + b"\r\nHost: 127.0.0.1\r\n"
                    + b"Authorization: Bearer \xe9\xe9\xe9\xe9\r\n"
                    + extra
                    + b"\r\n"
                )
                await writer.drain()
                status = await asyncio.wait_for(reader.readline(), timeout=10)
                writer.close()
                assert b"500" not in status, (target, status)
                assert b"401" in status or b"403" in status, (target, status)
        finally:
            server.should_exit = True
            await task

    asyncio.run(flow())


# ---------------------------------------------------------------------------
# N6 -- composite must not silently overrule an explicit refusal.
# ---------------------------------------------------------------------------


def test_composite_logs_when_a_later_member_overrules_a_refusal(caplog):
    class _Revoking(AuthProvider):
        async def authenticate(self, request):
            return AuthResult(authenticated=False, error="revoked")

        async def get_user_identity(self, request):
            return None

    class _Accepting(AuthProvider):
        async def authenticate(self, request):
            return AuthResult(authenticated=True, identity=UserIdentity(user_id="svc"))

        async def get_user_identity(self, request):
            return UserIdentity(user_id="svc")

    composite = CompositeAuthProvider.__new__(CompositeAuthProvider)
    composite._members = [_Revoking({}), _Accepting({})]
    with caplog.at_level("WARNING"):
        result = asyncio.new_event_loop().run_until_complete(
            composite.authenticate("whatever")
        )
    assert result.authenticated is True
    assert any(
        "refus" in record.message.lower() or "overrul" in record.message.lower()
        for record in caplog.records
    ), caplog.text


# ---------------------------------------------------------------------------
# N7 / N8 / skew logging
# ---------------------------------------------------------------------------


def test_minting_refuses_a_non_finite_ttl():
    with pytest.raises(ValueError):
        mint_identity_assertion(
            UserIdentity(user_id="alice"), secret=SECRET, ttl_seconds=float("nan")
        )
    with pytest.raises(ValueError):
        mint_identity_assertion(
            UserIdentity(user_id="alice"), secret=SECRET, now=float("nan")
        )


def test_derived_key_is_pinned_by_a_known_answer():
    """No wire vector exists, so without this a change to the HKDF salt,
    info string or expand step would silently rewrite the wire format
    with the whole suite still green.

    This value was independently cross-checked against
    ``cryptography.hazmat.primitives.kdf.hkdf.HKDF`` (RFC 5869,
    SHA-256, L=32) with the same salt and info, and is byte-identical —
    so it pins a real KDF output, not merely "whatever the code does".
    """
    from omniagents.rpc.identity_assertion import _derive_key

    assert (
        _derive_key("known-secret-for-the-known-answer-test-01").hex()
        == "98b44e0fb9902173e21067c3745933011953764a8e98907542afdbad29fedb5b"
    )


def test_derived_key_matches_rfc5869_reference_implementation():
    """The KAT above is only as good as the construction it pins."""
    cryptography = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from omniagents.rpc.identity_assertion import (
        _HKDF_INFO,
        _HKDF_SALT,
        _derive_key,
    )

    for secret in ("a" * 32, SECRET, "another-perfectly-fine-signing-secret-9f2c"):
        reference = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=_HKDF_INFO
        ).derive(secret.encode("utf-8"))
        assert _derive_key(secret) == reference


def test_skew_rejection_logs_the_measured_offset(caplog):
    """A step clock event (VM suspend, WSL2 host sleep) produces a
    terminal, non-self-healing outage; the operator needs the number."""
    from omniagents.rpc.identity_assertion import (
        InvalidIdentityAssertion,
        verify_identity_assertion,
    )
    import time as _time

    token = mint_identity_assertion(UserIdentity(user_id="alice"), secret=SECRET)
    with caplog.at_level("WARNING"):
        with pytest.raises(InvalidIdentityAssertion):
            verify_identity_assertion(token, secret=SECRET, now=_time.time() + 3600.0)
    assert any("skew" in r.message.lower() for r in caplog.records), caplog.text


# ---------------------------------------------------------------------------
# Round-4 review: defects introduced by the round-3 fixes.
# ---------------------------------------------------------------------------


def test_gateway_subclass_following_the_migration_note_enforces_authorization():
    """F1: the documented migration must not silently disable enforcement.

    The CHANGELOG tells operators of a gateway-header provider that
    subclasses ``NoneAuthProvider`` to set ``identifies_principals =
    True``. Splitting the capability made ``enforces_authorization`` a
    second, *inheritable* class attribute, so following that instruction
    verbatim produced a deployment that identifies principals, passes the
    proxy's gate and propagates per-user identity upstream — while the
    local server ran with no ownership checks at all. Before the split,
    one flag did both jobs and this shape was enforced.
    """
    from omniagents.core.providers.auth.none import NoneAuthProvider
    from omniagents.rpc.auth import ConnectionAuthenticator

    class GatewayHeaderProvider(NoneAuthProvider):
        # Exactly, and only, what the migration note says to add.
        identifies_principals = True

    provider = GatewayHeaderProvider()
    assert provider.identifies_principals is True
    assert provider.enforces_authorization is True

    policy = ConnectionAuthenticator(provider).authorization_policy
    assert policy.enforced is True
    assert policy.can_access(UserIdentity(user_id="alice"), "bob") is False
    assert policy.can_call_resourceless("disable_tool") is False
    assert policy.can_call_resourceless("a_brand_new_unclassified_method") is False

    # The untouched base provider is still the permissive single-user mode.
    base = NoneAuthProvider()
    assert base.identifies_principals is False
    assert base.enforces_authorization is False
    assert ConnectionAuthenticator(base).authorization_policy.enforced is False


def test_composite_preserves_a_retryable_refusal(monkeypatch):
    """F2: composite rebuilt the terminal AuthResult and dropped ``retryable``.

    ``composite[proxy_assertion, token]`` is the deployment shape the
    migration note exists to serve, so a capacity condition there was
    reported as a terminal 4401 — permanently disconnecting every
    well-behaved client and undoing B3 exactly where it matters most.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    member = ProxyAssertionAuthProvider({"signing_secret_env": SECRET_ENV})
    monkeypatch.setattr(type(member), "_consume", lambda self, *a, **k: "capacity")
    composite = CompositeAuthProvider.__new__(CompositeAuthProvider)
    composite._members = [member, TokenAuthProvider({"tokens": {"a" * 40: "alice"}})]

    assertion = mint_identity_assertion(UserIdentity(user_id="alice"), secret=SECRET)
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(composite.authenticate(assertion))
        assert result.authenticated is False
        assert result.retryable is True, "composite flattened a capacity refusal"

        # A genuinely bad credential through the composite stays terminal.
        bad = loop.run_until_complete(composite.authenticate("oaia1.nope.nope"))
        assert bad.authenticated is False
        assert bad.retryable is False
    finally:
        loop.close()


def test_composite_capacity_refusal_reaches_the_client_as_retryable(monkeypatch):
    """F2, end to end: 503 on the upgrade (→ 1013 through a proxy), not 403."""
    import websockets

    monkeypatch.setenv(SECRET_ENV, SECRET)

    async def flow():
        app = server_app.build_app(
            spec=_spec(
                security_config={
                    "providers": {
                        "auth": {
                            "type": "composite",
                            "accept": [
                                {
                                    "type": "proxy_assertion",
                                    "signing_secret_env": SECRET_ENV,
                                },
                                {
                                    "type": "token",
                                    "tokens": {"carol-token": "carol"},
                                },
                            ],
                        }
                    }
                }
            ),
            default_workspace_root="/tmp",
        )
        composite = app.state.connection_authenticator.provider
        assertion_member = composite._members[0]
        monkeypatch.setattr(
            type(assertion_member), "_consume", lambda self, *a, **k: "capacity"
        )
        port = _free_port()
        server, task = await _serve(app, port)
        try:
            assertion = mint_identity_assertion(
                UserIdentity(user_id="alice"), secret=SECRET
            )
            with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
                ws = await server_app._connect_remote(
                    f"ws://127.0.0.1:{port}/ws",
                    {"Authorization": f"Bearer {assertion}"},
                )
                await ws.close()
            assert (
                excinfo.value.response.status_code == 503
            ), excinfo.value.response.status_code
            assert server_app._upstream_handshake_close_code(excinfo.value) == 1013
        finally:
            server.should_exit = True
            await task

    asyncio.run(flow())


def test_token_provider_refuses_a_mixed_token_and_tokens_config():
    """F3: ``token:`` + ``tokens:`` failed OPEN on the proxy branch.

    The mixed config reported ``identifies_principals=False``, which made
    the proxy pick ``SharedUpstreamToken`` — so alice and bob reached the
    upstream as the same principal, while the *safe* configuration (an
    identity secret) was the one refused. There is no sensible meaning for
    the combination, so refuse it where it is written.
    """
    with pytest.raises(ValueError) as excinfo:
        TokenAuthProvider({"token": "x" * 40, "tokens": {"a" * 40: "alice"}})
    message = str(excinfo.value).lower()
    assert "token" in message and "tokens" in message

    # Each alone still constructs.
    assert TokenAuthProvider({"token": "x" * 40}).identifies_principals is False
    assert (
        TokenAuthProvider({"tokens": {"a" * 40: "alice"}}).identifies_principals is True
    )


def test_full_cache_does_not_rescan_on_every_attempt(monkeypatch):
    """F4: the emergency-prune disjunct bypassed the 1 s amortisation.

    Once globally full, every attempt — including refused ones — paid a
    full 16384-entry scan (measured at 335 µs, the very cost round 3
    claimed to remove) and the cache could not drain because the entries
    were unexpired.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    provider = ProxyAssertionAuthProvider({"signing_secret_env": SECRET_ENV})

    # Fill the global cache with unexpired entries directly.
    from omniagents.core.providers.auth import proxy_assertion as pa

    far_future = __import__("time").time() + 10_000
    provider._seen = {
        f"j{i}": (far_future, f"u{i}") for i in range(pa._MAX_REPLAY_ENTRIES)
    }
    provider._per_principal = {f"u{i}": 1 for i in range(pa._MAX_REPLAY_ENTRIES)}
    provider._next_prune = far_future  # amortisation window is wide open

    calls = {"n": 0}
    original = pa.ProxyAssertionAuthProvider._prune

    def counting_prune(self, now):
        calls["n"] += 1
        return original(self, now)

    monkeypatch.setattr(pa.ProxyAssertionAuthProvider, "_prune", counting_prune)

    for i in range(50):
        assert provider._consume(f"new{i}", far_future, "mallory") != pa.CONSUME_OK
    assert (
        calls["n"] == 0
    ), f"a full cache still forced {calls['n']} full scans in 50 attempts"
