"""Signed identity assertions — carrying an authenticated principal across a hop.

A ``remote_ws_url`` deployment puts a *proxy* server in front of an
*upstream* one. The proxy authenticates the local user itself (see
:mod:`omniagents.rpc.auth`) and then opens its own WebSocket to the
upstream. Something has to tell the upstream **who** that connection is
for, or the upstream's ownership rules (``AgentService.authorize_rpc``)
have nothing to separate and every proxied user collapses into one
identity.

This module is that something: the proxy mints a short-lived
HMAC-SHA256-signed assertion naming the principal it just authenticated,
and presents it upstream in the ordinary ``Authorization: Bearer``
header. The upstream validates it with
:class:`~omniagents.core.providers.auth.proxy_assertion.ProxyAssertionAuthProvider`
and resolves the *asserted* principal, so its ownership checks see
``alice`` and ``bob`` as distinct principals exactly as a direct
connection would.

Format (deliberately not JWT)::

    oaia1.<base64url(payload json)>.<base64url(hmac_sha256)>

The version prefix is a fixed constant covered by the signature and the
algorithm is not encoded in the token at all, so there is no ``alg``
field an attacker can influence — the JWT algorithm-confusion class of
bug cannot occur here. Verification is:

* an ASCII-only guard first, because ASGI decodes headers as latin-1 and
  the comparisons below raise on non-ASCII input;
* constant-time MAC comparison over ``"oaia1." + payload``, keyed by an
  HKDF-derived key rather than the configured secret itself, so a secret
  that is also valid in another role does not double as a forging key;
* audience match (constant-time) so an assertion minted for one upstream
  cannot be replayed at another;
* lifetimes that must be **finite numbers** of a numeric type — NaN
  passes every ``<``/``>`` comparison, so one NaN field would otherwise
  satisfy the cap, never expire and never be premature at once;
* ``exp`` in the future and ``iat`` not in the future, both with a small
  clock-skew allowance, and a hard cap on ``exp - iat`` so a proxy
  cannot mint a long-lived bearer credential;
* every field parsed *after* the MAC check — unauthenticated bytes are
  never interpreted.

Assertions are single-use **within one upstream process** (a ``jti``
replay cache lives in the provider, and it is never flushed by pressure
— see ``ProxyAssertionAuthProvider._consume``), mirroring the one-time
connect tickets minted by :class:`~omniagents.rpc.auth.WsTicketBroker`.
A horizontally scaled upstream keeps one cache per replica, so making
single-use hold deployment-wide needs a shared store; until then the
residual exposure is one replay per replica inside the (short) lifetime.

**Trust model.** The signing secret makes the proxy a trusted identity
*issuer* for the upstream: a compromised proxy can assert any user id.
That is inherent to terminating the client connection at the proxy —
the alternative (the proxy holding one upstream credential per user) has
the same blast radius and a larger secret inventory. What the secret
buys is that the upstream can *distinguish* proxied users at all, and
that a user who reaches the proxy cannot choose which identity is
asserted for them.
"""

from __future__ import annotations

import base64
import hmac
import logging
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Optional

from omniagents.core.providers.auth.base import UserIdentity

__all__ = [
    "ASSERTION_VERSION",
    "DEFAULT_ASSERTION_AUDIENCE",
    "DEFAULT_ASSERTION_TTL_SECONDS",
    "MAX_ASSERTION_TTL_SECONDS",
    "MIN_SIGNING_SECRET_LENGTH",
    "MAX_ASSERTED_ROLES",
    "MAX_ASSERTED_ROLE_LENGTH",
    "InvalidIdentityAssertion",
    "VerifiedAssertion",
    "mint_identity_assertion",
    "normalize_role_allowlist",
    "validate_signing_secret",
    "verify_identity_assertion",
]

#: Fixed token version prefix. Covered by the signature; never parsed as
#: an algorithm selector.
ASSERTION_VERSION = "oaia1"

#: Default audience. Both hops default to it, so a deployment only has to
#: set an audience when one proxy fans out to several upstreams.
DEFAULT_ASSERTION_AUDIENCE = "omniagents-upstream"

#: Assertions are presented once, at connect. Keep the window tiny: the
#: exposure window for a captured assertion is this plus the skew
#: allowance below.
DEFAULT_ASSERTION_TTL_SECONDS = 30.0

