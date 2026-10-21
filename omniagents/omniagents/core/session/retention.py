"""Retention: delete a session's data from EVERY store that holds it.

A session's content lives in four places:

1. ``history`` / ``sessions`` / ``audio_metadata`` — the original store.
2. ``threads`` / ``turns`` / ``items`` — the canonical conversation domain
   objects (and, on SQLite, the full-text index built over them).
3. the durable replay journal — every replayable notification, payloads
   included.
4. saved textual tool outputs on the runtime host.

Every one of those holds transcript text. ``purge_old_sessions`` only ever
knew about the first, so a deployment with a retention policy configured
was deleting its history and keeping a complete copy of the same
conversation in the domain store — the defect review found in
``delete_session`` before #312 landed, still live on the retention path.

This module is the single place that knows the full list. Both callers —
the boot-time retention sweep and the ``purge_threads`` RPC — go through
it, so another store added later has exactly one place to be registered.
"""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)

__all__ = [
    "compact_conversation_index",
    "purge_expired_sessions",
    "purge_session_derivatives",
]


def compact_conversation_index(
    *, project_slug: str | None = None, agent_slug: str | None = None
) -> None:
    """Reclaim index space freed by deleted threads. Run ONCE per operation.

    SQLite's FTS5 deletes are tombstones, so the words of a deleted
    transcript survive in the index's segment blobs until a merge rewrites
    them — a "deleted" conversation still greppable in the database file.
    See ``ConversationStore.compact_search_index``.
    """
    try:
        from omniagents.core.conversation.store import get_conversation_store

        get_conversation_store(project_slug, agent_slug).compact_search_index()
    except Exception:
        logger.exception("retention: search index not compacted")


def purge_session_derivatives(
    session_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> None:
    """Drop the domain rows and the replay journal for one session.

    Best-effort per store and loudly logged: a fault reclaiming one store
    must not abandon the others, because a partial purge that stops at the
    first error leaves MORE data behind than one that keeps going.
    """
    try:
        from omniagents.core.conversation.store import get_conversation_store

        get_conversation_store(project_slug, agent_slug).delete_thread(session_id)
    except Exception:
        logger.exception(
            "retention: conversation domain rows not deleted",
            extra={"session": session_id},
        )
    try:
        from omniagents.core.session.event_log import SessionEventLog

        SessionEventLog(
            session_id, project_slug=project_slug, agent_slug=agent_slug
        ).destroy()
    except Exception:
        logger.exception(
            "retention: replay journal not deleted", extra={"session": session_id}
        )

    try:
        import shutil
        from omniagents.core.tools.output_budget import output_dir

        directory = output_dir((project_slug, agent_slug), session_id)
        if directory.exists():
            shutil.rmtree(directory)
    except Exception:
        logger.exception(
            "retention: saved tool outputs not deleted", extra={"session": session_id}
        )


def purge_expired_sessions(
    retention_days: int,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> List[str]:
    """Delete every store's copy of sessions past the retention window.

    Sessions under legal hold are skipped by the history backend and are
    therefore skipped here too — the hold has to win over retention or it
    is not a hold. ``retention_days <= 0`` is a no-op.

    Returns the purged session ids.
    """
    if retention_days <= 0:
        return []
    from omniagents.core.session.history_db import purge_old_sessions

    purged = purge_old_sessions(
        retention_days, project_slug=project_slug, agent_slug=agent_slug
    )
    for session_id in purged:
        purge_session_derivatives(
            session_id, project_slug=project_slug, agent_slug=agent_slug
        )
    if purged:
        compact_conversation_index(project_slug=project_slug, agent_slug=agent_slug)
    return purged
