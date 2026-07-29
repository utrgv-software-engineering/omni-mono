"""Unit tests for the unified connection-auth building blocks.

Covers ticket minting/redemption, credential-transport precedence, the
legacy query-token gate, multi-token principal resolution, the shared
ownership rule, and terminal owner binding.
"""

import pytest

from omniagents.core.providers.auth.base import UserIdentity
from omniagents.core.providers.auth.none import NoneAuthProvider
from omniagents.core.providers.auth.token import TokenAuthProvider
from omniagents.rpc.auth import (
    GLOBAL_MUTATING_METHODS,
    GLOBAL_SAFE_METHODS,
    OWNER_UNKNOWN,
    RESOURCE_SCOPED_METHODS,
    AuthenticationFailed,
    AuthorizationDenied,
    AuthorizationPolicy,
    ConnectionAuthenticator,
    WsTicketBroker,
    legacy_query_auth_enabled,
    principal_can_access,
)


class FakeWebSocket:
    """Headers + query params double for authenticate_ws."""

    class _Params:
        def __init__(self, values):
            self._values = values

        def get(self, key, default=None):
            return self._values.get(key, default)

        def multi_items(self):
            return list(self._values.items())

    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = self._Params(query or {})


# ---------------------------------------------------------------------------
# WsTicketBroker
# ---------------------------------------------------------------------------


def test_ticket_is_single_use_and_identity_bound():
    broker = WsTicketBroker()
    alice = UserIdentity(user_id="alice")
    ticket, expires_in = broker.mint(alice)
    assert expires_in > 0
    redeemed = broker.redeem(ticket)
    assert redeemed is alice
    assert broker.redeem(ticket) is None  # second redemption fails


def test_expired_ticket_rejected(monkeypatch):
    broker = WsTicketBroker(ttl_seconds=0.0)
    ticket, _ = broker.mint(UserIdentity(user_id="alice"))
    import time

    time.sleep(0.01)
    assert broker.redeem(ticket) is None


def test_forged_ticket_rejected():
    broker = WsTicketBroker()
    assert broker.redeem("forged") is None


# ---------------------------------------------------------------------------
# ConnectionAuthenticator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_header_bearer_token_resolves_principal():
    auth = ConnectionAuthenticator(TokenAuthProvider({"token": "sekrit"}))
    identity = await auth.authenticate_ws(
        FakeWebSocket(headers={"authorization": "Bearer sekrit"})
    )
    assert identity.user_id == "token_user"


@pytest.mark.asyncio
async def test_missing_credentials_rejected_when_token_configured():
    auth = ConnectionAuthenticator(TokenAuthProvider({"token": "sekrit"}))
    with pytest.raises(AuthenticationFailed):
        await auth.authenticate_ws(FakeWebSocket())


@pytest.mark.asyncio
async def test_query_token_rejected_by_default_even_if_valid():
    """The gate closes the provider-side query-param extraction too."""
    auth = ConnectionAuthenticator(TokenAuthProvider({"token": "sekrit"}))
    with pytest.raises(AuthenticationFailed):
        await auth.authenticate_ws(FakeWebSocket(query={"token": "sekrit"}))


@pytest.mark.asyncio
async def test_query_token_accepted_when_legacy_enabled():
    auth = ConnectionAuthenticator(
        TokenAuthProvider({"token": "sekrit"}), allow_query_token=True
    )
    identity = await auth.authenticate_ws(FakeWebSocket(query={"token": "sekrit"}))
    assert identity.user_id == "token_user"


@pytest.mark.asyncio
async def test_ticket_query_param_resolves_minting_identity():
    auth = ConnectionAuthenticator(TokenAuthProvider({"token": "sekrit"}))
    ticket, _ = auth.ticket_broker.mint(UserIdentity(user_id="alice"))
    identity = await auth.authenticate_ws(FakeWebSocket(query={"ticket": ticket}))
    assert identity.user_id == "alice"


