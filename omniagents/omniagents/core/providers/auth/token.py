"""Bearer-token authentication provider.

Mirrors the existing single-token auth in the server backend but
wrapped as a provider so it can be swapped.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity

logger = logging.getLogger(__name__)


def _expand_env(raw: Any) -> Tuple[str, Optional[str]]:
    """Expand a ``${VAR}`` reference.

    Returns ``(value, env_var_name)`` where *env_var_name* is the name of
    the referenced environment variable, or ``None`` for literal values.
    """
    if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
        name = raw[2:-1]
        return os.getenv(name, ""), name
    return (str(raw) if raw is not None else "", None)


class TokenAuthProvider(AuthProvider):
    """Validates bearer tokens from config or environment.

    Config::

        security:
          providers:
            auth:
              type: token
              token: "${AUTH_TOKEN}"   # or literal value

    Multi-user deployments can map distinct tokens to distinct user ids
    (each value may also be a ``{user_id, roles}`` object)::

        security:
          providers:
            auth:
              type: token
              tokens:
                "${ALICE_TOKEN}": alice
                "${BOB_TOKEN}": { user_id: bob, roles: [admin] }

    Selecting this provider is an explicit request for authentication, so
    it fails closed: if the configuration resolves to **zero** usable
    credentials (e.g. every referenced environment variable is unset),
    construction raises :class:`ValueError` and the server refuses to
    start, rather than silently running open. Entries that fail to
    resolve while others succeed are skipped with a warning — only the
    resolved tokens authenticate.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        cfg = config or {}
        unresolved: List[str] = []

        token_value, env_name = _expand_env(cfg.get("token", ""))
        self._token: str = token_value
        if "token" in cfg and not token_value:
            unresolved.append(env_name or "`token` (empty literal)")

        # token value -> UserIdentity
        self._tokens: Dict[str, UserIdentity] = {}
        for raw_token, spec in (cfg.get("tokens") or {}).items():
            token_value, env_name = _expand_env(raw_token)
            if not token_value:
                unresolved.append(env_name or "`tokens` entry (empty literal)")
                continue
            if isinstance(spec, dict):
                identity = UserIdentity(
                    user_id=str(spec.get("user_id", "token_user")),
                    roles=list(spec.get("roles") or []),
                )
            else:
                identity = UserIdentity(user_id=str(spec))
            self._tokens[token_value] = identity

        if not self._token and not self._tokens:
            detail = (
                "unresolved entries: " + ", ".join(unresolved)
                if unresolved
                else "no `token` or `tokens` entries were provided"
            )
            raise ValueError(
                "Token authentication is configured but resolved zero usable "
                f"credentials ({detail}). Refusing to start with "
                "authentication silently disabled — set the referenced "
                "environment variables, or use `security.providers.auth.type: "
                "none` for an explicitly unauthenticated server."
            )
        if unresolved:
            logger.warning(
                "TokenAuthProvider: %d configured token(s) did not resolve "
                "and were skipped (%s); only the %d resolved credential(s) "
                "will authenticate.",
                len(unresolved),
                ", ".join(unresolved),
                len(self._tokens) + (1 if self._token else 0),
            )

    async def authenticate(self, request: Any) -> AuthResult:
        token = self._extract_token(request)
        if token:
            for candidate, identity in self._tokens.items():
                if hmac.compare_digest(token, candidate):
                    return AuthResult(authenticated=True, identity=identity)
            if self._token and hmac.compare_digest(token, self._token):
                return AuthResult(
                    authenticated=True,
                    identity=UserIdentity(user_id="token_user"),
                )
        return AuthResult(authenticated=False, error="Invalid token")

    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        result = await self.authenticate(request)
        return result.identity if result.authenticated else None

    @staticmethod
    def _extract_token(request: Any) -> Optional[str]:
        """Extract bearer token from query params or headers."""
        # FastAPI WebSocket
        if hasattr(request, "query_params"):
            token = request.query_params.get("token")
            if token:
                return token
        # HTTP header
        if hasattr(request, "headers"):
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:]
        # Plain string (for testing)
        if isinstance(request, str):
            return request
        return None