#: Hard ceiling enforced at verification, so a misconfigured or hostile
#: minter cannot turn an assertion into a long-lived bearer token.
MAX_ASSERTION_TTL_SECONDS = 120.0

#: Minimum signing-secret length. This is an HMAC key, not a password:
#: refusing short secrets at configuration time is cheaper than
#: discovering a brute-forceable one in production.
MIN_SIGNING_SECRET_LENGTH = 32

#: Keep assertion/config parsing bounded. Roles are exact, case-sensitive
#: identifiers; no case folding or whitespace rewriting is performed.
MAX_ASSERTED_ROLES = 64
MAX_ASSERTED_ROLE_LENGTH = 128

#: Dropped-role warnings can otherwise arrive once per proxied connection.
_DROPPED_ROLE_LOG_INTERVAL_SECONDS = 10.0
_next_dropped_role_log = 0.0

#: Minimum number of *distinct* characters. Length alone accepts
#: ``"a" * 32``, 32 spaces and 32 NUL bytes, none of which is a key.
MIN_SIGNING_SECRET_DISTINCT_CHARS = 8

#: Both hops are in one deployment and expected to be NTP-synced. This
#: adds directly to the replay window, so keep it small.
_CLOCK_SKEW_SECONDS = 10.0

_ISSUER = "omniagents-proxy"

#: Shapes that are configuration mistakes rather than secrets. The two
#: hops take the secret by different routes (the proxy names an env var,
#: the upstream takes a ``${VAR}`` reference or its own ``*_env`` key), so
#: an operator who mirrors the wrong syntax ends up with the *name* of
#: their environment variable as the signing key — a value that is
#: usually public, and long enough to pass a length check.
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: HKDF (RFC 5869) parameters. The signing key is *derived* from the
#: configured secret with a purpose-specific info string, so the same
#: byte string used in another role — an upstream bearer token, a client
#: credential — cannot be used to forge assertions with a naive
#: ``hmac(secret, signing_input)``. The MAC prefix ``oaia1.`` is a public
#: constant and carries no per-purpose entropy on its own.
_HKDF_SALT = b"omniagents.identity-assertion.hkdf.salt.v1"
_HKDF_INFO = b"omniagents/identity-assertion/hmac-sha256/v1"

logger = logging.getLogger(__name__)


class InvalidIdentityAssertion(Exception):
    """An assertion failed signature, audience, or freshness validation."""


@dataclass(frozen=True)
class VerifiedAssertion:
    """A validated assertion: the asserted principal plus replay metadata."""

    identity: UserIdentity
    jti: str
    expires_at: float


