"""Unit tests for the signed identity assertions carried across the proxy hop."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from omniagents.core.providers.auth.base import UserIdentity
from omniagents.rpc.identity_assertion import (
    ASSERTION_VERSION,
    ASSERTION_CLOCK_SKEW_SECONDS,
    DEFAULT_ASSERTION_AUDIENCE,
    MAX_ASSERTION_TTL_SECONDS,
    MAX_ASSERTED_ROLES,
    MAX_ASSERTED_ROLE_LENGTH,
    InvalidIdentityAssertion,
    _b64u_decode,
    _b64u_encode,
    mint_identity_assertion,
    normalize_role_allowlist,
    validate_signing_secret,
    verify_identity_assertion,
)

SECRET = "a-signing-secret-that-is-long-enough-0123456789"
OTHER_SECRET = "a-different-signing-secret-just-as-long-9876543"


def _identity() -> UserIdentity:
    return UserIdentity(
        user_id="alice",
        email="alice@example.com",
        display_name="Alice",
        roles=["admin"],
        claims={"tid": "contoso"},
    )


def test_round_trip_preserves_the_whole_principal():
    verified = verify_identity_assertion(
        mint_identity_assertion(_identity(), secret=SECRET, allowed_roles=["admin"]),
        secret=SECRET,
        allowed_roles=["admin"],
    )
    identity = verified.identity
    assert identity.user_id == "alice"
    assert identity.email == "alice@example.com"
    assert identity.display_name == "Alice"
    assert identity.roles == ["admin"]
    assert identity.claims == {"tid": "contoso"}
    assert verified.jti


def test_roles_are_fail_closed_and_require_symmetric_allowlists(caplog, monkeypatch):
    monkeypatch.setattr("omniagents.rpc.identity_assertion._next_dropped_role_log", 0.0)
    token = mint_identity_assertion(_identity(), secret=SECRET)
    mint_identity_assertion(_identity(), secret=SECRET)
    verified = verify_identity_assertion(token, secret=SECRET)
    assert verified.identity.roles == []
    assert caplog.text.count("Dropped roles") == 1

    token = mint_identity_assertion(_identity(), secret=SECRET, allowed_roles=["admin"])
    with pytest.raises(InvalidIdentityAssertion, match="unauthorized role"):
        verify_identity_assertion(token, secret=SECRET)

    verified = verify_identity_assertion(token, secret=SECRET, allowed_roles=["admin"])
    assert verified.identity.roles == ["admin"]


@pytest.mark.asyncio
async def test_proxy_assertion_provider_enforces_its_role_allowlist():
    from omniagents.core.providers.auth.proxy_assertion import (
        ProxyAssertionAuthProvider,
    )

    token = mint_identity_assertion(_identity(), secret=SECRET, allowed_roles=["admin"])
    default_provider = ProxyAssertionAuthProvider({"signing_secret": SECRET})
    rejected = await default_provider.authenticate(token)
    assert rejected.authenticated is False
    assert "unauthorized role" in rejected.error

    permitted_provider = ProxyAssertionAuthProvider(
        {"signing_secret": SECRET, "allowed_roles": ["admin", "admin"]}
    )
    accepted = await permitted_provider.authenticate(token)
    assert accepted.authenticated is True
    assert accepted.identity.roles == ["admin"]


def test_role_allowlists_are_exact_case_sensitive_and_deduplicated():
    assert normalize_role_allowlist(["admin", "Admin", "admin"]) == [
        "admin",
        "Admin",
    ]
    identity = UserIdentity(user_id="alice", roles=["admin", "Admin", "admin"])
    token = mint_identity_assertion(
        identity, secret=SECRET, allowed_roles=["admin", "admin"]
    )
    verified = verify_identity_assertion(token, secret=SECRET, allowed_roles=["admin"])
    assert verified.identity.roles == ["admin"]


@pytest.mark.parametrize(
    "roles",
    [
        "admin",
        [""],
        [1],
        ["x" * (MAX_ASSERTED_ROLE_LENGTH + 1)],
        [f"role-{index}" for index in range(MAX_ASSERTED_ROLES + 1)],
    ],
)
def test_invalid_role_allowlists_are_rejected(roles):
    with pytest.raises(ValueError):
        normalize_role_allowlist(roles)


def test_verifier_rejects_malformed_or_oversized_asserted_roles():
    for roles in (
        "admin",
        [1],
        ["x" * (MAX_ASSERTED_ROLE_LENGTH + 1)],
        [f"role-{index}" for index in range(MAX_ASSERTED_ROLES + 1)],
    ):
        token = _sign_payload(_payload(roles=roles))
        with pytest.raises(InvalidIdentityAssertion):
            verify_identity_assertion(
                token,
                secret=SECRET,
                allowed_roles=[f"role-{index}" for index in range(MAX_ASSERTED_ROLES)],
            )


def test_each_assertion_has_a_distinct_replay_id():
    a = verify_identity_assertion(
        mint_identity_assertion(_identity(), secret=SECRET), secret=SECRET
    )
    b = verify_identity_assertion(
        mint_identity_assertion(_identity(), secret=SECRET), secret=SECRET
    )
    assert a.jti != b.jti


def test_wrong_secret_is_refused():
    token = mint_identity_assertion(_identity(), secret=SECRET)
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(token, secret=OTHER_SECRET)


def test_audience_mismatch_is_refused():
    token = mint_identity_assertion(_identity(), secret=SECRET, audience="upstream-a")
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(token, secret=SECRET, audience="upstream-b")
    assert (
        verify_identity_assertion(
            token, secret=SECRET, audience="upstream-a"
        ).identity.user_id
        == "alice"
    )


def test_tampered_payload_is_refused():
    """Rewriting the subject without the secret cannot pass verification."""
    token = mint_identity_assertion(_identity(), secret=SECRET)
    version, body, signature = token.split(".")
    payload = json.loads(_b64u_decode(body))
    payload["sub"] = "bob"
    forged = f"{version}.{_b64u_encode(json.dumps(payload).encode())}.{signature}"
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(forged, secret=SECRET)


def test_expired_assertion_is_refused():
    token = mint_identity_assertion(
        _identity(), secret=SECRET, ttl_seconds=1.0, now=time.time() - 600
    )
    with pytest.raises(InvalidIdentityAssertion) as excinfo:
        verify_identity_assertion(token, secret=SECRET)
    assert "expired" in str(excinfo.value)


def test_assertion_from_the_future_is_refused():
    token = mint_identity_assertion(
        _identity(), secret=SECRET, now=time.time() + 3600.0
    )
    with pytest.raises(InvalidIdentityAssertion) as excinfo:
        verify_identity_assertion(token, secret=SECRET)
    assert "not yet valid" in str(excinfo.value)


def test_minting_a_long_lived_assertion_is_refused():
    with pytest.raises(ValueError):
        mint_identity_assertion(
            _identity(), secret=SECRET, ttl_seconds=MAX_ASSERTION_TTL_SECONDS + 1
        )


def test_verification_caps_the_lifetime_of_a_hostile_minter():
    """Even a correctly signed assertion cannot outlive the cap."""
    issued = time.time()
    payload = {
        "v": ASSERTION_VERSION,
        "iss": "omniagents-proxy",
        "aud": DEFAULT_ASSERTION_AUDIENCE,
        "iat": issued,
        "exp": issued + MAX_ASSERTION_TTL_SECONDS * 100,
        "jti": "j",
        "sub": "alice",
        "email": None,
        "name": None,
        "roles": [],
        "claims": {},
    }
    # Signed with the real derived key, so only the cap can refuse it.
    with pytest.raises(InvalidIdentityAssertion) as excinfo:
        verify_identity_assertion(_sign_payload(payload), secret=SECRET)
    assert "lifetime exceeds the cap" in str(excinfo.value)


@pytest.mark.parametrize(
    "token", ["", "not-a-token", "a.b", "oaia1.body", "oaia2.body.sig", "x." * 40]
)
def test_malformed_tokens_are_refused(token):
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(token, secret=SECRET)


def test_short_or_missing_signing_secrets_are_refused():
    with pytest.raises(ValueError):
        validate_signing_secret("")
    with pytest.raises(ValueError):
        validate_signing_secret(None)
    with pytest.raises(ValueError):
        validate_signing_secret("too-short")
    assert validate_signing_secret(SECRET) == SECRET


# ---------------------------------------------------------------------------
# Hardening from the PR #317 review.
# ---------------------------------------------------------------------------


def _sign_payload(payload, secret=SECRET):
    """Mint a correctly-signed assertion around an arbitrary payload."""
    from omniagents.rpc.identity_assertion import _sign

    body = _b64u_encode(json.dumps(payload).encode())
    signing_input = f"{ASSERTION_VERSION}.{body}"
    return f"{signing_input}.{_b64u_encode(_sign(secret, signing_input))}"


def _payload(**overrides):
    issued = time.time()
    payload = {
        "v": ASSERTION_VERSION,
        "iss": "omniagents-proxy",
        "aud": DEFAULT_ASSERTION_AUDIENCE,
        "iat": issued,
        "exp": issued + 30.0,
        "jti": "replay-id",
        "sub": "alice",
        "email": None,
        "name": None,
        "roles": [],
        "claims": {},
    }
    payload.update(overrides)
    return payload


# M1 -- non-finite lifetimes defeat every temporal check at once.
@pytest.mark.parametrize(
    "iat,exp",
    [
        (float("nan"), float("nan")),
        (0.0, float("nan")),
        (float("nan"), 0.0),
        (float("-inf"), float("inf")),
        (0.0, float("inf")),
        ("nan", "nan"),
        ("inf", "inf"),
        ("NaN", "Infinity"),
    ],
    ids=[
        "both-nan",
        "exp-nan",
        "iat-nan",
        "infinite-span",
        "exp-inf",
        "string-nan",
        "string-inf",
        "mixed-strings",
    ],
)
def test_non_finite_lifetimes_are_refused(iat, exp):
    token = _sign_payload(_payload(iat=iat, exp=exp))
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(token, secret=SECRET)
    # …and still refused ten years later, which is the actual risk: a
    # NaN assertion never expires.
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(
            token, secret=SECRET, now=time.time() + 10 * 365 * 86400
        )


def test_non_numeric_lifetimes_are_refused():
    for iat, exp in ((None, None), ([], {}), (True, False), ("12", "34")):
        token = _sign_payload(_payload(iat=iat, exp=exp))
        with pytest.raises(InvalidIdentityAssertion):
            verify_identity_assertion(token, secret=SECRET)


# M4 -- non-ASCII credentials are remotely reachable pre-auth.
@pytest.mark.parametrize(
    "token",
    ["oaia1.café.sig", "café", "oaia1.body.café", "ø" * 64, "oaia1.\x80\xff.sig"],
)
def test_non_ascii_tokens_are_refused_not_crashed(token):
    """ASGI headers are latin-1 decoded, so these arrive pre-auth. They
    must raise InvalidIdentityAssertion — the exception callers catch —
    never TypeError or UnicodeEncodeError."""
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(token, secret=SECRET)


def test_non_ascii_assertion_is_refused_by_the_provider():
    from omniagents.core.providers.auth.proxy_assertion import (
        ProxyAssertionAuthProvider,
    )

    provider = ProxyAssertionAuthProvider({"signing_secret": SECRET})

    class _Req:
        headers = {"authorization": "Bearer café-\x80"}

    result = (
        asyncio.get_event_loop_policy()
        .new_event_loop()
        .run_until_complete(provider.authenticate(_Req()))
    )
    assert result.authenticated is False


# M5 -- the signing key must not be usable as, or derivable from, a
# client-distributed bearer token in another context.
def test_signing_is_domain_separated_from_the_raw_secret():
    """A MAC computed with the raw secret over the public signing input
    must not verify: the key is derived for this purpose only, so holding
    the secret in another role does not let you forge assertions with a
    naive HMAC."""
    import hmac as _hmac
    from hashlib import sha256 as _sha256

    payload = _payload()
    body = _b64u_encode(json.dumps(payload).encode())
    signing_input = f"{ASSERTION_VERSION}.{body}"
    naive = _b64u_encode(
        _hmac.new(SECRET.encode(), signing_input.encode("ascii"), _sha256).digest()
    )
    with pytest.raises(InvalidIdentityAssertion):
        verify_identity_assertion(f"{signing_input}.{naive}", secret=SECRET)


# M6 -- length is not validation.
@pytest.mark.parametrize(
    "secret",
    [
        "OMNI_PROXY_PER_USER_IDENTITY_SECRET",
        "OMNIAGENTS_TEST_PROXY_IDENTITY_SECRET",
        "MY_VERY_LONG_ENVIRONMENT_VARIABLE_NAME",
        " " * 40,
        "\x00" * 40,
        "a" * 40,
        "ababababababababababababababababababab",
        "{'token': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}",
        "['aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa']",
        "  " + SECRET + "  ",
    ],
    ids=[
        "env-name",
        "env-name-2",
        "env-name-3",
        "spaces",
        "nuls",
        "one-char",
        "two-chars",
        "stringified-dict",
        "stringified-list",
        "untrimmed",
    ],
)
def test_degenerate_signing_secrets_are_refused(secret):
    with pytest.raises(ValueError):
        validate_signing_secret(secret)


def test_a_real_random_secret_is_accepted():
    import secrets as _secrets

    for _ in range(20):
        candidate = _secrets.token_urlsafe(32)
        assert validate_signing_secret(candidate) == candidate


# S2 -- the replay cache must not be flushable.
def test_replay_cache_cannot_be_flushed_to_enable_a_replay():
    """Filling the cache with fresh assertions must not evict a captured
    one. Fail closed: refuse new assertions rather than forget old ones."""
    from omniagents.core.providers.auth.base import UserIdentity
    from omniagents.core.providers.auth.proxy_assertion import (
        _MAX_REPLAY_ENTRIES,
        ProxyAssertionAuthProvider,
    )

    provider = ProxyAssertionAuthProvider({"signing_secret": SECRET})
    loop = asyncio.new_event_loop()
    try:
        captured = mint_identity_assertion(UserIdentity(user_id="alice"), secret=SECRET)
        assert loop.run_until_complete(provider.authenticate(captured)).authenticated

        # An attacker floods the process with their own valid assertions.
        for _ in range(_MAX_REPLAY_ENTRIES + 64):
            loop.run_until_complete(
                provider.authenticate(
                    mint_identity_assertion(UserIdentity(user_id="bob"), secret=SECRET)
                )
            )

        # The captured assertion is still burnt.
        replayed = loop.run_until_complete(provider.authenticate(captured))
        assert replayed.authenticated is False
    finally:
        loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary_offset",
    [-0.001, 0.0],
    ids=["just-before-boundary", "exact-boundary"],
)
async def test_replay_id_is_retained_through_expiration_plus_clock_skew(
    monkeypatch, boundary_offset
):
    """An assertion still accepted by the verifier must remain single-use."""
    from omniagents.core.providers.auth import proxy_assertion as provider_module
    from omniagents.rpc import identity_assertion as assertion_module

    expires_at = 1_000.0
    clock = [expires_at - 1.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(provider_module, "time", fake_time)
    monkeypatch.setattr(assertion_module, "time", fake_time)

    token = _sign_payload(
        _payload(iat=expires_at - 30.0, exp=expires_at, jti="boundary-jti")
    )
    provider = provider_module.ProxyAssertionAuthProvider({"signing_secret": SECRET})
    assert (await provider.authenticate(token)).authenticated is True

    clock[0] = expires_at + ASSERTION_CLOCK_SKEW_SECONDS + boundary_offset
    if boundary_offset == 0.0:
        assert clock[0] == expires_at + ASSERTION_CLOCK_SKEW_SECONDS
    # Force the amortized sweep with another valid assertion. Before the fix,
    # that sweep forgot boundary-jti at bare exp even though the verifier
    # still accepted its token during the skew allowance.
    sweep_trigger = _sign_payload(
        _payload(
            iat=clock[0] - 1.0,
            exp=clock[0] + 30.0,
            jti="sweep-trigger-jti",
        )
    )
    assert (await provider.authenticate(sweep_trigger)).authenticated is True

    replay = await provider.authenticate(token)
    assert replay.authenticated is False
    assert replay.error == "Identity assertion was already used"
    assert provider._seen["boundary-jti"][0] == (
        expires_at + ASSERTION_CLOCK_SKEW_SECONDS
    )


@pytest.mark.asyncio
async def test_replay_id_expires_only_after_verifier_acceptance_window(monkeypatch):
    """After exp + skew the token is invalid and its replay id is reclaimable."""
    from omniagents.core.providers.auth import proxy_assertion as provider_module
    from omniagents.rpc import identity_assertion as assertion_module

    expires_at = 2_000.0
    clock = [expires_at - 1.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(provider_module, "time", fake_time)
    monkeypatch.setattr(assertion_module, "time", fake_time)

    consumed = _sign_payload(
        _payload(iat=expires_at - 30.0, exp=expires_at, jti="expired-jti")
    )
    provider = provider_module.ProxyAssertionAuthProvider({"signing_secret": SECRET})
    assert (await provider.authenticate(consumed)).authenticated is True

    clock[0] = expires_at + ASSERTION_CLOCK_SKEW_SECONDS + 0.001
    expired = await provider.authenticate(consumed)
    assert expired.authenticated is False
    assert expired.error == "Identity assertion has expired"

    # A new valid assertion triggers the amortized sweep and demonstrates
    # that entries are reclaimed once no verifier can accept them.
    fresh = _sign_payload(
        _payload(
            iat=clock[0] - 1.0,
            exp=clock[0] + 30.0,
            jti="fresh-jti",
        )
    )
    assert (await provider.authenticate(fresh)).authenticated is True
    assert "expired-jti" not in provider._seen
