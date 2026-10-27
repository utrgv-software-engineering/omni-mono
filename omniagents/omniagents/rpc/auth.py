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
   containing the token) is logged on use. Header-only provider credentials,
   including proxy identity assertions, are never accepted this way.

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

from fastapi import (
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketException,
    status,
)

from omniagents.core.providers.auth.base import AuthProvider, UserIdentity

logger = logging.getLogger(__name__)

__all__ = [
    "ANONYMOUS_USER_ID",
    "AGENT_HOST_ADMIN_METHODS",
    "GLOBAL_MUTATING_METHODS",
    "GLOBAL_SAFE_METHODS",
    "OWNER_UNKNOWN",
    "RESOURCE_SCOPED_METHODS",
    "AuthorizationPolicy",
    "AuthenticationFailed",
    "AuthenticationUnavailable",
    "AuthorizationDenied",
    "ConnectionAuthenticator",
    "OwnerLookupError",
    "RPC_ERROR_AUTHORIZATION_DENIED",
    "WS_CLOSE_FORBIDDEN",
    "WS_CLOSE_TRY_AGAIN_LATER",
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

#: WebSocket close code for a transient capacity refusal. Unlike
#: 4401/4403 this is explicitly **retryable**: clients should back off
#: and reconnect.
WS_CLOSE_TRY_AGAIN_LATER = 1013

#: JSON-RPC error code for per-request authorization failures. The error
#: ``data.kind`` is ``"authorization_denied"``.
RPC_ERROR_AUTHORIZATION_DENIED = -32014

ADMIN_ROLE = "admin"

AGENT_HOST_ADMIN_METHODS = frozenset(
    {
        "agent_host_register_workspace",
        "agent_host_register_profile",
    }
)

LEGACY_QUERY_AUTH_ENV = "OMNIAGENTS_LEGACY_QUERY_AUTH"

_TICKET_QUERY_PARAM = "ticket"
_LEGACY_TOKEN_QUERY_PARAM = "token"


class AuthenticationFailed(Exception):
    """Raised when no acceptable credential resolves to an identity."""


class AuthenticationUnavailable(AuthenticationFailed):
    """Raised when authentication could not be *attempted* right now.

    A transient capacity condition, not a credential problem. Surfaced as
    a retryable close (:data:`WS_CLOSE_TRY_AGAIN_LATER` / HTTP 503)
    rather than the terminal 4401, which clients are required never to
    retry — reporting a 30-second capacity condition as terminal would
    permanently disconnect every well-behaved client.
    """


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


#: Server functions whose whole job is to bring a session into existence,
#: dispatched as ``server_call {function: ...}``. Naming a session that does
#: not exist yet is the point of the call, so the ownership check treats it
#: as a creation (the handler stamps the caller as owner) instead of a
#: refused read of an "unowned" resource.
#:
#: Deliberately an allowlist and deliberately tiny. Most session-naming
#: methods must keep failing closed on an unknown id — ``resume_session``
#: in particular, whose ``get_or_create`` would re-materialize a deleted
#: session on every reconnect.
SESSION_CREATING_SERVER_FUNCTIONS = frozenset({"session.ensure"})


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
        # Conversation listing + full-text search (tkt_eZldW-Gje6kn).
        # Safe for the SAME reason ``list_sessions`` is, and only for that
        # reason: the caller's ``user_id`` is part of the SQL predicate the
        # page and its ``total`` are drawn under, so a principal can never
        # be handed — or shown a search preview of — a thread they do not
        # own. Filtering a page in Python after the database applied LIMIT
        # would NOT be sufficient (it under-delivers, the defect review
        # found in list_sessions), which is why the scope lives in
        # ``ThreadFilter`` and the store methods take the whole filter.
        # ``ThreadFilter.user_id`` has no default and "every principal" is
        # an explicit UNSCOPED sentinel resolved in one constructor, so an
        # omitted scope is a type error rather than a silently unscoped
        # listing (the OWNER_UNKNOWN posture, on the query side).
        # A thread NAMED by id is resource-scoped instead, automatically:
        # ``thread_id`` resolution in _resolve_rpc_resource_owner wins over
        # this list.
        "list_threads",
        "search_threads",
        "agent_host_list_resources",
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
        # MCP management reads: configured-server listings, per-server
        # snapshots/probes, and sessionless resource/prompt reads. All
        # redacted (write-only header/env secrets — the layered-config
        # model) and read-only from the configuration's point of view.
        # ``mcp_list_servers`` / ``mcp_read_resource`` / ``mcp_get_prompt``
        # become session-scoped automatically when the optional
        # ``session_id`` is supplied (resource resolution is by
        # parameter, like ``list_models``).
        "mcp_list_servers",
        "mcp_get_server",
        "mcp_read_resource",
        "mcp_get_prompt",
    }
)

