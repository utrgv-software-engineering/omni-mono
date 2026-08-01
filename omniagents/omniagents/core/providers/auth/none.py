"""No-op authentication provider (default for open-source deployments)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity


class NoneAuthProvider(AuthProvider):
    """Always authenticates — no credentials required.

    Returns an anonymous identity with ``user_id="anonymous"``.

    Every connection is the same principal, so ownership enforcement has
    nothing to separate. A subclass that *does* resolve distinct
    principals (e.g. a gateway-header provider in front of Azure EasyAuth
    or oauth2-proxy) must set ``identifies_principals = True``;
    inheriting this False silently collapses every user into one identity.

    Setting that one flag is sufficient: ``enforces_authorization``
    follows it here rather than being a second inheritable attribute.
    Making an operator remember both would fail *open* — a subclass that
    declared per-user identity but inherited a False enforcement flag
    would pass the serve proxy's gate, propagate per-user identity
    upstream, and look entirely correct while running with no ownership
    checks locally.
    """

    identifies_principals = False

    @property
    def enforces_authorization(self) -> bool:  # type: ignore[override]
        """Enforce exactly when this provider distinguishes users.

        Anonymous single-user mode has nothing to enforce; a subclass
        that resolves real principals does.
        """
        return bool(self.identifies_principals)

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        self._identity = UserIdentity(user_id="anonymous")

    async def authenticate(self, request: Any) -> AuthResult:
        return AuthResult(authenticated=True, identity=self._identity)

    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        return self._identity
