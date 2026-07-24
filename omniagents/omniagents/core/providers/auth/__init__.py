"""Authentication provider domain."""

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity
from omniagents.core.providers.auth.none import NoneAuthProvider
from omniagents.core.providers.auth.token import TokenAuthProvider
from omniagents.core.providers import register_provider

register_provider("auth", "none", NoneAuthProvider)
register_provider("auth", "token", TokenAuthProvider)

# OIDC provider — only registered when PyJWT is installed
try:
    from omniagents.core.providers.auth.oidc import OIDCAuthProvider

    register_provider("auth", "oidc", OIDCAuthProvider)
except ImportError:
    pass

__all__ = [
    "AuthProvider",
    "AuthResult",
    "UserIdentity",
    "NoneAuthProvider",
    "TokenAuthProvider",
]
