from __future__ import annotations

import asyncio
import uuid
from typing import Dict, Optional, Any

from .history_db import load_history

__all__ = ["Session", "SessionManager", "sm"]


class Session:  # noqa: D101
    """Lightweight container for a client session.

    A Session keeps track of the streaming task currently running for the
    client as well as its chat history so consecutive *start_run* calls
    can preserve context.
    """

    def __init__(self, session_id: str | None = None) -> None:  # noqa: D401
        self.id: str = session_id or str(uuid.uuid4())
        self.history: list[dict] = load_history(self.id) if session_id else []
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


class SessionManager:  # noqa: D101
    """Holds *all* live sessions for the server process."""

    def __init__(self) -> None:  # noqa: D401
        self._by_id: Dict[str, Session] = {}
        self._task_map: Dict[str, Session] = {}

    # ---------------------------------------------------------------------
    # Session lifecycle helpers
    # ---------------------------------------------------------------------
    def get_or_create(self, session_id: str | None = None) -> Session:  # noqa: D401
        if session_id and session_id in self._by_id:
            return self._by_id[session_id]
        sess = Session(session_id)
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
        sess.current_task.cancel()
        return True

    def list_sessions(self) -> list[str]:  # noqa: D401
        """Return the list of active session IDs."""
        return list(self._by_id)


sm = SessionManager() 