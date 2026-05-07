"""Factory for SDK-native session backends.

OmniAgents ships its own multi-tenant SQLite session storage in
:mod:`omniagents.core.session.history_db` and
:class:`omniagents.core.session.manager.Session`, which today owns conversation
history end-to-end (UI history + model context). This module is purely about
*opting in* to one of the SDK's :class:`agents.memory.session.Session`
implementations as an *additional* model-context store, so users can take
advantage of Redis/Mongo/Postgres-backed conversations, OpenAI Conversations,
or transparent encryption.

The factory dispatches on a ``type`` string from a YAML block::

    session:
      backend:
        type: redis
        url: redis://localhost:6379/0
        session_id: user-123

The default — when ``session.backend`` is missing or ``type: sqlite`` — is to
return ``None`` so the existing OmniAgents SQLite store handles everything.

Each non-default backend pulls in its own pip extra:

* ``sqlalchemy`` -> ``omniagents[session-sqlalchemy]``
  (``openai-agents[sqlalchemy]``)
* ``redis`` -> ``omniagents[session-redis]`` (``openai-agents[redis]``)
* ``mongodb`` -> ``omniagents[session-mongodb]`` (``openai-agents[mongodb]``)
* ``encrypted`` -> ``omniagents[session-encrypted]``
  (``openai-agents[encrypt]``)
* ``openai_conversations`` -> no extra; uses the OpenAI client already required
  by the core install.

Wiring of the resolved backend into ``Runner.run_streamed(session=...)`` is
intentionally deferred. OmniAgents currently feeds full session history into
the runner as ``input``; passing an SDK-managed session in addition would
double-store. The plan is to revisit this once ``Runner.run(session=...)``
fully replaces the manual history feed (Phase 3 of the upgrade plan). Until
then, ``build_session_backend`` just resolves and returns the SDK ``Session``
instance and stores it on the spec; downstream consumers may opt to use it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _missing_extra_message(extra: str, backend_type: str) -> str:
    return (
        f"Session backend '{backend_type}' requires the '{extra}' extra. "
        f"Install it with: pip install omniagents[{extra}]"
    )


def _build_openai_conversations(config: Dict[str, Any]) -> Optional[Any]:
    """Build an OpenAIConversationsSession.

    YAML keys:
        conversation_id (optional): Existing conversation ID. If omitted, a new
            conversation is created lazily on first access.
    """
    try:
        from agents.memory.openai_conversations_session import (
            OpenAIConversationsSession,
        )
    except ImportError as e:
        logger.warning(
            "Failed to import OpenAIConversationsSession from agents SDK: %s", e
        )
        return None

    conversation_id = config.get("conversation_id")
    return OpenAIConversationsSession(conversation_id=conversation_id)


def _build_sqlalchemy(config: Dict[str, Any]) -> Optional[Any]:
    """Build a SQLAlchemySession.

    YAML keys:
        session_id (required): Conversation ID.
        url (required): Async SQLAlchemy URL, e.g.
            ``postgresql+asyncpg://user:pass@host/db``.
        create_tables (optional, default False): Auto-create tables.
        sessions_table / messages_table (optional): Override default names.
    """
    try:
        from agents.extensions.memory import SQLAlchemySession
    except ImportError as e:
        raise ImportError(
            _missing_extra_message("session-sqlalchemy", "sqlalchemy")
        ) from e

    session_id = config.get("session_id")
    url = config.get("url")
    if not session_id or not url:
        logger.warning(
            "Session backend 'sqlalchemy' requires both 'session_id' and 'url'."
        )
        return None

    extra_kwargs: Dict[str, Any] = {}
    for key in ("create_tables", "sessions_table", "messages_table"):
        if key in config:
            extra_kwargs[key] = config[key]

    engine_kwargs = config.get("engine_kwargs") or None
    return SQLAlchemySession.from_url(
        session_id,
        url=url,
        engine_kwargs=engine_kwargs,
        **extra_kwargs,
    )


def _build_redis(config: Dict[str, Any]) -> Optional[Any]:
    """Build a RedisSession.

    YAML keys:
        session_id (required): Conversation ID.
        url (required): Redis URL, e.g. ``redis://localhost:6379/0``.
        key_prefix (optional): Defaults to ``agents:session``.
        ttl (optional): TTL in seconds.
    """
    try:
        from agents.extensions.memory import RedisSession
    except ImportError as e:
        raise ImportError(_missing_extra_message("session-redis", "redis")) from e

    session_id = config.get("session_id")
    url = config.get("url")
    if not session_id or not url:
        logger.warning("Session backend 'redis' requires both 'session_id' and 'url'.")
        return None

    extra_kwargs: Dict[str, Any] = {}
    for key in ("key_prefix", "ttl"):
        if key in config:
            extra_kwargs[key] = config[key]

    return RedisSession.from_url(session_id, url=url, **extra_kwargs)


def _build_mongodb(config: Dict[str, Any]) -> Optional[Any]:
    """Build a MongoDBSession.

    YAML keys:
        session_id (required): Conversation ID.
        uri (required): MongoDB URI, e.g. ``mongodb://localhost:27017``.
        database (optional): Database name (default ``agents``).
        sessions_collection / messages_collection (optional).
    """
    try:
        from agents.extensions.memory import MongoDBSession
    except ImportError as e:
        raise ImportError(_missing_extra_message("session-mongodb", "mongodb")) from e

    session_id = config.get("session_id")
    uri = config.get("uri")
    if not session_id or not uri:
        logger.warning(
            "Session backend 'mongodb' requires both 'session_id' and 'uri'."
        )
        return None

    extra_kwargs: Dict[str, Any] = {}
    for key in ("database", "sessions_collection", "messages_collection"):
        if key in config:
            extra_kwargs[key] = config[key]

    return MongoDBSession.from_uri(session_id, uri=uri, **extra_kwargs)


def _build_encrypted(config: Dict[str, Any]) -> Optional[Any]:
    """Build an EncryptedSession that wraps another backend.

    YAML keys:
        session_id (required): Conversation ID (also used as HKDF salt).
        key (required): Encryption master key (Fernet key or raw secret).
            Use ``${ENV_VAR}`` substitution in YAML.
        ttl (optional): Token TTL in seconds (default 600).
        inner (required): Nested ``backend`` block describing the wrapped
            session, e.g. a ``redis`` or ``sqlalchemy`` backend.
    """
    try:
        from agents.extensions.memory import EncryptedSession
    except ImportError as e:
        raise ImportError(
            _missing_extra_message("session-encrypted", "encrypted")
        ) from e

    session_id = config.get("session_id")
    key = config.get("key") or config.get("encryption_key")
    inner_cfg = config.get("inner")
    if not session_id or not key or not isinstance(inner_cfg, dict):
        logger.warning(
            "Session backend 'encrypted' requires 'session_id', 'key', and "
            "an 'inner' backend block."
        )
        return None

    inner_session = build_session_backend(inner_cfg)
    if inner_session is None:
        logger.warning(
            "Session backend 'encrypted' could not resolve its inner backend; "
            "encryption wrapper will not be applied."
        )
        return None

    ttl = config.get("ttl", 600)
    return EncryptedSession(
        session_id=session_id,
        underlying_session=inner_session,
        encryption_key=key,
        ttl=ttl,
    )


_BUILDERS = {
    "openai_conversations": _build_openai_conversations,
    "sqlalchemy": _build_sqlalchemy,
    "redis": _build_redis,
    "mongodb": _build_mongodb,
    "encrypted": _build_encrypted,
}


def build_session_backend(config: Optional[Dict[str, Any]]) -> Optional[Any]:
    """Resolve a YAML ``session.backend`` block to an SDK ``Session`` instance.

    Returns ``None`` when:

    * ``config`` is ``None`` or empty,
    * ``type`` is ``sqlite`` (the OmniAgents default takes over),
    * ``type`` is unknown (a warning is logged),
    * a required field is missing (a warning is logged),
    * the SDK extras for the chosen backend are not installed (an
      ``ImportError`` is raised with installation guidance).

    Args:
        config: The contents of ``session.backend`` from YAML. Backend-specific
            kwargs sit alongside ``type`` (e.g. ``url``, ``session_id``,
            ``host``).

    Returns:
        An SDK ``Session`` instance, or ``None`` to indicate that the default
        OmniAgents SQLite store should be used.
    """
    if not config or not isinstance(config, dict):
        return None

    backend_type = config.get("type")
    if not backend_type or backend_type == "sqlite":
        # Default: OmniAgents SQLite session storage handles things.
        return None

    builder = _BUILDERS.get(backend_type)
    if builder is None:
        logger.warning(
            "Unknown session backend type %r; falling back to default OmniAgents "
            "SQLite storage. Supported types: %s",
            backend_type,
            ", ".join(sorted(["sqlite", *_BUILDERS.keys()])),
        )
        return None

    return builder(config)
