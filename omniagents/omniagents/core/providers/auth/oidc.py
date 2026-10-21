"""OIDC / JWT authentication provider.

Validates JWT tokens using JWKS from a configured issuer.  Enterprise
deployments (Azure Entra ID, Okta, Auth0) configure issuer, audience,
and JWKS URI.

Requires the ``PyJWT[crypto]`` optional dependency::

    pip install "omniagents[oidc]"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity


class OIDCAuthProvider(AuthProvider):
    """Validates JWTs against a JWKS endpoint.

    Config::

        security:
          providers:
            auth:
              type: oidc
              issuer: "https://login.microsoftonline.com/{tenant}/v2.0"
              audience: "api://omniagents"
              jwks_uri: "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
              user_id_claim: "oid"        # default: "sub"
              email_claim: "email"        # default: "email"
              roles_claim: "roles"        # default: "roles"
              algorithms: ["RS256"]       # default: ["RS256"]
              jwks_cache_ttl: 3600        # seconds, default: 3600
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        cfg = config or {}
        self._issuer: str = cfg.get("issuer", "")
        self._audience: str = cfg.get("audience", "")
        self._jwks_uri: str = cfg.get("jwks_uri", "")
        self._user_id_claim: str = cfg.get("user_id_claim", "sub")
        self._email_claim: str = cfg.get("email_claim", "email")
        self._roles_claim: str = cfg.get("roles_claim", "roles")
        self._algorithms: List[str] = cfg.get("algorithms", ["RS256"])
        self._jwks_cache_ttl: int = int(cfg.get("jwks_cache_ttl", 3600))
        self._jwks_client: Any = None  # lazy init

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
        """Authenticate a request by validating the JWT bearer token."""
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
                user_id=str(
                    claims.get(self._user_id_claim, claims.get("sub", "unknown"))
                ),
                email=claims.get(self._email_claim),
                display_name=claims.get("name"),
                roles=claims.get(self._roles_claim, []),
                claims=claims,
            )
            return AuthResult(authenticated=True, identity=identity)
        except Exception as exc:
            return AuthResult(authenticated=False, error=str(exc))

    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        """Extract user identity from an already-authenticated request."""
        result = await self.authenticate(request)
        return result.identity if result.authenticated else None

    @staticmethod
    def _extract_bearer_token(request: Any) -> Optional[str]:
        """Extract bearer token from Authorization header or query params."""
        # HTTP header
        if hasattr(request, "headers"):
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:]
        # Query param fallback (e.g., WebSocket upgrade)
        if hasattr(request, "query_params"):
            token = request.query_params.get("token")
            if token:
                return token
        # Plain string (for testing)
        if isinstance(request, str):
            return request
        return None