#: Methods with no resource whose effect is PROCESS-WIDE — they mutate
#: shared state for every connected principal. Denied whenever the
#: deployment identifies principals (multi-user mode); allowed in
#: single-user / no-auth mode where there is only one principal.
GLOBAL_MUTATING_METHODS = frozenset(
    {
        "agent_host_register_workspace",
        "agent_host_register_profile",
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
        # MCP server-config mutations rewrite the process-wide server
        # registry (spec + the user-layer mcp.json every session builds
        # from) — the ``disable_mcp_server``/``write_config`` precedent.
        "mcp_create_server",
        "mcp_update_server",
        "mcp_delete_server",
        "mcp_reload_server",
        # MCP OAuth flows store credentials every session's server
        # connections run on — the ``account_login_*`` precedent.
        # ``mcp_auth_start`` additionally guards its optional
        # ``session_id`` (elicitation delivery) in the handler so the
        # parameter cannot re-scope the request in enforced mode.
        "mcp_auth_start",
        "mcp_auth_complete",
        "mcp_auth_cancel",
        # Retention purge (tkt_eZldW-Gje6kn). The retention window is a
        # deployment-wide policy, and applying it deletes conversations
        # belonging to every principal in the process — irreversibly. That
        # is the ``write_config``/``disable_tool`` shape exactly: refused
        # for individual principals in enforced multi-user mode, available
        # in the single-principal desktop/local topology it exists for.
        # A principal who wants to remove their OWN conversation uses
        # ``delete_session``, which is ownership-gated.
        "purge_threads",
    }
)

#: Methods that name a resource (``thread_id`` / ``run_id`` /
#: ``environment_id`` / ``call_id`` / ``request_id``) and are therefore
#: authorized by ownership, not by this
#: list. Declared explicitly so every protocol request has a recorded
#: classification: ``test_every_protocol_request_method_is_classified``
#: fails when a new method belongs to none of the three sets, forcing the
#: authorization decision to be made deliberately.
RESOURCE_SCOPED_METHODS = frozenset(
    {
        "agent_host_bind_thread",
        "agent_host_materialize_environment",
        "agent_host_stop_environment",
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
        # Approval reviewer toggle: session ownership gates who reroutes
        # that session's approvals to the guardian.
        "set_session_approvals",
        # Workflow completion reviewer toggle: same posture — session
        # ownership gates who reroutes that session's plan-completion
        # reviews.
        "set_session_workflow",
        # Out-of-band MCP tool calls act on behalf of one session
        # (MCP-Apps postMessage routing): ``session_id`` is required and
        # its ownership authorizes the call.
        "mcp_call_tool",
        # Workspace filesystem watch & transfer (tkt_B5fXOZHf08K1): every
        # fs_* method names an environment_id. The environment resolves to
        # its durable Workspace owner before any filesystem access.
        # Watch/transfer ids are additionally bound to the creating
        # connection inside FilesystemManager.
        "fs_watch",
        "fs_unwatch",
        "fs_list",
        "fs_stat",
        "fs_download_open",
        "fs_download_read",
        "fs_download_close",
        "fs_upload_open",
        "fs_upload_chunk",
        "fs_upload_commit",
        "fs_upload_abort",
        "fs_delete",
        "fs_rename",
        "fs_mkdir",
        # Canonical conversation domain objects (tkt_a-QF2Oc4OAsD). A
        # thread IS a session (``thread_id == session_id``) and its turns
        # and items are that session's transcript — the most
        # privacy-sensitive data the protocol carries. Every method names a
        # ``thread_id``, so ownership of the session gates the read and a
        # cross-user attempt is refused with -32014. There is deliberately
        # no globally-safe listing counterpart: ``list_sessions`` already
        # covers self-scoped discovery, and adding a second global surface
        # over transcripts would widen the blast radius for nothing.
        "get_thread",
        "list_turns",
        "list_items",
        "get_item",
        # Conversation organization & branching (tkt_eZldW-Gje6kn). Each
        # names a ``thread_id`` and inherits the same session-ownership
        # gate as the reads above — renaming, archiving, exporting or
        # walking the fork tree of another principal's conversation is
        # refused with -32014 before the store is touched. ``export_thread``
        # in particular returns the whole transcript, so it is exactly as
        # sensitive as ``list_items``.
        "update_thread",
        "list_thread_descendants",
        "export_thread",
        # Durable plans & aggregated run diffs (tkt_FqUypyxF2tzC). A plan
        # is the agent's working state for one conversation and a run diff
        # is the literal content of that conversation's file edits — both
        # are at least as sensitive as the transcript they belong to. Each
        # method names a ``thread_id`` (== session_id), so ownership of the
        # session gates the read and a cross-user attempt is refused with
        # -32014 before any store lookup. There is no plan/diff WRITE on
        # this surface at all: plans are mutated by the agent's own tools
        # inside its run, and run diffs by the workspace observer, so no
        # client can author either.
        "get_plan",
        "get_run_diff",
        # Typed git operations (tkt_l_l0lUi4A5V2): every git_* method names
        # an environment_id. The environment resolves to its durable
        # Workspace owner before any process starts. Confirmation tokens are
        # additionally bound to that environment inside ConfirmationBroker.
        "git_list_repositories",
        "git_status",
        "git_diff",
        "git_log",
        "git_list_branches",
        "git_list_worktrees",
        "git_conflicts",
        "git_stage",
        "git_unstage",
        "git_discard",
        "git_commit",
        "git_checkout",
        "git_reset",
        "git_fetch",
        "git_pull",
        "git_push",
        # Repository-status watches follow the same environment-ownership
        # gate; watch ids are additionally bound to the creating connection
        # inside GitManager.
        "git_status_watch",
        "git_status_unwatch",
    }
)


