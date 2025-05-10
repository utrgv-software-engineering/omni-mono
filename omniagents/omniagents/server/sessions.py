from __future__ import annotations

import asyncio
import uuid
from typing import Dict, Optional

__all__ = ["Session", "SessionManager", "sm"]


class Session:  # noqa: D101
    """Lightweight container for a client session.

    A Session keeps track of the streaming task currently running for the
    client as well as its chat history so consecutive *start_run* calls
    can preserve context.
    """

    def __init__(self) -> None:  # noqa: D401
        self.id: str = str(uuid.uuid4())
        self.history: list[dict] = []
        self.current_task: Optional[asyncio.Task] = None


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
        sess = Session()
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


sm = SessionManager() 