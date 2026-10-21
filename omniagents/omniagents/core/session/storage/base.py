"""Abstract storage backend for omniagents session history.

The OmniAgents-native session layer (``history_db``) historically owned a
sqlite3 connection cache and a flat module of free functions. Cloud
deployments need durable, concurrent-writer storage that survives App
Service container restarts, so the storage is split into a backend
interface plus interchangeable implementations.

Backends are sync because the existing callers (``Session.append_message``,
``SessionManager.list_sessions``, the eval/training/audit pipelines) are
sync — keeping the interface sync avoids forcing every caller into
``asyncio.to_thread``.

Tenant scoping is part of the interface, not a per-call kwarg, because
the per-tenant identity is established at backend construction (from
``OMNIAGENTS_TENANT_ID``) and applies uniformly across operations.
SQLite backends ignore the tenant and key purely on (project, agent);
Postgres backends include it in the primary key and (optionally) enforce
it with row-level security.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple

# Sentinel matching the one in the legacy history_db so callers can keep
# distinguishing "do not update this column" from "set it to None".
_UNSET = object()


class SessionStorageBackend(ABC):
    """Interface for omniagents session-history storage.

    Implementations:
      * :class:`omniagents.core.session.storage.sqlite.SqliteSessionStorage`
      * :class:`omniagents.core.session.storage.postgres.PgSessionStorage`
    """

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release any pooled resources. Idempotent."""

    @abstractmethod
    def load_sdk_context(
        self, session_id: str, *, project_slug=None, agent_slug=None
    ) -> list[dict] | None: ...

    @abstractmethod
    def replace_sdk_context(
        self, session_id: str, items: list[dict], *, project_slug=None, agent_slug=None
    ) -> None: ...

    # ------------------------------------------------------------------
    # Retention + legal hold
    # ------------------------------------------------------------------

    @abstractmethod
    def purge_old_sessions(
        self,
        retention_days: int,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[str]:
        """Delete sessions older than *retention_days*. Returns the purged ids.

        Sessions under legal hold (``hold = 1``) are skipped. ``retention_days
        <= 0`` is a no-op.

        The **ids**, not a count: a session's data does not live only in
        this backend. The canonical conversation rows and the durable replay
        journal are separate stores, and a caller that is told "7 sessions
        were purged" cannot go and finish the job. Retention that leaves
        transcripts behind is a retention failure, so the identity of what
        was removed is part of the contract — see
        ``omniagents.core.session.retention.purge_expired_sessions``."""

    @abstractmethod
    def set_session_hold(
        self,
        session_id: str,
        hold: bool = True,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        """Set or clear legal hold. Returns True if a row was updated."""

    @abstractmethod
    def get_session_hold(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool: ...

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------

    @abstractmethod
    def load_history(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]:
        """Return history rows in insertion order."""

    @abstractmethod
    def append_message(
        self,
        session_id: str,
        message: dict,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        projection: dict | None = None,
    ) -> None: ...

    @abstractmethod
    def pending_projections(
        self, session_id: str, *, project_slug=None, agent_slug=None
    ) -> list[dict]: ...

    @abstractmethod
    def acknowledge_projection(
        self, session_id: str, row_id: int, *, project_slug=None, agent_slug=None
    ) -> None: ...

    # ------------------------------------------------------------------
    # Session lifecycle + metadata
    # ------------------------------------------------------------------

    @abstractmethod
    def register_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Insert if missing. Idempotent."""

    @abstractmethod
    def get_session_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> Tuple[Any, Any]:
        """Return ``(context, variables)`` decoded from JSON, or ``(None, None)``."""

    @abstractmethod
    def update_session_metadata(
        self,
        session_id: str,
        *,
        context: object = _UNSET,
        variables: object = _UNSET,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None:
        """Update non-``_UNSET`` columns."""

    @abstractmethod
    def set_archived(
        self,
        session_id: str,
        archived: bool,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None: ...

    @abstractmethod
    def get_archived(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def delete_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        """Hard delete the session + its history + its audio metadata. Returns
        True if the session was found. Raises ValueError if the session is on
        legal hold."""

    # ------------------------------------------------------------------
    # Listing + search
    # ------------------------------------------------------------------

    @abstractmethod
    def list_sessions(
        self,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        user_id: str | None = None,
    ) -> list[dict]:
        """Sessions (newest-first) with a trimmed first/last-message preview.

        ``limit``/``offset`` page the newest-first ordering; ``limit=None``
        returns everything (the historical behavior). ``user_id`` scopes the
        listing to one owner **in SQL**, so ownership filtering composes
        with pagination instead of shrinking pages after the fact.
        """

    @abstractmethod
    def get_user_messages(
        self,
        *,
        limit: int = 200,
        include_archived: bool = True,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """Recent user-role messages across all sessions, oldest-first.

        ``user_id`` scopes to one owner in SQL (see :meth:`list_sessions`).
        """

    # ------------------------------------------------------------------
    # User identity (added late in the SQLite schema)
    # ------------------------------------------------------------------

    @abstractmethod
    def update_session_user_id(
        self,
        session_id: str,
        user_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None: ...

    @abstractmethod
    def get_session_user_id(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> str | None:
        """Owner ``user_id`` recorded for *session_id* (``None`` = unowned)."""

    @abstractmethod
    def session_exists(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> bool:
        """Whether a ``sessions`` row already exists for *session_id*.

        Lets callers distinguish creating a session (owner may be stamped)
        from attaching to an existing one (owner must never be rewritten).
        """

    @abstractmethod
    def list_sessions_for_user(
        self,
        user_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]: ...

    # ------------------------------------------------------------------
    # Audio metadata (voice/realtime agents)
    # ------------------------------------------------------------------

    @abstractmethod
    def save_audio_metadata(
        self,
        session_id: str,
        item_id: str,
        content_index: int,
        file_path: str,
        audio_format: str,
        duration_ms: Optional[int] = None,
        message_id: Optional[int] = None,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> int:
        """Return the inserted row id."""

    @abstractmethod
    def get_audio_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    def get_audio_metadata_by_item(
        self,
        item_id: str,
        content_index: int,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> Optional[dict]: ...

    @abstractmethod
    def delete_audio_metadata(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> int: ...
