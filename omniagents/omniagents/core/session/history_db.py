"""Public façade for omniagents session-history storage.

This module preserves the original free-function API so all 14 callers
across ``core/agents/service.py``, ``core/eval/*``, ``core/audit/export.py``,
``core/training/sft.py``, ``rpc/realtime_session.py``, ``core/session/manager.py``,
``core/session/query.py``, and ``core/session/cli.py`` keep working without
edits. Each function delegates to a backend instance.

Backend selection (read at first use, not import — so test fixtures that
flip env vars after import via ``importlib.reload`` see the change):

  * ``OMNIAGENTS_HISTORY_BACKEND=sqlite`` (default) — file-per-(project,
    agent) SQLite shard under ``OMNIAGENTS_HOME``. Mirrors the legacy
    behaviour exactly. Honours ``OMNIAGENTS_HISTORY_DB`` (single-file
    override) and the encryption-provider hook.

  * ``OMNIAGENTS_HISTORY_BACKEND=postgres`` — single shared Postgres
    database keyed by (tenant_id, project_slug, agent_slug, session_id).
    Requires ``OMNIAGENTS_HISTORY_URL`` (libpq DSN, e.g.
    ``postgresql://omni_app:...@host:5432/omni_sessions?sslmode=require``)
    and the ``session-postgres`` extra (``psycopg[binary]``,
    ``psycopg_pool``). ``OMNIAGENTS_TENANT_ID`` (default ``"local"``) sets
    the tenant column + ``app.current_tenant`` GUC each operation runs
    under.
"""

from __future__ import annotations

import os
import sqlite3
from threading import Lock
from typing import Any, Optional, Tuple

from .storage.base import _UNSET, SessionStorageBackend
from .storage.sqlite import (
    SqliteSessionStorage,
    _STORES as _SQLITE_STORES,
    set_encryption_provider as _sqlite_set_encryption_provider,
)

# Module-level alias kept for the test conftest, which iterates this dict
# at teardown to close connections. The PG backend ignores it.
_STORES: dict[str, Tuple[sqlite3.Connection, Lock]] = _SQLITE_STORES


def _get_store(
    project_slug: str | None, agent_slug: str | None
) -> Tuple[sqlite3.Connection, Lock]:
    """SQLite-only escape hatch retained for tests that need a raw cursor.

    Resolves the backend (so the connection is opened + schema bootstrapped)
    then returns the cached (conn, lock) tuple from the SQLite store. Raises
    when the active backend isn't SQLite — there's no analogue on Postgres."""
    backend = _resolve_backend()
    if not isinstance(backend, SqliteSessionStorage):
        raise RuntimeError(
            "_get_store is SQLite-only; the active backend does not expose a "
            "sqlite3.Connection."
        )
    return backend._get_store(project_slug, agent_slug)


_backend: SessionStorageBackend | None = None
_backend_lock = Lock()


def _resolve_backend() -> SessionStorageBackend:
    """Lazy single-instance backend resolver. Re-runs after ``importlib
    .reload`` because module-level state resets."""
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        kind = (os.getenv("OMNIAGENTS_HISTORY_BACKEND") or "sqlite").strip().lower()
        if kind == "postgres":
            dsn = os.getenv("OMNIAGENTS_HISTORY_URL")
            if not dsn:
                raise RuntimeError(
                    "OMNIAGENTS_HISTORY_BACKEND=postgres requires "
                    "OMNIAGENTS_HISTORY_URL to be set."
                )
            from .storage.postgres import PgSessionStorage

            _backend = PgSessionStorage(dsn)
        elif kind == "sqlite":
            _backend = SqliteSessionStorage()
        else:
            raise RuntimeError(
                f"Unknown OMNIAGENTS_HISTORY_BACKEND={kind!r}. "
                "Expected 'sqlite' or 'postgres'."
            )
    return _backend


def set_encryption_provider(provider) -> None:
    """Install a SQLite encryption provider (e.g. SQLCipher).

    No-op when the active backend is not SQLite — column-level / cluster-
    level encryption strategies for other backends are configured at the
    deployment layer (Azure PG TDE + CMK) rather than via this hook."""
    _sqlite_set_encryption_provider(provider)


