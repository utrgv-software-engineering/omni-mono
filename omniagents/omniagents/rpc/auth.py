"""Unified connection authentication for every OmniAgents WebSocket surface.

One :class:`ConnectionAuthenticator` guards ``/ws``, ``/ws/realtime`` and
``/ws/terminal`` so all three resolve the *same* authenticated principal
(:class:`~omniagents.core.providers.auth.base.UserIdentity`) through the
configured :class:`~omniagents.core.providers.auth.base.AuthProvider`.

Supported credential transports, in resolution order:

1. ``Authorization: Bearer <token>`` header — native clients (Python
   ``RemoteAgent``, the Ink TUI, server-to-server proxies) attach the
   token as a header so it never appears in a URL.
2. ``?ticket=<one-time>`` — browsers cannot set WebSocket headers, so
   they first POST to ``/auth/ws-ticket`` (authenticated with the
   ``Authorization`` header over HTTP) and receive a short-lived,
   single-use connect ticket bound to their identity. The ticket is not
   an access token: it expires in seconds, works exactly once, and
   grants only the identity that minted it.
3. ``?token=<bearer>`` — the legacy query-string transport. Disabled by
   default; re-enable during migration with
   ``OMNIAGENTS_LEGACY_QUERY_AUTH=1`` (or ``security.legacy_query_auth:
   true`` in the spec's security config). A deprecation warning (never
   containing the token) is logged on use.

Authentication failures close the socket with :data:`WS_CLOSE_UNAUTHENTICATED`
(4401) or :data:`WS_CLOSE_FORBIDDEN` (4403). Both codes are terminal:
clients must not auto-reconnect without obtaining new credentials.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from fastapi import Depends, HTTPException, Request, WebSocket, status

from omniagents.core.providers.auth.base import AuthProvider, UserIdentity

logger = logging.getLogger(__name__)

__all__ = [
    "ANONYMOUS_USER_ID",
    "GLOBAL_MUTATING_METHODS",
    "GLOBAL_SAFE_METHODS",
    "OWNER_UNKNOWN",
    "RESOURCE_SCOPED_METHODS",
    "AuthorizationPolicy",
    "AuthenticationFailed",
    "AuthorizationDenied",
    "ConnectionAuthenticator",
    "OwnerLookupError",
    "RPC_ERROR_AUTHORIZATION_DENIED",
    "WS_CLOSE_FORBIDDEN",
    "WS_CLOSE_UNAUTHENTICATED",
    "WsTicketBroker",
    "build_ws_auth_dependency",
    "legacy_query_auth_enabled",
    "principal_can_access",
    "register_ticket_route",
]

ANONYMOUS_USER_ID = "anonymous"


class _OwnerUnknown:
    """Sentinel: the owner of a resource could not be determined.

    Distinct from ``None`` (definitively unowned). Any authorization
    decision involving it **denies** — a failed ownership lookup must
    never widen access (fail closed).
    """

    _instance: "_OwnerUnknown | None" = None

    def __new__(cls) -> "_OwnerUnknown":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "OWNER_UNKNOWN"

    def __bool__(self) -> bool:
        return False


#: See :class:`_OwnerUnknown`.
OWNER_UNKNOWN = _OwnerUnknown()


class OwnerLookupError(Exception):
    """Raised when a resource's owner cannot be resolved.

    Callers must translate this into a denial, never into access.
    """


#: WebSocket close code for failed/missing authentication. Terminal —
#: reconnecting with the same credentials will fail again.
WS_CLOSE_UNAUTHENTICATED = 4401

#: WebSocket close code for an authenticated principal that is not
#: allowed to use the requested resource (e.g. another user's terminal).
WS_CLOSE_FORBIDDEN = 4403

#: JSON-RPC error code for per-request authorization failures. The error
#: ``data.kind`` is ``"authorization_denied"``.
RPC_ERROR_AUTHORIZATION_DENIED = -32014

ADMIN_ROLE = "admin"

LEGACY_QUERY_AUTH_ENV = "OMNIAGENTS_LEGACY_QUERY_AUTH"

_TICKET_QUERY_PARAM = "ticket"
_LEGACY_TOKEN_QUERY_PARAM = "token"


class AuthenticationFailed(Exception):
    """Raised when no acceptable credential resolves to an identity."""


class AuthorizationDenied(Exception):
    """Raised when an authenticated principal may not touch a resource."""

    def __init__(
        self,
        message: str = "Not authorized",
        *,
        resource: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.resource = resource
        self.resource_id = resource_id

    def error_data(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"kind": "authorization_denied"}
        if self.resource:
            data["resource"] = self.resource
        if self.resource_id:
            data["resource_id"] = self.resource_id
        return data


def principal_can_access(
    principal: Optional[UserIdentity],
    owner_user_id: "str | None | _OwnerUnknown",
) -> bool:
    """Single ownership rule shared by every resource class. Fails closed.

    * Admin-role principals can access everything.
    * Otherwise the principal's ``user_id`` must equal the owner.
    * An unowned resource (``None``) or an unresolvable owner
      (:data:`OWNER_UNKNOWN`) is **not** accessible — an absent owner is
      never treated as consent. Deployments that identify no principals
      (``NoneAuthProvider``) short-circuit this rule in
      :class:`AuthorizationPolicy`, so single-user behavior is unchanged.
    """
    if owner_user_id is None or isinstance(owner_user_id, _OwnerUnknown):
        return False
    if principal is not None and ADMIN_ROLE in (principal.roles or []):
        return True
    user_id = principal.user_id if principal is not None else ANONYMOUS_USER_ID
    return user_id == owner_user_id


#: Methods that need no resource and are safe for any authenticated
#: principal: they are read-only, self-scoped (server-side filtered to the
#: caller's own resources), per-connection, or create a brand-new resource
#: owned by the caller.
GLOBAL_SAFE_METHODS = frozenset(
    {
        # Handshake / introspection
        "initialize",
        "initialized",
        "get_agent_info",
        "list_server_functions",
        # Self-scoped listings (filtered to the caller server-side)
        "list_sessions",
        "get_user_history",
        # Per-connection state
        "client_functions",
        "set_client_status",
        # Creates a new session owned by the caller (an existing
        # ``session_id`` makes this resource-scoped instead)
        "start_run",
        # Dispatches a registered server function. Session-scoped when it
        # carries a ``session_id``; otherwise the invoked function owns its
        # own authorization (products register these).
        "server_call",
        # Layered configuration reads: read-only, and secret fields are
        # never returned (only is_set flags), so any authenticated
        # principal may inspect the effective configuration. Dry-run
        # validation mutates nothing and echoes no submitted values.
        "get_config",
        "validate_config",
        # Model & capability catalog reads — read-only views over the
        # process model registry; results never include resolved secrets.
        # ``list_models`` becomes session-scoped automatically when the
        # optional ``session_id`` is supplied (resource resolution is by
        # parameter, like ``server_call``).
        "list_models",
        "get_model",
        "list_providers",
        # Redacted provider account/usage state. Read-only: snapshots
        # carry identity and quota data, never credential material.
        "account_status",
        "account_usage",
    }
)

#: Methods with no resource whose effect is PROCESS-WIDE — they mutate
#: shared state for every connected principal. Denied whenever the
#: deployment identifies principals (multi-user mode); allowed in
#: single-user / no-auth mode where there is only one principal.
GLOBAL_MUTATING_METHODS = frozenset(
    {
        "disable_tool",
        "disable_mcp_server",
        # Writes server-level configuration (the user overlay layer) for
        # every connected principal — same posture as disable_tool:
        # allowed in single-user mode, refused for individual principals
        # when the deployment identifies distinct users.
        "write_config",
        # Sets the process-wide realtime voice default (models.json
        # ``voice_default``) — every connected principal sees the change.
        "set_voice_model",
        # Provider account mutations rewrite credentials every session in
        # the process runs on (the codex token store, provider API-key
        # environment). Following the ``disable_tool`` precedent they are
        # refused for individual principals in enforced multi-user mode
        # and available only where there is a single (anonymous)
        # principal — the desktop/local topology they exist for.
        "account_login_start",
        "account_login_complete",
        "account_login_cancel",
        "account_logout",
        "account_refresh",
        "account_select",
    }
)

#: Methods that name a resource (``session_id`` / ``run_id`` / ``call_id`` /
#: ``request_id``) and are therefore authorized by ownership, not by this
#: list. Declared explicitly so every protocol request has a recorded
#: classification: ``test_every_protocol_request_method_is_classified``
#: fails when a new method belongs to none of the three sets, forcing the
#: authorization decision to be made deliberately.
RESOURCE_SCOPED_METHODS = frozenset(
    {
        "stop_run",
        "send_user_message",
        "get_session_history",
        "archive_session",
        "delete_session",
        "tool_approval_response",
        "mcp_approval_response",
        "client_response",
        # Elicitation answers name an ``elicitation_id`` minted for one
        # session; ownership of that session gates who may answer (same
        # posture as tool_approval_response).
        "elicitation_response",
        "enqueue_message",
        "list_queue",
        "cancel_queued_message",
        # Durable replay cursors: both name a session_id, so ownership of
        # the session gates who may read (resume) or compact (ack) its
        # journaled events.
        "resume_session",
        "ack_events",
        "fork_session",
        "set_session_hold",
        "queue_status",
        "enqueue_notification",
        "update_session_variables",
        "report_incident",
        "export_session",
        # Catalog application to one session: ownership of the named
        # session gates who may change its model / reasoning effort.
        "set_session_model",
        "set_session_reasoning",
    }
)


class AuthorizationPolicy:
    """Decides access for one deployment's authentication mode.

    ``enforced`` is True when the configured provider identifies distinct
    principals (token/OIDC/platform). When it is False — the
    ``NoneAuthProvider`` single-user default, or a token provider with no
    token configured — every connection is the same anonymous principal,
    so ownership and global-effect rules are vacuous and skipped: the
    open-source single-user experience is untouched.
    """

    def __init__(self, *, enforced: bool) -> None:
        self.enforced = enforced

    def can_access(
        self,
        principal: Optional[UserIdentity],
        owner_user_id: "str | None | _OwnerUnknown",
    ) -> bool:
        if not self.enforced:
            return True
        return principal_can_access(principal, owner_user_id)

    def can_call_resourceless(self, method: str) -> bool:
        """Whether *method* may run when it names no resource.

        Default-DENY: a method must be explicitly classified. Anything
        unclassified (including newly added methods) is refused in
        enforced mode, so new global surfaces cannot land unnoticed.
        """
        if not self.enforced:
            return True
        if method in GLOBAL_SAFE_METHODS:
            return True
        return False


def legacy_query_auth_enabled(security_config: Optional[Mapping[str, Any]]) -> bool:
    """Whether the deprecated ``?token=`` transport is accepted.

    The ``OMNIAGENTS_LEGACY_QUERY_AUTH`` environment variable wins over
    the ``security.legacy_query_auth`` config key. Default: disabled.
    """
    env = os.getenv(LEGACY_QUERY_AUTH_ENV)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if security_config and bool(security_config.get("legacy_query_auth")):
        return True
    return False


@dataclass
class _PendingTicket:
    identity: UserIdentity
    expires_at: float


class WsTicketBroker:
    """Mints and redeems short-lived, single-use WebSocket connect tickets."""

    def __init__(self, *, ttl_seconds: float = 60.0, max_pending: int = 1024) -> None:
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._pending: Dict[str, _PendingTicket] = {}

    def mint(self, identity: UserIdentity) -> Tuple[str, float]:
        """Return ``(ticket, expires_in_seconds)`` bound to *identity*."""
        self._prune()
        if len(self._pending) >= self._max_pending:
            # Drop the oldest pending ticket rather than refusing service.
            oldest = min(self._pending, key=lambda k: self._pending[k].expires_at)
            self._pending.pop(oldest, None)
        ticket = secrets.token_urlsafe(32)
        self._pending[ticket] = _PendingTicket(
            identity=identity, expires_at=time.monotonic() + self._ttl
        )
        return ticket, self._ttl

    def redeem(self, ticket: str) -> Optional[UserIdentity]:
        """Consume *ticket*; returns its identity or ``None``. Single use."""
        self._prune()
        pending = self._pending.pop(ticket, None)
        if pending is None:
            return None
        if pending.expires_at < time.monotonic():
            return None
        return pending.identity

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._pending.items() if v.expires_at < now]
        for key in expired:
            self._pending.pop(key, None)


class _HeaderOnlyRequest:
    """Request shim handed to providers when no explicit credential exists.

    Exposes headers but *hides query parameters* so provider-side query
    extraction (a legacy behavior of the token/OIDC providers) cannot
    bypass the ``legacy_query_auth`` gate.
    """

    class _EmptyParams:
        @staticmethod
        def get(_key: str, default: Any = None) -> Any:
            return default

        @staticmethod
        def multi_items() -> list:
            return []

    def __init__(self, headers: Mapping[str, str]) -> None:
        self.headers = headers
        self.query_params = self._EmptyParams()


class ConnectionAuthenticator:
    """Resolves one authenticated principal for any connection surface."""

    def __init__(
        self,
        provider: AuthProvider,
        *,
        ticket_broker: Optional[WsTicketBroker] = None,
        allow_query_token: bool = False,
    ) -> None:
        self.provider = provider
        self.ticket_broker = ticket_broker or WsTicketBroker()
        self.allow_query_token = allow_query_token
        self._legacy_warned = False

    @property
    def identifies_principals(self) -> bool:
        """Whether this deployment distinguishes users.

        False for the ``NoneAuthProvider`` default and for a
        ``TokenAuthProvider`` with no token configured — both authenticate
        every connection as the same anonymous identity, so ownership
        enforcement would have nothing to separate.
        """
        from omniagents.core.providers.auth.none import NoneAuthProvider
        from omniagents.core.providers.auth.token import TokenAuthProvider

        provider = self.provider
        if isinstance(provider, NoneAuthProvider):
            return False
        if isinstance(provider, TokenAuthProvider):
            return bool(
                getattr(provider, "_token", "") or getattr(provider, "_tokens", {})
            )
        return True

    @property
    def authorization_policy(self) -> AuthorizationPolicy:
        """The authorization policy implied by this authenticator."""
        return AuthorizationPolicy(enforced=self.identifies_principals)

    # ------------------------------------------------------------------
    # Construction from an AgentSpec
    # ------------------------------------------------------------------
    @classmethod
    def from_spec(
        cls, spec: Any, *, auth_token: Optional[str] = None
    ) -> "ConnectionAuthenticator":
        """Build the process-wide authenticator for *spec*.

        Provider resolution order:

        1. ``security.providers.auth`` from the spec (``token``/``oidc``/
           ``platform``/custom), when configured and not ``none``.
        2. A single-token :class:`TokenAuthProvider` when the legacy
           ``auth_token`` argument was supplied to ``build_app``.
        3. :class:`NoneAuthProvider` (anonymous single-user mode).
        """
        from omniagents.core.providers import resolve_provider
        from omniagents.core.providers.auth.none import NoneAuthProvider
        from omniagents.core.providers.auth.token import TokenAuthProvider

        security = getattr(spec, "security_config", None) or {}
        provider: Optional[AuthProvider] = None
        auth_cfg = (security.get("providers") or {}).get("auth")
        if auth_cfg and auth_cfg.get("type", "none") != "none":
            provider = resolve_provider("auth", auth_cfg)
        if provider is None and auth_token:
            provider = TokenAuthProvider({"token": auth_token})
        if provider is None:
            provider = NoneAuthProvider()
        return cls(
            provider,
            allow_query_token=legacy_query_auth_enabled(security),
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    async def authenticate_ws(self, websocket: WebSocket) -> UserIdentity:
        """Authenticate a WebSocket upgrade request.

        Raises :class:`AuthenticationFailed` when no credential resolves.
        """
        return await self._authenticate(
            headers=websocket.headers, query_params=websocket.query_params
        )

    async def authenticate_http(self, request: Request) -> UserIdentity:
        """Authenticate an HTTP request (used by ``/auth/ws-ticket``)."""
        return await self._authenticate(headers=request.headers, query_params=None)

    async def _authenticate(
        self, *, headers: Mapping[str, str], query_params: Any
    ) -> UserIdentity:
        # 1. Authorization header (native clients, proxies).
        auth_header = headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            return await self._authenticate_token(auth_header[7:])

        # 2. One-time connect ticket (browser WebSocket clients).
        if query_params is not None:
            ticket = query_params.get(_TICKET_QUERY_PARAM)
            if ticket:
                identity = self.ticket_broker.redeem(ticket)
                if identity is None:
                    raise AuthenticationFailed("Invalid or expired connect ticket")
                return identity

        # 3. Legacy query-string bearer token (deprecated, opt-in).
        if query_params is not None:
            legacy_token = query_params.get(_LEGACY_TOKEN_QUERY_PARAM)
            if legacy_token:
                if not self.allow_query_token:
                    raise AuthenticationFailed(
                        "Query-string tokens are disabled; use the Authorization "
                        "header or a /auth/ws-ticket connect ticket "
                        f"(set {LEGACY_QUERY_AUTH_ENV}=1 during migration)"
                    )
                if not self._legacy_warned:
                    self._legacy_warned = True
                    logger.warning(
                        "Deprecated query-string token authentication was used; "
                        "migrate clients to Authorization headers or "
                        "/auth/ws-ticket connect tickets."
                    )
                return await self._authenticate_token(legacy_token)

        # 4. No explicit credential — let the provider decide (NoneAuth
        # authenticates anonymously; token/OIDC providers reject).
        result = await self.provider.authenticate(_HeaderOnlyRequest(headers))
        if not result.authenticated or result.identity is None:
            raise AuthenticationFailed(result.error or "Authentication required")
        return result.identity

    async def _authenticate_token(self, token: str) -> UserIdentity:
        result = await self.provider.authenticate(token)
        if not result.authenticated or result.identity is None:
            raise AuthenticationFailed(result.error or "Invalid credentials")
        return result.identity


def build_ws_auth_dependency(authenticator: ConnectionAuthenticator):
    """FastAPI dependency shared by every WebSocket route.

    Stores the resolved principal on ``websocket.state.principal``; closes
    the socket with 4401 and raises 403 when authentication fails.
    """

    async def _authenticate(websocket: WebSocket) -> None:
        try:
            identity = await authenticator.authenticate_ws(websocket)
        except AuthenticationFailed as exc:
            await websocket.close(code=WS_CLOSE_UNAUTHENTICATED, reason=str(exc)[:120])
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        websocket.state.principal = identity

    return Depends(_authenticate)


def register_ticket_route(app: Any, authenticator: ConnectionAuthenticator) -> None:
    """Register ``POST /auth/ws-ticket``.

    Browser clients authenticate this HTTP call with the ``Authorization``
    header, then dial the WebSocket with the returned one-time ticket —
    the launcher's fetch-token-then-dial pattern. The response never
    echoes the caller's access token.
    """

    async def _issue_ticket(request: Request) -> Dict[str, Any]:
        try:
            identity = await authenticator.authenticate_http(request)
        except AuthenticationFailed as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        ticket, expires_in = authenticator.ticket_broker.mint(identity)
        return {"ticket": ticket, "expires_in": expires_in}

    app.add_api_route("/auth/ws-ticket", _issue_ticket, methods=["POST"])
