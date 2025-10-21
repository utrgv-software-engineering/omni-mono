from __future__ import annotations

import asyncio
import uuid
from typing import Dict, Optional, Any

from .history_db import (
    load_history,
    register_session,
    get_archived,
    set_archived,
    list_sessions as db_list_sessions,
    append_message as db_append_message,
    get_user_messages,
)

__all__ = ["Session", "SessionManager", "sm"]


class Session:  # noqa: D101
    """Lightweight container for a client session.

    A Session keeps track of the streaming task currently running for the
    client as well as its chat history so consecutive *start_run* calls
    can preserve context.
    """

    def __init__(
        self,
        project_slug: str | None,
        agent_slug: str | None,
        session_id: str | None = None,
    ) -> None:  # noqa: D401
        self._project_slug = project_slug
        self._agent_slug = agent_slug
        self.id: str = session_id or str(uuid.uuid4())
        self.history: list[dict] = (
            load_history(self.id, project_slug=project_slug, agent_slug=agent_slug)
            if session_id
            else []
        )
        self.current_task: Optional[asyncio.Task] = None
        # Holds (method, payload) tuples that could not be delivered because
        # the WebSocket connection was already closed.  When the client
        # reconnects we replay everything that is still pending so the UI can
        # catch up (e.g. pending tool-approval requests).
        self.pending_events: list[tuple[str, dict]] = []
        # Active Rpc channels (one per concurrent client connection).  We
        # may have more than one in case the user opens multiple tabs.
        # Disconnected channels are automatically culled when a send fails.
        self.channels: list[Any] = []
        # Map active run_id -> preferred channel that initiated the run.
        self.run_channels: Dict[str, Any] = {}
        # Flag indicating whether this session was archived by the client.
        self.archived: bool = (
            get_archived(self.id, project_slug=project_slug, agent_slug=agent_slug)
            if session_id
            else False
        )
        self.always_allow_tools: set[str] = set()
        self.always_allow_patterns: set[str] = set()

    def append_message(self, message: dict) -> None:
        """Add a message to this session and persist it."""
        self.history.append(message)
        db_append_message(
            self.id,
            message,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )


class SessionManager:  # noqa: D101
    """Holds *all* live sessions for the server process."""

    def __init__(
        self, project_slug: str | None, agent_slug: str | None
    ) -> None:  # noqa: D401
        self._project_slug = project_slug
        self._agent_slug = agent_slug
        self._by_id: Dict[str, Session] = {}
        self._task_map: Dict[str, Session] = {}

    # ---------------------------------------------------------------------
    # Session lifecycle helpers
    # ---------------------------------------------------------------------
    def get_or_create(self, session_id: str | None = None) -> Session:  # noqa: D401
        if session_id and session_id in self._by_id:
            return self._by_id[session_id]
        sess = Session(self._project_slug, self._agent_slug, session_id)
        register_session(
            sess.id, project_slug=self._project_slug, agent_slug=self._agent_slug
        )
        self._by_id[sess.id] = sess
        return sess

    def bind_task(self, task: asyncio.Task, session: Session) -> None:  # noqa: D401
        self._task_map[task.get_name()] = session
        session.current_task = task

    def get_by_run_id(self, run_id: str) -> Optional[Session]:  # noqa: D401
        return self._task_map.get(run_id)

    def cancel(self, run_id: str) -> bool:  # noqa: D401
        sess = self._task_map.get(run_id)
        if not sess or not sess.current_task:
            return False
        task = sess.current_task
        # Cancel the task - this will raise CancelledError in the task
        task.cancel()
        # Clear the task reference so new runs can start immediately
        # The task will still run its cleanup but won't block new runs
        sess.current_task = None
        return True

    def archive_session(self, session_id: str) -> bool:  # noqa: D401
        """Mark *session_id* as archived."""
        sess = self._by_id.get(session_id)
        if not sess:
            return False
        sess.archived = True
        set_archived(
            session_id,
            True,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )
        return True

    def list_sessions(self) -> list[dict]:  # noqa: D401
        """Return metadata for all known sessions."""
        return db_list_sessions(
            project_slug=self._project_slug, agent_slug=self._agent_slug
        )

    def get_user_messages(
        self,
        limit: int,
        include_archived: bool,
    ) -> list[dict]:  # noqa: D401
        return get_user_messages(
            limit=limit,
            include_archived=include_archived,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )


sm = SessionManager("legacy", "legacy")
