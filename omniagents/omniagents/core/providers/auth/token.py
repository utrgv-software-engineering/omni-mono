"""Bearer-token authentication provider.

Mirrors the existing single-token auth in the server backend but
wrapped as a provider so it can be swapped.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity


class TokenAuthProvider(AuthProvider):
    """Validates a single bearer token from config or environment.

    Config::

        security:
          providers:
            auth:
              type: token
              token: "${AUTH_TOKEN}"   # or literal value
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        import os

        raw = (config or {}).get("token", "")
        # Support ${ENV_VAR} expansion
        if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
            var = raw[2:-1]
            raw = os.getenv(var, "")
        self._token: str = str(raw)

    async def authenticate(self, request: Any) -> AuthResult:
        token = self._extract_token(request)
        if not self._token:
            # No token configured — allow all (same as NoneAuth)
            return AuthResult(
                authenticated=True,
                identity=UserIdentity(user_id="anonymous"),
            )
        if token == self._token:
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
