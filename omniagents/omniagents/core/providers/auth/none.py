"""No-op authentication provider (default for open-source deployments)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity


class NoneAuthProvider(AuthProvider):
    """Always authenticates — no credentials required.

    Returns an anonymous identity with ``user_id="anonymous"``.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        self._identity = UserIdentity(user_id="anonymous")

    async def authenticate(self, request: Any) -> AuthResult:
        return AuthResult(authenticated=True, identity=self._identity)

    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        return self._identity
