"""Platform authentication provider — validates runtime JWTs from omni-platform.

The management plane issues short-lived RS256 JWTs to agents at session start.
This provider validates them against the platform's JWKS endpoint, which
exposes the public key used for signing.

Config::

    security:
      providers:
        auth:
          type: "platform"
          platform_url: "https://omni-platform.example.edu"
          # OR override individual URLs:
          jwks_uri: "https://omni-platform.example.edu/api/v1/auth/jwks"
          audience: "omni-agents"    # default
          issuer: "omni-platform"    # default
          jwks_cache_ttl: 3600       # seconds, default
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity


def _expand_env(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} in a string."""
    if not value:
        return value
    if value.startswith("${") and value.endswith("}"):
        inner = value[2:-1]
        if ":-" in inner:
            var, default = inner.split(":-", 1)
            return os.getenv(var, default)
        return os.getenv(inner, "")
    return value


class PlatformAuthProvider(AuthProvider):
    """Validates JWTs issued by the omni-platform management plane.

    Uses the OIDC auth provider under the hood, configured to point at
    the platform's JWKS endpoint. This is a thin wrapper that resolves
    platform_url into the correct JWKS URI and sets platform-specific
    defaults (issuer, audience).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        cfg = config or {}

        platform_url = _expand_env(cfg.get("platform_url", "")).rstrip("/")
        self._jwks_uri = _expand_env(
            cfg.get("jwks_uri", f"{platform_url}/api/v1/auth/jwks")
        )
        self._issuer = cfg.get("issuer", "omni-platform")
        self._audience = cfg.get("audience", "omni-agents")
        self._algorithms: List[str] = cfg.get("algorithms", ["RS256"])
        self._jwks_cache_ttl: int = int(cfg.get("jwks_cache_ttl", 3600))
        self._jwks_client: Any = None

    def _get_jwks_client(self) -> Any:
        if self._jwks_client is None:
            import jwt

            self._jwks_client = jwt.PyJWKClient(
                self._jwks_uri,
                cache_jwk_set=True,
                lifespan=self._jwks_cache_ttl,
            )
        return self._jwks_client

    async def authenticate(self, request: Any) -> AuthResult:
        """Validate a runtime JWT from the management plane."""
        token = self._extract_bearer_token(request)
        if not token:
            return AuthResult(authenticated=False, error="No bearer token")

        try:
            import jwt

            jwks_client = self._get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            decode_opts: Dict[str, Any] = {
                "algorithms": self._algorithms,
            }
            if self._audience:
                decode_opts["audience"] = self._audience
            if self._issuer:
                decode_opts["issuer"] = self._issuer

            claims = jwt.decode(token, signing_key.key, **decode_opts)

            identity = UserIdentity(
                user_id=str(claims.get("sub", "unknown")),
                email=claims.get("email"),
                display_name=claims.get("name"),
                roles=claims.get("roles", []),
                claims=claims,
            )
            return AuthResult(authenticated=True, identity=identity)
        except Exception as exc:
            return AuthResult(authenticated=False, error=str(exc))

    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        result = await self.authenticate(request)
        return result.identity if result.authenticated else None

    @staticmethod
    def _extract_bearer_token(request: Any) -> Optional[str]:
        """Extract bearer token from Authorization header, query params, or env."""
        # HTTP header
        if hasattr(request, "headers"):
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:]
        # Query param fallback (WebSocket upgrade)
        if hasattr(request, "query_params"):
            token = request.query_params.get("token")
            if token:
                return token
        # Environment variable fallback (container injection)
        env_token = os.getenv("OMNI_RUNTIME_TOKEN")
        if env_token:
            return env_token
        # Plain string (testing)
        if isinstance(request, str):
            return request
        return None
