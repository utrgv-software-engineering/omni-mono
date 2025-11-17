from __future__ import annotations

import asyncio
import inspect
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
    get_session_metadata,
    update_session_metadata,
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
        self.current_stream_result: Any | None = None
        self.cancel_requested: bool = False
        self.cancel_end_reason: str | None = None
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
        self.context: Any | None = None
        self.variables: Any | None = None
        try:
            ctx, vars_data = get_session_metadata(
                self.id, project_slug=project_slug, agent_slug=agent_slug
            )
            if ctx is not None:
                self.context = ctx
            if vars_data is not None:
                self.variables = vars_data
        except Exception:
            self.context = None
            self.variables = None

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
        run_id = task.get_name()
        self._task_map[run_id] = session
        session.current_task = task
        session.current_stream_result = None
        session.cancel_requested = False
        session.cancel_end_reason = None
        task.add_done_callback(lambda _: self._task_map.pop(run_id, None))

    def get_by_run_id(self, run_id: str) -> Optional[Session]:  # noqa: D401
        return self._task_map.get(run_id)

    async def cancel(self, run_id: str, *, timeout: float = 5.0) -> bool:  # noqa: D401
        sess = self._task_map.get(run_id)
        if not sess:
            return False
        task = sess.current_task
        if task is None:
            self._task_map.pop(run_id, None)
            return False
        if task.done():
            self._task_map.pop(run_id, None)
            sess.current_task = None
            sess.current_stream_result = None
            return False

        sess.cancel_requested = True
        if not sess.cancel_end_reason:
            sess.cancel_end_reason = "cancelled"

        stream = getattr(sess, "current_stream_result", None)
        force_cancel = False

        if stream and hasattr(stream, "cancel"):
            try:
                cancel_result = stream.cancel("immediate")
                if inspect.isawaitable(cancel_result):
                    await asyncio.wait_for(cancel_result, timeout)
            except Exception:
                force_cancel = True
        else:
            force_cancel = True

        if not force_cancel and not task.done():
            try:
                await asyncio.wait_for(task, timeout)
            except asyncio.TimeoutError:
                force_cancel = True
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if force_cancel and not task.done():
            if not task.cancelled():
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        sess.current_task = None
        sess.current_stream_result = None
        self._task_map.pop(run_id, None)
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

    def persist_metadata(self, session: Session, *, context: Any, variables: Any) -> None:
        update_session_metadata(
            session.id,
            context=context,
            variables=variables,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
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
