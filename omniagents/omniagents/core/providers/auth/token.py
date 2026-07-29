"""Bearer-token authentication provider.

Mirrors the existing single-token auth in the server backend but
wrapped as a provider so it can be swapped.
"""

from __future__ import annotations

import hmac
from typing import Any, Dict, Optional

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity


def _expand_env(raw: Any) -> str:
    import os

    if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
        return os.getenv(raw[2:-1], "")
    return str(raw) if raw is not None else ""


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
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        self._token: str = _expand_env((config or {}).get("token", ""))
        # token value -> UserIdentity
        self._tokens: Dict[str, UserIdentity] = {}
        for raw_token, spec in ((config or {}).get("tokens") or {}).items():
            token_value = _expand_env(raw_token)
            if not token_value:
                continue
            if isinstance(spec, dict):
                identity = UserIdentity(
                    user_id=str(spec.get("user_id", "token_user")),
                    roles=list(spec.get("roles") or []),
                )
            else:
                identity = UserIdentity(user_id=str(spec))
            self._tokens[token_value] = identity

    async def authenticate(self, request: Any) -> AuthResult:
        token = self._extract_token(request)
        if not self._token and not self._tokens:
            # No token configured — allow all (same as NoneAuth)
            return AuthResult(
                authenticated=True,
                identity=UserIdentity(user_id="anonymous"),
            )
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
