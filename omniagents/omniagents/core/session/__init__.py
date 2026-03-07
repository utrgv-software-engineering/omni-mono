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
from .manager import Session, SessionManager
from .query import (
    count_sessions,
    ensure_sessions_schema,
    parse_datetime_arg,
    populate_session_stats,
    query_sessions,
    search_session_messages,
)
from .cli import add_sessions_subparser, handle_sessions_command

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
    "parse_datetime_arg",
    "ensure_sessions_schema",
    "query_sessions",
    "count_sessions",
    "populate_session_stats",
    "search_session_messages",
    "add_sessions_subparser",
    "handle_sessions_command",
    # Session management
    "Session",
    "SessionManager",
]