@pytest.mark.asyncio
async def test_anonymous_mode_authenticates_without_credentials():
    auth = ConnectionAuthenticator(NoneAuthProvider())
    identity = await auth.authenticate_ws(FakeWebSocket())
    assert identity.user_id == "anonymous"


def test_from_spec_prefers_configured_provider_and_reads_legacy_flag(monkeypatch):
    class Spec:
        security_config = {
            "legacy_query_auth": True,
            "providers": {"auth": {"type": "token", "token": "abc"}},
        }

    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    auth = ConnectionAuthenticator.from_spec(Spec(), auth_token=None)
    assert isinstance(auth.provider, TokenAuthProvider)
    assert auth.allow_query_token is True

    monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "0")
    auth = ConnectionAuthenticator.from_spec(Spec(), auth_token=None)
    assert auth.allow_query_token is False  # env wins over config


def test_legacy_query_auth_env_parsing(monkeypatch):
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    assert legacy_query_auth_enabled(None) is False
    assert legacy_query_auth_enabled({"legacy_query_auth": True}) is True
    monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "1")
    assert legacy_query_auth_enabled(None) is True
    monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "0")
    assert legacy_query_auth_enabled({"legacy_query_auth": True}) is False


# ---------------------------------------------------------------------------
# TokenAuthProvider multi-token principals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_token_map_resolves_distinct_users():
    provider = TokenAuthProvider(
        {
            "tokens": {
                "alice-token": "alice",
                "bob-token": {"user_id": "bob", "roles": ["admin"]},
            }
        }
    )
    alice = await provider.authenticate("alice-token")
    assert alice.authenticated and alice.identity.user_id == "alice"
    bob = await provider.authenticate("bob-token")
    assert bob.authenticated and bob.identity.user_id == "bob"
    assert bob.identity.roles == ["admin"]
    stranger = await provider.authenticate("mallory-token")
    assert not stranger.authenticated


# ---------------------------------------------------------------------------
# principal_can_access — the single ownership rule
# ---------------------------------------------------------------------------


def test_ownership_rule_fails_closed():
    alice = UserIdentity(user_id="alice")
    bob = UserIdentity(user_id="bob")
    admin = UserIdentity(user_id="root", roles=["admin"])
    assert principal_can_access(alice, "alice")
    assert not principal_can_access(bob, "alice")
    assert not principal_can_access(None, "alice")  # anonymous vs owned
    assert principal_can_access(admin, "alice")  # admin bypass
    # An absent owner is never consent.
    assert not principal_can_access(alice, None)
    assert not principal_can_access(admin, None)
    # An owner that could not be resolved denies for everyone.
    assert not principal_can_access(alice, OWNER_UNKNOWN)
    assert not principal_can_access(admin, OWNER_UNKNOWN)


def test_policy_short_circuits_in_single_user_mode():
    """No-auth deployments keep the historical permissive behavior."""
    permissive = AuthorizationPolicy(enforced=False)
    enforced = AuthorizationPolicy(enforced=True)
    alice = UserIdentity(user_id="alice")

    assert permissive.can_access(None, None)
    assert permissive.can_access(alice, "bob")
    assert permissive.can_access(alice, OWNER_UNKNOWN)
    assert permissive.can_call_resourceless("disable_tool")

    assert not enforced.can_access(alice, "bob")
    assert not enforced.can_access(alice, None)
    assert not enforced.can_access(alice, OWNER_UNKNOWN)


def test_resourceless_methods_are_default_deny():
    enforced = AuthorizationPolicy(enforced=True)
    # Classified read-only / self-scoped / per-connection methods pass.
    for method in ("initialize", "get_agent_info", "list_sessions", "start_run"):
        assert enforced.can_call_resourceless(method)
    # Process-wide mutators are refused...
    for method in GLOBAL_MUTATING_METHODS:
        assert not enforced.can_call_resourceless(method)
    # ...and so is anything unclassified (e.g. a newly added method).
    assert not enforced.can_call_resourceless("brand_new_global_method")


