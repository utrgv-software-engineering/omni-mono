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


class AuthProvider(ABC):
    """Base class for authentication providers.

    Implementations must handle:
    - Authenticating incoming requests (WebSocket or HTTP)
    - Extracting user identity from valid credentials
    """

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