def normalize_role_allowlist(
    roles: Any, *, field_name: str = "allowed_roles"
) -> List[str]:
    """Validate and de-duplicate an explicit role allowlist.

    Role matching is deliberately exact and case-sensitive. Empty is a valid
    (and safe) default: identity assertions carry no roles until both hops are
    configured to permit them.
    """
    if roles is None:
        return []
    if not isinstance(roles, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of role strings")

    normalized: List[str] = []
    seen = set()
    for role in roles:
        if not isinstance(role, str) or not role:
            raise ValueError(f"{field_name} entries must be non-empty strings")
        if len(role) > MAX_ASSERTED_ROLE_LENGTH:
            raise ValueError(
                f"{field_name} role {role!r} exceeds "
                f"{MAX_ASSERTED_ROLE_LENGTH} characters"
            )
        if role not in seen:
            seen.add(role)
            normalized.append(role)
            if len(normalized) > MAX_ASSERTED_ROLES:
                raise ValueError(
                    f"{field_name} permits at most {MAX_ASSERTED_ROLES} roles"
                )
    return normalized


def _filter_asserted_roles(
    identity: UserIdentity, allowed_roles: Iterable[str]
) -> List[str]:
    """Return locally-held roles explicitly permitted for assertion."""
    global _next_dropped_role_log

    allowed = set(allowed_roles)
    selected: List[str] = []
    seen = set()
    dropped: List[str] = []
    for role in identity.roles or []:
        if (
            not isinstance(role, str)
            or not role
            or len(role) > MAX_ASSERTED_ROLE_LENGTH
        ):
            dropped.append(repr(role))
        elif role not in allowed:
            dropped.append(role)
        elif role not in seen:
            seen.add(role)
            selected.append(role)

    if dropped:
        clock = time.monotonic()
        if clock >= _next_dropped_role_log:
            _next_dropped_role_log = clock + _DROPPED_ROLE_LOG_INTERVAL_SECONDS
            logger.warning(
                "Dropped roles not permitted by remote_agent.identity_allowed_roles "
                "while minting proxy assertion for user_id=%s: %s",
                identity.user_id,
                ", ".join(dropped[:MAX_ASSERTED_ROLES]),
            )
    return selected


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


_GENERATE_HINT = (
    'Generate one with `python -c "import secrets; '
    'print(secrets.token_urlsafe(32))"`.'
)


def validate_signing_secret(secret: Optional[str]) -> str:
    """Return *secret* if it is usable as an HMAC key, else raise.

    Fails closed at configuration time, the
    :class:`~omniagents.core.providers.auth.token.TokenAuthProvider`
    precedent: a proxy that cannot sign must not start rather than fall
    back to a weaker credential.

    Length is necessary but nowhere near sufficient. This also rejects
    the values that are *configuration mistakes wearing the right length*
    — an unexpanded environment-variable name, a stringified
    dict/list, untrimmed whitespace, and low-entropy filler like ``"a" *
    32``. Each of those was verified to sail through a length-only check
    while being either public or trivially guessable.
    """
    if not secret:
        raise ValueError(
            "An identity-assertion signing secret is required but resolved "
            f"empty. {_GENERATE_HINT}"
        )
    if secret != secret.strip():
        raise ValueError(
            "The identity-assertion signing secret has leading or trailing "
            "whitespace, which usually means the value was pasted or quoted "
            "by accident. Trim it (or re-generate it)."
        )
    if len(secret) < MIN_SIGNING_SECRET_LENGTH:
        raise ValueError(
            "The identity-assertion signing secret is too short "
            f"({len(secret)} characters); at least "
            f"{MIN_SIGNING_SECRET_LENGTH} are required. {_GENERATE_HINT}"
        )
    if _ENV_VAR_NAME_RE.match(secret) and secret.upper() == secret:
        raise ValueError(
            f"The identity-assertion signing secret is `{secret}`, which is "
            "the shape of an environment-variable NAME, not a secret — the "
            "reference was almost certainly not expanded. On the proxy use "
            "`remote_agent.identity_secret_env: <VAR>`; on the upstream use "
            '`signing_secret_env: <VAR>` or `signing_secret: "${<VAR>}"`.'
        )
    if secret.startswith(("{", "[")):
        raise ValueError(
            "The identity-assertion signing secret looks like a stringified "
            "structure rather than a secret; pass the secret value itself."
        )
    if not secret.isprintable():
        raise ValueError(
            "The identity-assertion signing secret contains non-printable "
            f"characters. {_GENERATE_HINT}"
        )
    if len(set(secret)) < MIN_SIGNING_SECRET_DISTINCT_CHARS:
        raise ValueError(
            "The identity-assertion signing secret has too little variety "
            f"({len(set(secret))} distinct characters; at least "
            f"{MIN_SIGNING_SECRET_DISTINCT_CHARS} are required). "
            f"{_GENERATE_HINT}"
        )
    return secret


def _log_skew(kind: str, offset_seconds: float) -> None:
    """Report the measured clock offset on a freshness rejection.

    The realistic cause is not NTP drift but a *step* event — a suspended
    VM resuming, or WSL2's clock lagging after the host sleeps, which
    matters because a WSL backend is a shipped topology. The failure is a
    terminal, non-self-healing outage for every proxied user, so the
    operator needs the number rather than a bare "expired".
    """
    logger.warning(
        "Identity assertion rejected as %s: clock skew between the proxy and "
        "this upstream measured at %.1fs, beyond the %.0fs allowance. Check "
        "time synchronisation on both hops (a suspended/resumed VM or a WSL2 "
        "distro after host sleep will do this).",
        kind,
        offset_seconds,
        _CLOCK_SKEW_SECONDS,
    )


def _finite_timestamp(value: Any) -> float:
    """Coerce a signed lifetime field to a real, finite float, or refuse.

    Strictly typed on purpose: ``float("nan")`` also accepts the *string*
    ``"nan"``, and ``bool`` is an ``int``, so an unconstrained coercion
    is how a non-number reaches the comparisons below.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidIdentityAssertion("Identity assertion has invalid lifetimes")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidIdentityAssertion("Identity assertion has invalid lifetimes")
    return number


def _derive_key(secret: str) -> bytes:
    """HKDF-SHA256 (RFC 5869) — one 32-byte block, purpose-bound.

    Domain separation, not stretching: the derived key is only usable for
    signing identity assertions, so a secret that is *also* valid in
    another role does not hand its holder a forging oracle.
    """
    prk = hmac.new(_HKDF_SALT, secret.encode("utf-8"), sha256).digest()
    return hmac.new(prk, _HKDF_INFO + b"\x01", sha256).digest()


def _sign(secret: str, signing_input: str) -> bytes:
    return hmac.new(_derive_key(secret), signing_input.encode("ascii"), sha256).digest()


def mint_identity_assertion(
    identity: UserIdentity,
    *,
    secret: str,
    audience: str = DEFAULT_ASSERTION_AUDIENCE,
    ttl_seconds: float = DEFAULT_ASSERTION_TTL_SECONDS,
    now: Optional[float] = None,
    allowed_roles: Any = None,
) -> str:
    """Sign an assertion naming *identity* for one upstream connection."""
    key = validate_signing_secret(secret)
    # NaN fails every comparison, so an unguarded range check would mint
    # ``exp: nan``. Verification refuses those, but the guard is
    # two-sided on purpose: nothing should ever put a non-finite lifetime
    # on the wire.
    if not math.isfinite(ttl_seconds):
        raise ValueError("Assertion ttl_seconds must be a finite number.")
    if now is not None and not math.isfinite(now):
        raise ValueError("Assertion issue time must be a finite number.")
    if ttl_seconds <= 0 or ttl_seconds > MAX_ASSERTION_TTL_SECONDS:
        raise ValueError(
            f"Assertion ttl_seconds must be in (0, {MAX_ASSERTION_TTL_SECONDS}]; "
            f"got {ttl_seconds}."
        )
    issued = time.time() if now is None else now
    role_allowlist = normalize_role_allowlist(
        allowed_roles, field_name="remote_agent.identity_allowed_roles"
    )
    payload: Dict[str, Any] = {
        "v": ASSERTION_VERSION,
        "iss": _ISSUER,
        "aud": audience,
        "iat": issued,
        "exp": issued + ttl_seconds,
        "jti": secrets.token_urlsafe(16),
        "sub": identity.user_id,
        "email": identity.email,
        "name": identity.display_name,
        "roles": _filter_asserted_roles(identity, role_allowlist),
        "claims": dict(identity.claims or {}),
    }
    body = _b64u_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{ASSERTION_VERSION}.{body}"
    return f"{signing_input}.{_b64u_encode(_sign(key, signing_input))}"


def verify_identity_assertion(
    token: str,
    *,
    secret: str,
    audience: str = DEFAULT_ASSERTION_AUDIENCE,
    now: Optional[float] = None,
    allowed_roles: Any = None,
) -> VerifiedAssertion:
    """Validate *token* and return the principal it asserts.

    Raises :class:`InvalidIdentityAssertion` for every failure mode. The
    message is intentionally coarse — callers surface it as an
    authentication error, and a precise oracle would help an attacker
    probe the secret.
    """
    key = validate_signing_secret(secret)
    if not isinstance(token, str):
        raise InvalidIdentityAssertion("Assertion is not a string")

    # ASGI decodes request headers as latin-1, so an anonymous attacker
    # can put any byte in the ``Authorization`` header. Every byte of a
    # well-formed assertion is base64url or ``.``; anything else must be
    # refused *here*, because further down ``hmac.compare_digest`` raises
    # TypeError on a non-ASCII str and ``str.encode("ascii")`` raises
    # UnicodeEncodeError — neither of which is an
    # ``InvalidIdentityAssertion``, so both would escape the provider's
    # and the authenticator's handlers and crash the ASGI app pre-auth
    # instead of producing a 4401 close.
    if not token.isascii():
        raise InvalidIdentityAssertion("Malformed identity assertion")

    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidIdentityAssertion("Malformed identity assertion")
    version, body, signature = parts

    # Compare the version in constant time too: it is part of the signed
    # input, so a mismatch is a forgery attempt, not a format question.
    if not hmac.compare_digest(version, ASSERTION_VERSION):
        raise InvalidIdentityAssertion("Unsupported identity assertion version")

    expected = _sign(key, f"{version}.{body}")
    try:
        provided = _b64u_decode(signature)
    except Exception as exc:  # noqa: BLE001 - malformed base64 is a forgery
        raise InvalidIdentityAssertion("Malformed identity assertion") from exc
    if not hmac.compare_digest(expected, provided):
        raise InvalidIdentityAssertion("Identity assertion signature mismatch")

    # Only now — after the MAC verified — is the payload interpreted.
    try:
        payload = json.loads(_b64u_decode(body).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise InvalidIdentityAssertion("Malformed identity assertion") from exc
    if not isinstance(payload, dict):
        raise InvalidIdentityAssertion("Malformed identity assertion")

    if not hmac.compare_digest(str(payload.get("v", "")), ASSERTION_VERSION):
        raise InvalidIdentityAssertion("Unsupported identity assertion version")
    if not hmac.compare_digest(str(payload.get("aud", "")), audience):
        raise InvalidIdentityAssertion("Identity assertion audience mismatch")

    # Lifetimes must be real, finite numbers. Both halves matter:
    #
    # * ``bool`` is an ``int``, and strings like ``"nan"``/``"inf"`` are
    #   accepted by ``float()``, so a permissive coercion lets a
    #   non-number in;
    # * every temporal comparison below is a ``<``/``>`` against NaN,
    #   which is False. A NaN ``exp``/``iat`` therefore passes the
    #   lifetime cap, never expires, and is never premature — one value
    #   defeating all three checks and poisoning the replay cache's
    #   expiry pruning on the way through.
    issued_at = _finite_timestamp(payload.get("iat"))
    expires_at = _finite_timestamp(payload.get("exp"))
    if expires_at - issued_at > MAX_ASSERTION_TTL_SECONDS:
        raise InvalidIdentityAssertion("Identity assertion lifetime exceeds the cap")

    clock = time.time() if now is None else now
    if expires_at < clock - _CLOCK_SKEW_SECONDS:
        _log_skew("expired", clock - expires_at)
        raise InvalidIdentityAssertion("Identity assertion has expired")
    if issued_at > clock + _CLOCK_SKEW_SECONDS:
        _log_skew("not yet valid", issued_at - clock)
        raise InvalidIdentityAssertion("Identity assertion is not yet valid")

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidIdentityAssertion("Identity assertion names no subject")
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise InvalidIdentityAssertion("Identity assertion has no replay id")

    allowed = set(
        normalize_role_allowlist(
            allowed_roles, field_name="proxy_assertion.allowed_roles"
        )
    )
    raw_roles = payload.get("roles")
    if not isinstance(raw_roles, list):
        raise InvalidIdentityAssertion("Identity assertion has invalid roles")
    if len(raw_roles) > MAX_ASSERTED_ROLES:
        raise InvalidIdentityAssertion("Identity assertion has too many roles")
    roles: List[str] = []
    seen_roles = set()
    for role in raw_roles:
        if (
            not isinstance(role, str)
            or not role
            or len(role) > MAX_ASSERTED_ROLE_LENGTH
        ):
            raise InvalidIdentityAssertion("Identity assertion has invalid roles")
        if role not in allowed:
            raise InvalidIdentityAssertion(
                "Identity assertion contains unauthorized role"
            )
        if role not in seen_roles:
            seen_roles.add(role)
            roles.append(role)
    raw_claims = payload.get("claims")
    claims: Dict[str, Any] = dict(raw_claims) if isinstance(raw_claims, dict) else {}
    email = payload.get("email")
    display_name = payload.get("name")

    identity = UserIdentity(
        user_id=subject,
        email=email if isinstance(email, str) else None,
        display_name=display_name if isinstance(display_name, str) else None,
        roles=roles,
        claims=claims,
    )
    return VerifiedAssertion(identity=identity, jti=jti, expires_at=expires_at)
