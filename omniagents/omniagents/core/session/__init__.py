"""Unified session management for OmniAgents."""

from .base import SessionBase, SessionStorageBase
from .history_db import (
    load_history,
    append_message,
    register_session,
    set_archived,
    get_archived,
    list_sessions,
)
from .manager import Session, SessionManager, sm

__all__ = [
    # Base classes
    "SessionBase",
    "SessionStorageBase",
    # Database functions
    "load_history",
    "append_message",
    # Advanced session features
    "register_session",
    "set_archived", 
    "get_archived",
    "list_sessions",
    # Session management
    "Session",
    "SessionManager",
    "sm",
]