def load_sdk_context(
    session_id: str, *, project_slug=None, agent_slug=None
) -> list[dict] | None:
    return _resolve_backend().load_sdk_context(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def replace_sdk_context(
    session_id: str, items: list[dict], *, project_slug=None, agent_slug=None
) -> None:
    _resolve_backend().replace_sdk_context(
        session_id, items, project_slug=project_slug, agent_slug=agent_slug
    )


# ---------------------------------------------------------------------------
# Retention + hold
# ---------------------------------------------------------------------------


def purge_old_sessions(
    retention_days: int,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> list[str]:
    """Purge history rows past the retention window; returns the purged ids.

    This is the HISTORY half only. Use
    :func:`omniagents.core.session.retention.purge_expired_sessions` unless
    you are deliberately leaving the conversation domain rows and the replay
    journal in place.
    """
    return _resolve_backend().purge_old_sessions(
        retention_days, project_slug=project_slug, agent_slug=agent_slug
    )


def set_session_hold(
    session_id: str,
    hold: bool = True,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    return _resolve_backend().set_session_hold(
        session_id, hold, project_slug=project_slug, agent_slug=agent_slug
    )


def get_session_hold(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    return _resolve_backend().get_session_hold(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def load_history(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> list[dict]:
    return _resolve_backend().load_history(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def append_message(
    session_id: str,
    message: dict,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
    projection: dict | None = None,
) -> None:
    return _resolve_backend().append_message(
        session_id,
        message,
        project_slug=project_slug,
        agent_slug=agent_slug,
        projection=projection,
    )


def pending_projections(
    session_id: str, *, project_slug=None, agent_slug=None
) -> list[dict]:
    return _resolve_backend().pending_projections(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def acknowledge_projection(
    session_id: str, row_id: int, *, project_slug=None, agent_slug=None
) -> None:
    _resolve_backend().acknowledge_projection(
        session_id, row_id, project_slug=project_slug, agent_slug=agent_slug
    )


# ---------------------------------------------------------------------------
# Session lifecycle + metadata
# ---------------------------------------------------------------------------


def register_session(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
    user_id: str | None = None,
) -> None:
    return _resolve_backend().register_session(
        session_id,
        project_slug=project_slug,
        agent_slug=agent_slug,
        user_id=user_id,
    )


def get_session_metadata(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> Tuple[Any, Any]:
    return _resolve_backend().get_session_metadata(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def update_session_metadata(
    session_id: str,
    *,
    context: object = _UNSET,
    variables: object = _UNSET,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    return _resolve_backend().update_session_metadata(
        session_id,
        context=context,
        variables=variables,
        project_slug=project_slug,
        agent_slug=agent_slug,
    )


def set_archived(
    session_id: str,
    archived: bool,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    return _resolve_backend().set_archived(
        session_id, archived, project_slug=project_slug, agent_slug=agent_slug
    )


def get_archived(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    return _resolve_backend().get_archived(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def delete_session(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    return _resolve_backend().delete_session(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_sessions(
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    user_id: str | None = None,
) -> list[dict]:
    return _resolve_backend().list_sessions(
        project_slug=project_slug,
        agent_slug=agent_slug,
        limit=limit,
        offset=offset,
        user_id=user_id,
    )


def get_user_messages(
    *,
    limit: int = 200,
    include_archived: bool = True,
    project_slug: str | None = None,
    agent_slug: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    return _resolve_backend().get_user_messages(
        limit=limit,
        include_archived=include_archived,
        project_slug=project_slug,
        agent_slug=agent_slug,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# User identity
# ---------------------------------------------------------------------------


def update_session_user_id(
    session_id: str,
    user_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    return _resolve_backend().update_session_user_id(
        session_id, user_id, project_slug=project_slug, agent_slug=agent_slug
    )


def get_session_user_id(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> str | None:
    return _resolve_backend().get_session_user_id(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def session_exists(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> bool:
    return _resolve_backend().session_exists(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def list_sessions_for_user(
    user_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> list[dict]:
    return _resolve_backend().list_sessions_for_user(
        user_id, project_slug=project_slug, agent_slug=agent_slug
    )


# ---------------------------------------------------------------------------
# Audio metadata
# ---------------------------------------------------------------------------


def save_audio_metadata(
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
    return _resolve_backend().save_audio_metadata(
        session_id,
        item_id,
        content_index,
        file_path,
        audio_format,
        duration_ms=duration_ms,
        message_id=message_id,
        project_slug=project_slug,
        agent_slug=agent_slug,
    )


def get_audio_metadata(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> list[dict]:
    return _resolve_backend().get_audio_metadata(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )


def get_audio_metadata_by_item(
    item_id: str,
    content_index: int,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> Optional[dict]:
    return _resolve_backend().get_audio_metadata_by_item(
        item_id,
        content_index,
        project_slug=project_slug,
        agent_slug=agent_slug,
    )


def delete_audio_metadata(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> int:
    return _resolve_backend().delete_audio_metadata(
        session_id, project_slug=project_slug, agent_slug=agent_slug
    )
