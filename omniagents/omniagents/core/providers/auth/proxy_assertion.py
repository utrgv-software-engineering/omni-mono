"""Auth provider for upstreams sitting behind an OmniAgents serve proxy.

The proxy hop (``remote_ws_url`` in
:mod:`omniagents.backends.server.app`) authenticates the local user with
its own provider and then mints a short-lived signed assertion naming
that principal (:mod:`omniagents.rpc.identity_assertion`). This provider
is the other half: it validates the assertion and returns the *asserted*
identity, so the upstream's ownership rules see one distinct principal
per proxied user instead of a single shared service account.

Configure it on the **upstream**::

    security:
      providers:
        auth:
          type: proxy_assertion
          signing_secret: "${OMNI_PROXY_IDENTITY_SECRET}"
          audience: "omniagents-upstream"   # optional; must match the proxy
          allowed_roles: [operator]          # optional; defaults empty

and on the **proxy**::

    remote_agent:
      ws_url: "wss://upstream.example/ws"
      identity_secret_env: OMNI_PROXY_IDENTITY_SECRET
      identity_audience: "omniagents-upstream"   # optional
      identity_allowed_roles: [operator]          # optional; defaults empty

Selecting this provider is an explicit request for authentication, so it
fails closed exactly like
:class:`~omniagents.core.providers.auth.token.TokenAuthProvider`: an
unresolved or too-short secret raises at construction and the server
refuses to start rather than running open.

Only clients that hold the signing secret can authenticate here, so this
provider is for upstreams reached **exclusively** through a trusted
proxy. It does not accept end-user credentials of any kind, and it never
reads a credential from the query string — assertions travel in the
``Authorization`` header only.

An upstream that also serves **direct** clients composes this with
another provider rather than choosing between them::

    security:
      providers:
        auth:
          type: composite
          accept:
            - {type: proxy_assertion, signing_secret_env: OMNI_PROXY_IDENTITY_SECRET}
            - {type: oidc, ...}

The signing secret must be one no client ever holds: anyone with it can
mint an assertion for any user, including one carrying the ``admin``
role. ``build_app`` refuses to start a proxy whose signing secret is
also one of its own configured client credentials.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity
from omniagents.core.providers.auth.token import _expand_env

logger = logging.getLogger(__name__)

__all__ = ["ProxyAssertionAuthProvider"]

#: Global cap on the replay cache — a memory backstop, not the primary
#: control. Reaching it refuses new assertions rather than evicting
#: unexpired ones; see :meth:`ProxyAssertionAuthProvider._consume`.
_MAX_REPLAY_ENTRIES = 16384

#: Per-principal sub-quota. This is what makes a flood self-limiting:
#: one principal cannot consume the global cap, so refusals land on the
#: flooder instead of on everyone. With a 30 s assertion lifetime this
#: allows ~8 sustained connects/second per principal, far above any
#: legitimate reconnect pattern.
_MAX_REPLAY_ENTRIES_PER_PRINCIPAL = 256

#: Expired-entry sweeps are amortised to this interval so a refused
#: attempt costs a dict lookup rather than a full scan.
_PRUNE_INTERVAL_SECONDS = 1.0

#: Capacity refusals log at most this often (they arrive at flood rate).
_CAPACITY_LOG_INTERVAL_SECONDS = 10.0

#: ``_consume`` outcomes.
CONSUME_OK = "ok"
CONSUME_REPLAY = "replay"
CONSUME_CAPACITY = "capacity"
CONSUME_PRINCIPAL_QUOTA = "principal_quota"


class ProxyAssertionAuthProvider(AuthProvider):
    """Validates identity assertions minted by a trusted proxy hop.

    This provider identifies distinct principals — that is its whole
    purpose — and does **not** issue browser connect tickets: browsers
    never talk to an upstream in this topology, and a ticket would move a
    proxy-derived credential into a query string with a fresh TTL.
    """

    #: Declared capabilities; see :class:`AuthProvider`.
    identifies_principals = True
    supports_connect_tickets = False

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        from omniagents.rpc.identity_assertion import (
            DEFAULT_ASSERTION_AUDIENCE,
            normalize_role_allowlist,
            validate_signing_secret,
        )

        cfg = config or {}
        # ``signing_secret_env`` names an environment variable, the same
        # shape the proxy uses (``remote_agent.identity_secret_env``). The
        # two hops taking the secret by *different* syntaxes is exactly
        # how an operator ends up signing with the NAME of their
        # environment variable instead of its value.
        secret: str
        env_name: Optional[str]
        secret_env = cfg.get("signing_secret_env")
        if secret_env:
            if cfg.get("signing_secret"):
                raise ValueError(
                    "Proxy-assertion authentication has both `signing_secret` "
                    "and `signing_secret_env`; configure exactly one."
                )
            secret, env_name = os.getenv(str(secret_env), ""), str(secret_env)
        else:
            secret, env_name = _expand_env(cfg.get("signing_secret", ""))
        try:
            self._secret = validate_signing_secret(secret)
        except ValueError as exc:
            source = f"`{env_name}`" if env_name else "`signing_secret`"
            raise ValueError(
                f"Proxy-assertion authentication is configured but {source} "
                f"did not resolve a usable signing secret: {exc} Refusing to "
                "start with authentication silently disabled."
            ) from exc
        self._audience = str(cfg.get("audience") or DEFAULT_ASSERTION_AUDIENCE)
        self._allowed_roles = normalize_role_allowlist(
            cfg.get("allowed_roles"), field_name="proxy_assertion.allowed_roles"
        )
        # jti -> expiry. Assertions are single-use **within this process**
        # (see ``_consume``); a horizontally scaled upstream needs a
        # shared cache for that to hold deployment-wide.
        self._seen: Dict[str, Tuple[float, str]] = {}
        self._per_principal: Dict[str, int] = {}
        self._next_prune = 0.0
        self._next_capacity_log = 0.0

    # ------------------------------------------------------------------
    # AuthProvider
    # ------------------------------------------------------------------
    async def authenticate(self, request: Any) -> AuthResult:
        from omniagents.rpc.identity_assertion import (
            InvalidIdentityAssertion,
            verify_identity_assertion,
        )

        token = self._extract_assertion(request)
        if not token:
            return AuthResult(
                authenticated=False,
                error="An identity assertion is required on this server",
            )
        try:
            verified = verify_identity_assertion(
                token,
                secret=self._secret,
                audience=self._audience,
                allowed_roles=self._allowed_roles,
            )
        except InvalidIdentityAssertion as exc:
            logger.warning("Rejected proxy identity assertion: %s", exc)
            return AuthResult(authenticated=False, error=str(exc))

        outcome = self._consume(
            verified.jti, verified.expires_at, verified.identity.user_id
        )
        if outcome == CONSUME_REPLAY:
            logger.warning(
                "Rejected replayed proxy identity assertion for user_id=%s",
                verified.identity.user_id,
            )
            return AuthResult(
                authenticated=False, error="Identity assertion was already used"
            )
        if outcome != CONSUME_OK:
            # Capacity, not credentials. Reported as retryable so a
            # short-lived condition does not permanently disconnect
            # clients that (correctly) never retry a 4401.
            self._log_capacity(outcome, verified.identity.user_id)
            return AuthResult(
                authenticated=False,
                error="Too many concurrent connection attempts; retry shortly",
                retryable=True,
            )
        logger.debug(
            "Accepted proxy identity assertion for user_id=%s",
            verified.identity.user_id,
        )
        return AuthResult(authenticated=True, identity=verified.identity)

    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        result = await self.authenticate(request)
        return result.identity if result.authenticated else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_assertion(request: Any) -> Optional[str]:
        """Pull the assertion out of *request*.

        Header only. Query parameters are deliberately not consulted: an
        assertion in a URL would be logged by every intermediary, and the
        proxy strips credential query params before dialing anyway.
        """
        if isinstance(request, str):
            return request or None
        headers = getattr(request, "headers", None)
        if headers is not None:
            auth = headers.get("authorization", "") or ""
            if auth.lower().startswith("bearer "):
                return auth[7:] or None
        return None

    def _consume(self, jti: str, expires_at: float, subject: str) -> str:
        """Record *jti* as used. Returns one of the ``CONSUME_*`` outcomes.

        Three properties, in tension, all required:

        * **Unflushable.** Only *expired* entries are ever dropped.
          Evicting an unexpired entry to make room would let anyone who
          can connect forget a captured jti and replay it.
        * **Contained.** Each principal gets its own sub-quota, so a
          flood costs the flooder and nobody else. Refusing globally was
          measured at 635 conn/s filling the cache in 25.8 s — inside the
          assertion lifetime — from one ordinary authenticated user.
        * **Cheap.** The replay check is a dict lookup and pruning is
          amortised to at most once a second, so a refused attempt no
          longer costs a full-dictionary scan (measured at 339 µs each,
          which made the flood cheaper for the attacker than the target).
        """
        now = time.time()
        # O(1) and first: a replay must be caught even at capacity.
        if jti in self._seen:
            return CONSUME_REPLAY

        # Amortised, unconditionally. An "emergency" sweep when the cache
        # is full sounds prudent and is the opposite: at the global cap
        # the entries are by definition unexpired, so the scan reclaims
        # nothing and simply pays a full-dictionary walk on every attempt
        # — including refused ones — which is the cost this amortisation
        # exists to remove. Waiting up to one second to reclaim is fine.
        if now >= self._next_prune:
            self._prune(now)

        if self._per_principal.get(subject, 0) >= _MAX_REPLAY_ENTRIES_PER_PRINCIPAL:
            return CONSUME_PRINCIPAL_QUOTA
        if len(self._seen) >= _MAX_REPLAY_ENTRIES:
            return CONSUME_CAPACITY

        self._seen[jti] = (expires_at, subject)
        self._per_principal[subject] = self._per_principal.get(subject, 0) + 1
        return CONSUME_OK

    def _prune(self, now: float) -> None:
        """Drop expired entries and decay their per-principal counts."""
        self._next_prune = now + _PRUNE_INTERVAL_SECONDS
        expired = [key for key, (exp, _) in self._seen.items() if exp < now]
        for key in expired:
            _, subject = self._seen.pop(key)
            remaining = self._per_principal.get(subject, 0) - 1
            if remaining > 0:
                self._per_principal[subject] = remaining
            else:
                self._per_principal.pop(subject, None)

    def _log_capacity(self, outcome: str, subject: str) -> None:
        """Log a capacity refusal at most once per interval, not per attempt."""
        now = time.time()
        if now < self._next_capacity_log:
            return
        self._next_capacity_log = now + _CAPACITY_LOG_INTERVAL_SECONDS
        if outcome == CONSUME_PRINCIPAL_QUOTA:
            logger.warning(
                "Principal %s exceeded its identity-assertion quota (%d live "
                "assertions); refusing further connections from it. Other "
                "principals are unaffected.",
                subject,
                _MAX_REPLAY_ENTRIES_PER_PRINCIPAL,
            )
        else:
            logger.error(
                "Identity-assertion replay cache is full (%d live entries) "
                "even after per-principal quotas; refusing new assertions "
                "rather than forgetting used ones.",
                len(self._seen),
            )
