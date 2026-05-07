"""SDK-native compaction wrapping for OmniAgents sessions.

This thin helper wraps an OmniAgents :class:`~omniagents.core.session.manager.Session`
with the SDK's :class:`~agents.memory.OpenAIResponsesCompactionSession` so the
OpenAI Responses ``compact`` endpoint manages context-window pressure instead
of the legacy custom heuristics in :mod:`omniagents.core.runtime.compaction`.

When compaction is disabled (``config`` empty/falsy or ``enabled`` flag false),
the original session is returned unchanged so existing call sites stay
backwards-compatible.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("omniagents.session.compaction")


def wrap_with_compaction(session: Any, config: Optional[Dict[str, Any]]) -> Any:
    """Wrap *session* with ``OpenAIResponsesCompactionSession`` if enabled.

    *config* mirrors the YAML shape::

        session:
          compaction:
            enabled: true
            model: gpt-4.1
            compaction_mode: auto

    All keys other than ``enabled`` are forwarded as kwargs to
    :class:`OpenAIResponsesCompactionSession`. Returns the original session
    unchanged when compaction is disabled or when wrapping fails.
    """

    if not config or not isinstance(config, dict):
        return session
    if not config.get("enabled", False):
        return session

    try:
        from agents.memory import OpenAIResponsesCompactionSession
    except Exception as exc:  # pragma: no cover - SDK should always be present
        logger.warning(
            "Failed to import OpenAIResponsesCompactionSession; compaction disabled: %s",
            exc,
        )
        return session

    kwargs: Dict[str, Any] = {k: v for k, v in config.items() if k not in {"enabled"}}

    session_id = getattr(session, "session_id", None) or getattr(session, "id", None)
    if not session_id:
        logger.warning("Session has no session_id/id; compaction wrapping skipped.")
        return session

    try:
        return OpenAIResponsesCompactionSession(
            session_id=session_id,
            underlying_session=session,
            **kwargs,
        )
    except Exception as exc:
        logger.warning(
            "Failed to wrap session with OpenAIResponsesCompactionSession: %s",
            exc,
        )
        return session
