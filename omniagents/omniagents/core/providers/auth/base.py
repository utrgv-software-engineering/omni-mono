"""Base authentication provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UserIdentity:
    """Represents an authenticated user."""

    user_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    claims: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthResult:
    """Result of an authentication attempt."""

    authenticated: bool
    identity: Optional[UserIdentity] = None
    error: Optional[str] = None
    #: True when the refusal is a *transient capacity* condition rather
    #: than a credential problem. Callers surface these as a retryable
    #: close (1013 / HTTP 503) instead of the terminal 4401 clients are
    #: required never to retry — a short-lived capacity condition must
    #: not permanently disconnect well-behaved clients.
    retryable: bool = False


class AuthProvider(ABC):
    """Base class for authentication providers.

    Implementations must handle:
    - Authenticating incoming requests (WebSocket or HTTP)
    - Extracting user identity from valid credentials

    Four **declared capabilities** drive security decisions elsewhere.
    They are attributes a provider states, deliberately not something
    inferred from its type: a provider that resolves a distinct principal
    per connection (a gateway-header provider in front of Azure EasyAuth
    or oauth2-proxy, say) must be able to say so whatever it subclasses.
    Each defaults to the safe answer for an unknown provider.

    ``identifies_principals`` and ``enforces_authorization`` are
    deliberately separate booleans. They coincide for most providers, but
    a single shared bearer token is exactly the case where they diverge:
    one principal (so the proxy must not claim per-user propagation) on a
    credentialed server (so the authorization rules must still apply).
    """

    #: Whether this provider resolves *distinct* principals — i.e. two
    #: different callers can end up with different ``user_id``s. This is
    #: what the serve proxy needs to know before claiming it can
    #: propagate per-user identity upstream. A single shared bearer token
    #: authenticates many humans as one ``user_id``, so it is False.
    #: Defaults to True: a provider that has not thought about it is
    #: treated as multi-user, which fails closed.
    identifies_principals: bool = True

    #: Whether ownership and global-effect authorization rules apply.
    #: Deliberately **separate** from ``identifies_principals``: a single
    #: shared token identifies one principal (so per-user propagation is
    #: a lie) but is still a *deployed, credentialed* server where
    #: process-wide mutations like ``disable_tool`` should be refused and
    #: unclassified methods should stay default-denied. Only a genuinely
    #: unauthenticated single-user deployment sets this False.
    enforces_authorization: bool = True

    #: Whether ``POST /auth/ws-ticket`` should be offered. Browsers need
    #: it because they cannot set WebSocket headers; surfaces reached
    #: only by server-to-server credentials should decline it rather than
    #: mint a query-string credential nothing will use.
    supports_connect_tickets: bool = True

    #: Whether this provider may receive bearer credentials through the
    #: deprecated ``?token=`` WebSocket query parameter. Server-to-server
    #: credentials such as proxy identity assertions must remain header-only,
    #: even when an operator temporarily enables legacy query authentication
    #: for ordinary client bearer tokens.
    supports_legacy_query_auth: bool = True

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    async def authenticate(self, request: Any) -> AuthResult:
        """Authenticate a request and return the result.

        *request* is backend-specific (e.g., a FastAPI WebSocket or
        HTTP request object).
        """
        ...

    @abstractmethod
    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        """Extract user identity from an already-authenticated request.

        Returns ``None`` if the request is not authenticated.
        """
        ...