def test_every_protocol_request_method_is_classified():
    """A new GUI request must be explicitly classified as resource-scoped
    or global — an unclassified method would otherwise be silently denied
    (or, worse, silently allowed if the default ever flipped)."""
    from omniagents.rpc.protocol_catalog import OPERATIONS

    request_methods = {
        op.name
        for op in OPERATIONS
        if op.kind == "request" and op.direction == "client_to_server"
    }
    classified = GLOBAL_SAFE_METHODS | GLOBAL_MUTATING_METHODS | RESOURCE_SCOPED_METHODS
    unclassified = sorted(request_methods - classified)
    assert not unclassified, (
        "these GUI request methods have no authorization classification: "
        f"{unclassified} — add them to GLOBAL_SAFE_METHODS, "
        "GLOBAL_MUTATING_METHODS, or RESOURCE_SCOPED_METHODS"
    )


def test_authorization_denied_error_data():
    exc = AuthorizationDenied("nope", resource="session", resource_id="s1")
    assert exc.error_data() == {
        "kind": "authorization_denied",
        "resource": "session",
        "resource_id": "s1",
    }


# ---------------------------------------------------------------------------
# TerminalManager owner binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_attach_requires_owner_identity():
    from omniagents.rpc.terminal import TerminalAuthorizationError, TerminalManager
    from tests.unit.rpc.test_terminal_manager import FakePtyBackend

    manager = TerminalManager(
        backend=FakePtyBackend(),
        authorization_policy=AuthorizationPolicy(enforced=True),
    )
    created = await manager.create_terminal("sess-1", owner_user_id="alice")
    terminal_id = created["terminal_id"]
    token = created["token"]

    # A stolen attach token is useless without the owner's credentials.
    with pytest.raises(TerminalAuthorizationError):
        await manager.authorize(
            "sess-1", terminal_id, token, principal=UserIdentity(user_id="bob")
        )
    # Anonymous connections cannot attach to an owned terminal either.
    with pytest.raises(TerminalAuthorizationError):
        await manager.authorize("sess-1", terminal_id, token, principal=None)

    descriptor = await manager.authorize(
        "sess-1", terminal_id, token, principal=UserIdentity(user_id="alice")
    )
    assert descriptor.owner_user_id == "alice"
    await manager.close_terminal("sess-1", terminal_id)


@pytest.mark.asyncio
async def test_unowned_terminal_keeps_single_user_behavior():
    """No-auth deployments create unowned terminals and can attach them."""
    from omniagents.rpc.terminal import TerminalManager
    from tests.unit.rpc.test_terminal_manager import FakePtyBackend

    manager = TerminalManager(backend=FakePtyBackend())
    created = await manager.create_terminal("sess-1")
    descriptor = await manager.authorize(
        "sess-1", created["terminal_id"], created["token"]
    )
    assert descriptor.owner_user_id is None
    await manager.close_terminal("sess-1", created["terminal_id"])


@pytest.mark.asyncio
async def test_unowned_terminal_not_attachable_under_enforced_auth():
    """Reviewer probe: an unowned terminal was attachable by ANY
    authenticated user holding the attach token. Under enforced
    authorization an absent owner must deny."""
    from omniagents.rpc.terminal import TerminalAuthorizationError, TerminalManager
    from tests.unit.rpc.test_terminal_manager import FakePtyBackend

    manager = TerminalManager(
        backend=FakePtyBackend(),
        authorization_policy=AuthorizationPolicy(enforced=True),
    )
    created = await manager.create_terminal("unowned-sess", owner_user_id=None)
    with pytest.raises(TerminalAuthorizationError):
        await manager.authorize(
            "unowned-sess",
            created["terminal_id"],
            created["token"],
            principal=UserIdentity(user_id="mallory"),
        )
    await manager.close_terminal("unowned-sess", created["terminal_id"])