class AuthorizationPolicy:
    """Decides access for one deployment's authentication mode.

    ``enforced`` is True whenever the deployment is credentialed at all
    (token/OIDC/platform/proxy-assertion), which includes a single shared
    token: ownership is vacuous there — everyone is the same principal,
    so ``can_access`` passes anyway — but process-wide mutators stay
    refused and unclassified methods stay default-denied.

    It is False only for the ``NoneAuthProvider`` default, the genuinely
    unauthenticated single-user topology, so the open-source local
    experience is untouched.

    This is deliberately NOT the same question as
    :attr:`ConnectionAuthenticator.identifies_principals`, which asks
    whether two callers can get *different* ``user_id``s — the thing the
    serve proxy needs before it claims to propagate identity upstream.
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

        Reads the provider's **declared** capability
        (:attr:`~omniagents.core.providers.auth.base.AuthProvider.identifies_principals`),
        defaulting to True for anything that does not declare one — an
        unknown provider is assumed multi-user, which fails closed.

        This is deliberately not an ``isinstance`` test. Inferring the
        answer from the class made the capability undeclarable: a
        provider that resolves a distinct principal per connection but
        happens to derive from ``NoneAuthProvider`` (the gateway-header
        shape used in front of Azure EasyAuth / oauth2-proxy) was read as
        single-user, which silently disabled ownership enforcement and
        let the serve proxy collapse every user onto one upstream
        identity.
        """
        return bool(getattr(self.provider, "identifies_principals", True))

    @property
    def supports_connect_tickets(self) -> bool:
        """Whether ``POST /auth/ws-ticket`` should be registered."""
        return bool(getattr(self.provider, "supports_connect_tickets", True))

    @property
    def enforces_authorization(self) -> bool:
        """Whether ownership / global-effect rules apply.

        Separate from :attr:`identifies_principals` on purpose. A single
        shared bearer token identifies exactly one principal — so the
        proxy must not claim it can propagate per-user identity — yet it
        is still a credentialed deployment where ``disable_tool`` and
        friends should be refused and unclassified methods should stay
        default-denied. Collapsing the two would have silently traded
        that control away to fix the proxy claim.
        """
        return bool(getattr(self.provider, "enforces_authorization", True))

    @property
    def authorization_policy(self) -> AuthorizationPolicy:
        """The authorization policy implied by this authenticator."""
        return AuthorizationPolicy(enforced=self.enforces_authorization)

    # ------------------------------------------------------------------
    # Construction from an AgentSpec
    # ------------------------------------------------------------------
    @classmethod
    def from_spec(
        cls,
        spec: Any,
        *,
        auth_token: Optional[str] = None,
        agent_host_control_token: Optional[str] = None,
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
        if provider is not None and agent_host_control_token:
            raise ValueError(
                "agent_host_control_token cannot be combined with a configured "
                "auth provider; add an admin-role credential to that provider"
            )
        if auth_token and agent_host_control_token:
            if auth_token == agent_host_control_token:
                raise ValueError(
                    "agent_host_control_token must differ from the client auth token"
                )
            provider = TokenAuthProvider(
                {
                    "tokens": {
                        auth_token: "token_user",
                        agent_host_control_token: {
                            "user_id": "agent_host_control",
                            "roles": [ADMIN_ROLE],
                        },
                    }
                }
            )
        elif provider is None and auth_token:
            provider = TokenAuthProvider({"token": auth_token})
        elif provider is None and agent_host_control_token:
            provider = TokenAuthProvider(
                {
                    "tokens": {
                        agent_host_control_token: {
                            "user_id": "agent_host_control",
                            "roles": [ADMIN_ROLE],
                        }
                    }
                }
            )
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

    async def authenticate_token(self, token: str) -> UserIdentity:
        """The principal a bearer *token* authenticates as: the server's own,
        for work it starts itself (an integration's runs)."""
        return await self._authenticate_token(token)

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
                if not bool(getattr(self.provider, "supports_legacy_query_auth", True)):
                    raise AuthenticationFailed(
                        "This authentication provider requires Authorization headers"
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
        return self._identity_or_raise(result, "Authentication required")

    async def _authenticate_token(self, token: str) -> UserIdentity:
        # ASGI decodes request headers as latin-1, so an anonymous client
        # can put any byte in ``Authorization``. Bearer credentials are
        # ASCII by definition (RFC 6750 token68), and the comparisons
        # providers run on them are not: ``hmac.compare_digest`` raises
        # TypeError on a non-ASCII str and ``str.encode("ascii")`` raises
        # UnicodeEncodeError. Neither is an AuthenticationFailed, so both
        # escape every handler and surface as an unauthenticated HTTP 500.
        #
        # The guard lives here, at the single point every credential
        # transport funnels through, rather than in each provider — one
        # audited provider does not make the class of bug go away.
        if not isinstance(token, str) or not token.isascii():
            raise AuthenticationFailed("Malformed credential")
        result = await self.provider.authenticate(token)
        return self._identity_or_raise(result, "Invalid credentials")

    @staticmethod
    def _identity_or_raise(result: Any, default_error: str) -> UserIdentity:
        """Translate an :class:`AuthResult` into an identity or an error."""
        if result.authenticated and result.identity is not None:
            return result.identity
        message = result.error or default_error
        if getattr(result, "retryable", False):
            raise AuthenticationUnavailable(message)
        raise AuthenticationFailed(message)


def build_ws_auth_dependency(authenticator: ConnectionAuthenticator):
    """FastAPI dependency shared by every WebSocket route.

    Stores the resolved principal on ``websocket.state.principal``. Terminal
    authentication failures raise one WebSocket exception with code 4401 so
    Starlette owns the close; closing first and then raising an HTTP exception
    attempts a second ASGI response and produces an unhandled RuntimeError.
    """

    async def _authenticate(websocket: WebSocket) -> None:
        try:
            identity = await authenticator.authenticate_ws(websocket)
        except AuthenticationUnavailable as exc:
            # Retryable: the client SHOULD back off and reconnect, unlike
            # 4401. Must be caught before AuthenticationFailed (its base).
            #
            # Deliberately does NOT call ``websocket.close()`` first. A
            # close before ``accept()`` is rendered by Starlette as an
            # HTTP 403 upgrade rejection, which erases the distinction —
            # the close code never reaches the wire on a handshake that
            # never completed. Raising alone yields a genuine HTTP 503,
            # which a direct client can tell apart from 403 and which the
            # serve proxy maps to a retryable 1013 close for its own
            # client (``_upstream_handshake_close_code``).
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except AuthenticationFailed as exc:
            raise WebSocketException(
                code=WS_CLOSE_UNAUTHENTICATED, reason=str(exc)[:120]
            ) from None
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
        except AuthenticationUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except AuthenticationFailed as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        ticket, expires_in = authenticator.ticket_broker.mint(identity)
        return {"ticket": ticket, "expires_in": expires_in}

    app.add_api_route("/auth/ws-ticket", _issue_ticket, methods=["POST"])
