"""Durable storage for the canonical Thread / Turn / Item objects.

Two backends, selected by the same ``OMNIAGENTS_HISTORY_BACKEND`` env the
session history uses, so a deployment never ends up with its transcript in
Postgres and its domain objects in SQLite:

* :class:`SqliteConversationStore` — colocated with the session-history
  shard at ``<sessions.db>.domain``. This is the
  :class:`~omniagents.core.session.event_log.SqliteEventLogStore` precedent:
  test fixtures that redirect ``OMNIAGENTS_HISTORY_DB`` isolate the domain
  store automatically, with no extra plumbing.
* :class:`PgConversationStore` — the cloud counterpart, mirroring
  :class:`~omniagents.core.session.storage.postgres.PgSessionStorage`:
  ``tenant_id`` on every row and in every predicate, ``JSONB`` payloads,
  ``TIMESTAMPTZ``-free (domain timestamps are epoch floats, so ordering is
  identical across both backends), row-level security pre-positioned on the
  ``app.current_tenant`` GUC.

The store persists and queries rows. Lifecycle
rules (terminal-once transitions, ordinal allocation policy) live in
:mod:`~omniagents.core.conversation.model` and
:mod:`~omniagents.core.conversation.recorder`. Runtime append and mutation
operations own the SQL transactions so identity, counters and transitions
cannot be torn apart or overwritten by stale reads.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
import json
import logging
import os
import sqlite3
from threading import Lock
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from omniagents.core.paths import get_sessions_db_path

from .model import (
    ITEM_SCHEMA_VERSION,
    UNSCOPED,
    Item,
    ItemKind,
    ItemStatus,
    SearchHit,
    Thread,
    ThreadFilter,
    ThreadStatus,
    Turn,
    TurnStatus,
    derive_search_text,
    search_terms,
)

logger = logging.getLogger(__name__)


def _attach_initial_item(thread: Thread, turn: Turn, item: Item) -> Item:
    if item.thread_id != thread.thread_id or item.turn_id != turn.turn_id:
        raise ValueError("Initial item must belong to its turn")
    thread.last_seq += 1
    thread.item_count += 1
    turn.item_count += 1
    turn.first_seq = min(turn.first_seq or thread.last_seq, thread.last_seq)
    turn.last_seq = max(turn.last_seq or thread.last_seq, thread.last_seq)
    return replace(item, seq=thread.last_seq)


__all__ = [
    "ConversationStore",
    "PgConversationStore",
    "SearchUnavailable",
    "SqliteConversationStore",
    "get_conversation_store",
    "reset_conversation_store",
]


class SearchUnavailable(RuntimeError):
    """The active backend has no full-text index available.

    Raised instead of quietly degrading to a Python substring scan: a
    search that silently stops using the index would return *different*
    results (and different previews) from the same query on the same data,
    which is worse than a typed failure the client can report.
    """


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value)
    except TypeError:
        return json.dumps(value, default=str)


def _loads(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_loads(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None or raw == "":
        return None
    decoded = _loads(raw)
    return decoded or None


class ConversationStore(ABC):
    def read_snapshot(self, thread_id: str) -> dict:
        """Read all conversation items and the inbox in one database snapshot.

        This is a full hydration boundary, not independently paginated reads.
        Runtime/event-journal state is still owned by the serving process.
        """
        raise NotImplementedError("This store does not support consistent snapshots")

    @abstractmethod
    def mutate_queue(
        self, thread_id: str, update: Callable, *, submission_id: str | None = None
    ) -> Any:
        """Atomically mutate the durable inbox and an optional submission receipt.

        Callback(state, receipt) returns (result, replacement_receipt). It
        may modify state but must not perform I/O or call the store.
        """

    def submission_receipt(
        self,
        thread_id: str,
        submission_id: str,
        claim: dict | None = None,
        result: dict | None = None,
    ) -> dict | None:
        """Atomically reserve a submission or read/update its durable receipt."""
        raise NotImplementedError

    """Backend interface for the conversation domain objects.

    Project/agent (and, on Postgres, tenant) scoping is fixed at
    construction — see the module docstring of
    ``omniagents.core.session.storage.base`` for why scoping is part of
    construction rather than a per-call kwarg.
    """

    def close(self) -> None:
        """Release pooled resources. Idempotent."""

    # -- threads -------------------------------------------------------

    @abstractmethod
    def get_thread(self, thread_id: str) -> Optional[Thread]: ...

    @abstractmethod
    def upsert_thread(self, thread: Thread) -> None: ...

    @abstractmethod
    def mutate_thread(
        self, thread_id: str, update: Callable[[Thread], None]
    ) -> Optional[Thread]:
        """Apply an in-memory mutation to the locked current thread."""

    @abstractmethod
    def delete_thread(self, thread_id: str) -> bool:
        """Remove a thread and everything under it. True if it existed.

        "Everything" includes the full-text index entries. A search index
        that outlived the transcript would keep answering queries with
        previews of deleted content — the same retention defect as leaving
        the domain rows behind."""

    @abstractmethod
    def list_threads(
        self,
        thread_filter: ThreadFilter,
        *,
        limit: int,
        after: Optional[Tuple[float, str]] = None,
        descending: bool = True,
    ) -> List[Thread]:
        """One keyset page of threads ordered by ``(updated_at, thread_id)``.

        ``after`` is the position from the previous page's cursor, applied
        as a strict composite comparison so a listing that is being written
        while a client pages through it neither skips nor repeats rows.

        Every field of *thread_filter* — the ownership scope included — is
        applied in SQL, in the same statement as ``LIMIT``. The scope has
        no default and "every principal" is the explicit
        :data:`~omniagents.core.conversation.model.UNSCOPED` sentinel, so a
        caller who omitted it gets a type error rather than an unscoped
        listing.
        """

    @abstractmethod
    def count_threads(self, thread_filter: ThreadFilter) -> int:
        """Threads matching the SAME predicate :meth:`list_threads` pages."""

    @abstractmethod
    def get_threads(self, thread_ids: Sequence[str]) -> Dict[str, Thread]:
        """A whole PAGE of thread rows, in one statement per batch.

        Keyed by id and silently missing the ones that are gone, so a
        caller that assembled its id list from a separate walk is not
        broken by a concurrent delete between the two reads. Exists so
        that a page derived from :meth:`descendant_thread_rows` costs two
        statements rather than N.
        """

    def compact_search_index(self) -> None:
        """Reclaim search-index space freed by deleted threads.

        A deletion removes the row and its searchable entry immediately,
        but SQLite's FTS5 deletes are TOMBSTONES: the terms of the deleted
        transcript stay in the index's segment blobs until a merge rewrites
        them, so the words of a deleted conversation remain in the database
        file — readable with a hex editor — after ``delete_thread`` returns
        and even after ``VACUUM``. ``PRAGMA secure_delete`` cannot help,
        because the pages were never freed.

        Callers run this ONCE at the end of a destructive operation rather
        than per row: the cost is proportional to the whole index (~22 ms
        for a 60k-item shard), which is fine for one delete or one
        retention pass and is not fine multiplied by a 500-thread cascade.

        Default no-op: a backend whose index is a column of the row it
        describes has nothing to reclaim.
        """

    @abstractmethod
    def count_items_for_threads(self, thread_ids: Sequence[str]) -> Dict[str, int]:
        """Exact item counts for a whole PAGE, in one statement.

        Older writers maintained ``Thread.item_count`` non-transactionally,
        so historical counters can drift. A listing is the surface a client
        sizes its UI from. Counting per row would
        be N round trips for a page of N; one grouped count is two.
        """

    @abstractmethod
    def count_turns_for_threads(self, thread_ids: Sequence[str]) -> Dict[str, int]:
        """Exact turn counts for a whole page. See
        :meth:`count_items_for_threads`."""

    @abstractmethod
    def descendant_thread_rows(
        self, root_thread_id: str
    ) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """EVERY thread below *root_thread_id*, as ``(id, user_id, parent)``.

        **The only descendant walk in the system.** The ancestry read, the
        archive/unarchive cascade and the delete guard all derive from this
        one result (via
        :meth:`~omniagents.core.conversation.service.ConversationService.descendant_nodes`),
        because two walks over one relation drift: an owner-scoped CTE and
        a per-node-scoped cascade once disagreed about which of the
        caller's own threads existed below a fork owned by somebody else.

        Two properties make it usable as an integrity check as well as a
        read:

        * **Unbounded.** No depth ceiling, no row limit. A capped walk used
          as a delete guard silently answers "these are all the children"
          when it is not, and the caller then orphans the overflow. Cycle
          termination comes from de-duplicating on ``thread_id`` alone (the
          recursive ``UNION``), not from a depth cap. Callers that cap —
          the ancestry read — cap their OUTPUT and say so.
        * **Owner-agnostic.** A descendant owned by someone else — or, on a
          partially backfilled shard, owned by nobody — still holds a
          ``parent_thread_id`` pointing here. Whether the relationship
          exists is not a question about who may read it; every caller that
          reads or acts turns the returned owner into its own per-node
          access decision.
        """

    @abstractmethod
    def search(
        self,
        query: str,
        thread_filter: ThreadFilter,
        *,
        limit: int,
        offset: int = 0,
    ) -> Tuple[List[SearchHit], int]:
        """Full-text search over titles and item content.

        Returns ``(hits, total_matching_threads)``. One hit per matching
        thread, carrying the count of matching rows in that thread and the
        highest-ranked match's backend-generated preview.

        Ranked results are paged by ``offset``, not by a keyset: rank is
        not a monotonic column, so there is no position to resume from.
        The predicate — ownership included — is applied in SQL alongside
        the match, so a page is never shrunk after the database drew it.

        Raises :class:`SearchUnavailable` when the backend has no index."""

    @abstractmethod
    def allocate_seq(self, thread_id: str, count: int = 1) -> int:
        """Atomically reserve *count* item ordinals; return the FIRST one.

        Must be a single statement (or an equivalent serialized section):
        two concurrent tool completions on the same thread must never be
        handed the same ordinal, or the unique ``(thread_id, seq)`` index
        rejects one of them and an item is lost.
        """

    # -- turns ---------------------------------------------------------

    @abstractmethod
    def get_turn(self, thread_id: str, turn_id: str) -> Optional[Turn]: ...

    @abstractmethod
    def upsert_turn(self, turn: Turn) -> None: ...

    @abstractmethod
    def mutate_turn(
        self,
        thread_id: str,
        turn_id: str,
        update: Callable[[Thread, Optional[Turn], int], Optional[Turn]],
        *,
        initial_item: Optional[Item] = None,
    ) -> Optional[Turn]:
        """Read/modify a turn and its thread in one transaction.

        The callback receives the locked current thread, current turn and
        next available ordinal. It must not call storage or perform I/O.
        Raw upserts remain for projection/import; runtime uses this method
        so stale metadata cannot overwrite item counters or settled state.
        """

    @abstractmethod
    def list_turns(
        self,
        thread_id: str,
        *,
        limit: int,
        after_ordinal: Optional[int] = None,
        before_ordinal: Optional[int] = None,
        descending: bool = False,
    ) -> List[Turn]: ...

    @abstractmethod
    def count_turns(self, thread_id: str) -> int: ...

    @abstractmethod
    def max_turn_ordinal(self, thread_id: str) -> int: ...

    # -- items ---------------------------------------------------------

    @abstractmethod
    def get_item(self, thread_id: str, item_id: str) -> Optional[Item]: ...

    @abstractmethod
    def upsert_item(self, item: Item) -> None: ...

    @abstractmethod
    def append_item(self, item: Item) -> Item:
        """Atomically create an item, allocate its sequence and update counts.

        An existing identity is returned unchanged, without advancing cursors
        or counts. The caller's proposed sequence is ignored for new items.
        """

    @abstractmethod
    def mutate_item(
        self, thread_id: str, item_id: str, update: Callable[[Item], Item]
    ) -> Optional[Item]:
        """Apply an in-memory revision/transition to the locked current item."""
        ...

    @abstractmethod
    def list_items(
        self,
        thread_id: str,
        *,
        limit: int,
        turn_id: Optional[str] = None,
        kinds: Optional[Sequence[ItemKind]] = None,
        after_seq: Optional[int] = None,
        before_seq: Optional[int] = None,
        descending: bool = False,
    ) -> List[Item]: ...

    @abstractmethod
    def count_items(
        self,
        thread_id: str,
        *,
        turn_id: Optional[str] = None,
        kinds: Optional[Sequence[ItemKind]] = None,
    ) -> int:
        """Count items under the SAME predicate ``list_items`` pages.

        ``kinds`` is part of the predicate because ``total`` ships on the
        wire next to a filtered page; a count that ignored the filter would
        report 21 next to a single returned item."""

    @abstractmethod
    def open_items(
        self, thread_id: str, *, turn_id: Optional[str] = None
    ) -> List[Item]:
        """Non-terminal items, oldest first. Drives run-end finalization.

        ``turn_id`` narrows to one turn; omitting it returns every open item
        on the thread, INCLUDING items recorded with no ``turn_id`` at all
        (an elicitation or client_request that carried no ``run_id``). The
        run-end sweep needs the wider view or those items stay ``started``
        forever."""

    # -- bulk ----------------------------------------------------------

    @abstractmethod
    def write_projection(
        self, thread: Thread, turns: Iterable[Turn], items: Iterable[Item]
    ) -> None:
        """Persist a whole projected thread in ONE transaction.

        Used by the compatibility backfill: a half-written projection would
        look like a partially recorded thread and would never be retried,
        so atomicity here is a correctness requirement, not an optimization.
        """


# ---------------------------------------------------------------------
# Row (de)serialization shared by both backends
# ---------------------------------------------------------------------

_THREAD_COLUMNS = (
    "thread_id",
    "user_id",
    "status",
    "title",
    "created_at",
    "updated_at",
    "last_seq",
    "turn_count",
    "item_count",
    "parent_thread_id",
    "branched_from_item_id",
    "usage_json",
    "compaction_json",
    "source",
    "projected_at",
    "schema_version",
    # Appended, never inserted: ``_thread_from_row`` reads by position, so
    # a column added in the middle would silently re-map every field after
    # it.
    "pinned",
    "metadata_json",
)

_TURN_COLUMNS = (
    "thread_id",
    "turn_id",
    "ordinal",
    "status",
    "created_at",
    "updated_at",
    "completed_at",
    "prompt",
    "prompt_role",
    "end_reason",
    "error_json",
    "usage_json",
    "model",
    "model_ref",
    "item_count",
    "first_seq",
    "last_seq",
    "attempts",
    "source",
    "schema_version",
)

_ITEM_COLUMNS = (
    "thread_id",
    "item_id",
    "turn_id",
    "seq",
    "kind",
    "status",
    "role",
    "created_at",
    "updated_at",
    "completed_at",
    "revision",
    "content_json",
    "source_ref_json",
    "long_lived",
    "source",
    "schema_version",
)

#: The item write set: every stored column plus the derived, indexed
#: search projection. ``search_text`` is deliberately NOT in
#: ``_ITEM_COLUMNS`` (the read/round-trip set) because it is not a field of
#: :class:`Item` — it is an index input recomputed from ``content`` on
#: every write, so it can never drift from the content it describes.
_ITEM_WRITE_COLUMNS = _ITEM_COLUMNS + ("search_text",)


# ---------------------------------------------------------------------
# Upsert SQL
#
# ``INSERT OR REPLACE`` was wrong on two counts and both were destructive:
#
# * On ``threads`` it rewrote ``last_seq`` from whatever the caller happened
#   to be holding. A reader that had loaded a Thread before the recorder
#   advanced the cursor would roll it BACKWARDS on the next write, and the
#   next ``allocate_seq`` would hand out an already-used ordinal.
# * On ``items`` a REPLACE that violated the unique ``(thread_id, seq)``
#   index silently DELETED the row already holding that seq instead of
#   raising. That turned the rolled-back cursor into permanent, unlogged
#   data loss.
#
# So: ``last_seq`` only ever moves forward (``MAX``/``GREATEST``), and
# conflicts are resolved on the primary key with an explicit DO UPDATE —
# any *other* constraint violation now raises loudly.
# ---------------------------------------------------------------------


def _sqlite_upsert(table: str, columns: tuple[str, ...], key: tuple[str, ...]) -> str:
    assignments = []
    for column in columns:
        if column in key:
            continue
        if column == "last_seq" and table == "threads":
            assignments.append(f"{column} = MAX({table}.{column}, excluded.{column})")
        elif column == "projected_at" and table == "threads":
            # Once materialization has happened it has happened; a writer
            # holding an older Thread must not un-set the marker.
            assignments.append(
                f"{column} = COALESCE({table}.{column}, excluded.{column})"
            )
        else:
            assignments.append(f"{column} = excluded.{column}")
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' * len(columns))}) "
        f"ON CONFLICT({', '.join(key)}) DO UPDATE SET {', '.join(assignments)}"
    )


_SQLITE_UPSERT_THREAD = _sqlite_upsert("threads", _THREAD_COLUMNS, ("thread_id",))
_SQLITE_UPSERT_TURN = _sqlite_upsert("turns", _TURN_COLUMNS, ("thread_id", "turn_id"))
_SQLITE_UPSERT_ITEM = _sqlite_upsert(
    "items", _ITEM_WRITE_COLUMNS, ("thread_id", "item_id")
)


def _thread_values(thread: Thread) -> Tuple[Any, ...]:
    return (
        thread.thread_id,
        thread.user_id,
        thread.status.value,
        thread.title,
        float(thread.created_at),
        float(thread.updated_at),
        int(thread.last_seq),
        int(thread.turn_count),
        int(thread.item_count),
        thread.parent_thread_id,
        thread.branched_from_item_id,
        _dumps(thread.usage),
        _dumps(thread.compaction),
        thread.source,
        thread.projected_at,
        int(thread.schema_version),
        1 if thread.pinned else 0,
        _dumps(thread.metadata),
    )


def _thread_from_row(row: Sequence[Any]) -> Thread:
    return Thread(
        thread_id=row[0],
        user_id=row[1],
        status=ThreadStatus(row[2]),
        title=row[3],
        created_at=float(row[4] or 0.0),
        updated_at=float(row[5] or 0.0),
        last_seq=int(row[6] or 0),
        turn_count=int(row[7] or 0),
        item_count=int(row[8] or 0),
        parent_thread_id=row[9],
        branched_from_item_id=row[10],
        usage=_loads(row[11]),
        compaction=_loads(row[12]),
        source=row[13] or "recorded",
        projected_at=float(row[14]) if row[14] is not None else None,
        schema_version=int(row[15] or ITEM_SCHEMA_VERSION),
        pinned=bool(row[16]),
        metadata=_loads(row[17]),
    )


def _turn_values(turn: Turn) -> Tuple[Any, ...]:
    return (
        turn.thread_id,
        turn.turn_id,
        int(turn.ordinal),
        turn.status.value,
        float(turn.created_at),
        float(turn.updated_at),
        turn.completed_at,
        turn.prompt,
        turn.prompt_role,
        turn.end_reason,
        _dumps(turn.error) if turn.error is not None else None,
        _dumps(turn.usage),
        turn.model,
        turn.model_ref,
        int(turn.item_count),
        turn.first_seq,
        turn.last_seq,
        int(turn.attempts),
        turn.source,
        int(turn.schema_version),
    )


def _turn_from_row(row: Sequence[Any]) -> Turn:
    return Turn(
        thread_id=row[0],
        turn_id=row[1],
        ordinal=int(row[2] or 0),
        status=TurnStatus(row[3]),
        created_at=float(row[4] or 0.0),
        updated_at=float(row[5] or 0.0),
        completed_at=float(row[6]) if row[6] is not None else None,
        prompt=row[7],
        prompt_role=row[8],
        end_reason=row[9],
        error=_optional_loads(row[10]),
        usage=_loads(row[11]),
        model=row[12],
        model_ref=row[13],
        item_count=int(row[14] or 0),
        first_seq=int(row[15]) if row[15] is not None else None,
        last_seq=int(row[16]) if row[16] is not None else None,
        attempts=int(row[17] or 0),
        source=row[18] or "recorded",
        schema_version=int(row[19] or ITEM_SCHEMA_VERSION),
    )


def _item_values(item: Item) -> Tuple[Any, ...]:
    return (
        item.thread_id,
        item.item_id,
        item.turn_id,
        int(item.seq),
        item.kind.value,
        item.status.value,
        item.role,
        float(item.created_at),
        float(item.updated_at),
        item.completed_at,
        int(item.revision),
        _dumps(item.content),
        _dumps(item.source_ref),
        1 if item.long_lived else 0,
        item.source,
        int(item.schema_version),
        derive_search_text(item.kind, item.content),
    )


def _item_from_row(row: Sequence[Any]) -> Item:
    return Item(
        thread_id=row[0],
        item_id=row[1],
        turn_id=row[2],
        seq=int(row[3] or 0),
        kind=ItemKind(row[4]),
        status=ItemStatus(row[5]),
        role=row[6],
        created_at=float(row[7] or 0.0),
        updated_at=float(row[8] or 0.0),
        completed_at=float(row[9]) if row[9] is not None else None,
        revision=int(row[10] or 0),
        content=_loads(row[11]),
        source_ref=_loads(row[12]),
        long_lived=bool(row[13]),
        source=row[14] or "recorded",
        schema_version=int(row[15] or ITEM_SCHEMA_VERSION),
    )


#: How many thread ids one grouped-count statement binds at a time. SQLite
#: caps host parameters (999 by default); the batch keeps the page-count
#: query one statement per batch rather than one per row either way.
_COUNT_BATCH = 400

#: Rows per transaction in the Postgres ``search_text`` backfill. Small
#: enough that an upgrade never holds one long write transaction over the
#: whole table.
_PG_BACKFILL_BATCH = 500

_TERMINAL_STATUS_VALUES = tuple(
    status.value
    for status in (ItemStatus.COMPLETED, ItemStatus.FAILED, ItemStatus.CANCELLED)
)


# ---------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------

_SQLITE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS command_inboxes (thread_id TEXT PRIMARY KEY, state TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS submission_receipts (thread_id TEXT NOT NULL, submission_id TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(thread_id, submission_id))",
    """
    CREATE TABLE IF NOT EXISTS threads (
        thread_id             TEXT PRIMARY KEY,
        user_id               TEXT,
        status                TEXT    NOT NULL DEFAULT 'active',
        title                 TEXT,
        created_at            REAL    NOT NULL DEFAULT 0,
        updated_at            REAL    NOT NULL DEFAULT 0,
        last_seq              INTEGER NOT NULL DEFAULT 0,
        turn_count            INTEGER NOT NULL DEFAULT 0,
        item_count            INTEGER NOT NULL DEFAULT 0,
        parent_thread_id      TEXT,
        branched_from_item_id TEXT,
        usage_json            TEXT    NOT NULL DEFAULT '{}',
        compaction_json       TEXT    NOT NULL DEFAULT '{}',
        source                TEXT    NOT NULL DEFAULT 'recorded',
        projected_at          REAL,
        schema_version        INTEGER NOT NULL DEFAULT 1,
        pinned                INTEGER NOT NULL DEFAULT 0,
        metadata_json         TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS turns (
        thread_id      TEXT    NOT NULL,
        turn_id        TEXT    NOT NULL,
        ordinal        INTEGER NOT NULL,
        status         TEXT    NOT NULL DEFAULT 'running',
        created_at     REAL    NOT NULL DEFAULT 0,
        updated_at     REAL    NOT NULL DEFAULT 0,
        completed_at   REAL,
        prompt         TEXT,
        prompt_role    TEXT,
        end_reason     TEXT,
        error_json     TEXT,
        usage_json     TEXT    NOT NULL DEFAULT '{}',
        model          TEXT,
        model_ref      TEXT,
        item_count     INTEGER NOT NULL DEFAULT 0,
        first_seq      INTEGER,
        last_seq       INTEGER,
        attempts       INTEGER NOT NULL DEFAULT 0,
        source         TEXT    NOT NULL DEFAULT 'recorded',
        schema_version INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (thread_id, turn_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS items (
        thread_id       TEXT    NOT NULL,
        item_id         TEXT    NOT NULL,
        turn_id         TEXT,
        seq             INTEGER NOT NULL,
        kind            TEXT    NOT NULL,
        status          TEXT    NOT NULL DEFAULT 'started',
        role            TEXT,
        created_at      REAL    NOT NULL DEFAULT 0,
        updated_at      REAL    NOT NULL DEFAULT 0,
        completed_at    REAL,
        revision        INTEGER NOT NULL DEFAULT 0,
        content_json    TEXT    NOT NULL DEFAULT '{}',
        source_ref_json TEXT    NOT NULL DEFAULT '{}',
        long_lived      INTEGER NOT NULL DEFAULT 0,
        source          TEXT    NOT NULL DEFAULT 'recorded',
        schema_version  INTEGER NOT NULL DEFAULT 1,
        search_text     TEXT,
        PRIMARY KEY (thread_id, item_id)
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_thread_seq ON items(thread_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_items_turn ON items(thread_id, turn_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_turns_ordinal ON turns(thread_id, ordinal)",
    # The thread listing's ordering key and its two hottest filters. The
    # listing is the sidebar of every client, so it runs constantly.
    "CREATE INDEX IF NOT EXISTS idx_threads_listing "
    "ON threads(user_id, status, updated_at, thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_threads_parent ON threads(parent_thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_turns_model ON turns(model, thread_id)",
)

#: Additive column migrations for stores created by an earlier build. Each
#: is attempted once per connection and ignored when the column exists.
_SQLITE_MIGRATIONS = (
    "ALTER TABLE items ADD COLUMN long_lived INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE threads ADD COLUMN projected_at REAL",
    "ALTER TABLE threads ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE threads ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE items ADD COLUMN search_text TEXT",
)

# ---------------------------------------------------------------------
# Full-text search (SQLite / FTS5)
#
# Two standalone FTS5 tables, keyed by the content table's ``rowid`` so a
# delete or a revision is an O(1) index operation rather than a scan.
#
# Standalone rather than ``content=''`` external-content tables on purpose:
# an external-content table requires the trigger to hand FTS5 the OLD
# indexed value on every delete, and any row written before the index
# existed has no such value. Getting that wrong does not raise — it
# corrupts the index — so the duplicated text is the price of a
# maintenance path that cannot silently rot.
#
# Sync is by TRIGGER, not by a Python call at each write site, so the index
# stays correct for every path that touches the tables, including the bulk
# ``write_projection`` and the cascade inside ``delete_thread``.
# ---------------------------------------------------------------------

_SQLITE_FTS_SCHEMA = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS item_search USING fts5(
        thread_id UNINDEXED,
        item_id   UNINDEXED,
        turn_id   UNINDEXED,
        kind      UNINDEXED,
        seq       UNINDEXED,
        text
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS thread_search USING fts5(
        thread_id UNINDEXED,
        text
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS item_search_ai AFTER INSERT ON items BEGIN
        INSERT INTO item_search(rowid, thread_id, item_id, turn_id, kind, seq, text)
        VALUES (new.rowid, new.thread_id, new.item_id, new.turn_id, new.kind,
                new.seq, COALESCE(new.search_text, ''));
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS item_search_ad AFTER DELETE ON items BEGIN
        DELETE FROM item_search WHERE rowid = old.rowid;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS item_search_au AFTER UPDATE ON items BEGIN
        DELETE FROM item_search WHERE rowid = old.rowid;
        INSERT INTO item_search(rowid, thread_id, item_id, turn_id, kind, seq, text)
        VALUES (new.rowid, new.thread_id, new.item_id, new.turn_id, new.kind,
                new.seq, COALESCE(new.search_text, ''));
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS thread_search_ai AFTER INSERT ON threads BEGIN
        INSERT INTO thread_search(rowid, thread_id, text)
        VALUES (new.rowid, new.thread_id, COALESCE(new.title, ''));
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS thread_search_ad AFTER DELETE ON threads BEGIN
        DELETE FROM thread_search WHERE rowid = old.rowid;
    END
    """,
    # Guarded: a thread row is rewritten on every recorded item (counters,
    # updated_at), and reindexing an unchanged title on each of those would
    # make the index the dominant cost of a streaming run.
    """
    CREATE TRIGGER IF NOT EXISTS thread_search_au AFTER UPDATE ON threads
    WHEN old.title IS NOT new.title BEGIN
        DELETE FROM thread_search WHERE rowid = old.rowid;
        INSERT INTO thread_search(rowid, thread_id, text)
        VALUES (new.rowid, new.thread_id, COALESCE(new.title, ''));
    END
    """,
)


class SqliteConversationStore(ConversationStore):
    """SQLite store at ``<sessions.db>.domain``.

    One cached connection per file (the ``SqliteSessionStorage`` pattern)
    guarded by a lock, because the recorder writes on every notification and
    per-operation connects would dominate a streaming run's cost.
    """

    def __init__(
        self,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        path: str | None = None,
    ) -> None:
        if path is None:
            path = f"{get_sessions_db_path(project_slug, agent_slug)}.domain"
        self._path = str(path)
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None
        self._fts_ready = False

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        # Zero freed pages rather than leaving deleted transcript bytes
        # readable in the free list. This store holds full conversation
        # content, so "deleted" should mean gone, not unlinked. Cheaper
        # than VACUUM (no whole-file rewrite, no exclusive lock on delete).
        conn.execute("PRAGMA secure_delete = ON")
        for statement in _SQLITE_SCHEMA:
            conn.execute(statement)
        for statement in _SQLITE_MIGRATIONS:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                # Column already present. `CREATE TABLE IF NOT EXISTS` is a
                # no-op on an existing table, so columns added after a store
                # was first opened need an explicit ALTER — this is the same
                # additive-migration shape the session-history schema uses.
                pass
        conn.commit()
        self._fts_ready = self._bootstrap_fts(conn)
        self._conn = conn
        return conn

    def _bootstrap_fts(self, conn: sqlite3.Connection) -> bool:
        """Create (and, on an upgrade, populate) the FTS5 index.

        Returns whether full-text search is available. FTS5 is a compile
        time option; a Python built against a SQLite without it must still
        be able to *use* the store, so the failure is recorded here and
        surfaced as a typed error at search time rather than at open time.

        The population order matters. ``search_text`` is backfilled for
        pre-upgrade rows **before** the triggers exist, so the backfill
        does not fire N index writes for rows the index is about to be
        seeded with wholesale; the index is then seeded from the fully
        populated table in one statement.
        """
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('item_search', 'thread_search')"
            ).fetchall()
        }
        fresh = "item_search" not in existing or "thread_search" not in existing
        if fresh:
            self._backfill_search_text(conn)
        try:
            for statement in _SQLITE_FTS_SCHEMA:
                conn.execute(statement)
        except sqlite3.OperationalError:
            logger.warning(
                "SQLite build has no FTS5 module; conversation search is "
                "unavailable for %s",
                self._path,
            )
            conn.rollback()
            return False
        if fresh:
            conn.execute("DELETE FROM item_search")
            conn.execute("DELETE FROM thread_search")
            conn.execute(
                "INSERT INTO item_search"
                "(rowid, thread_id, item_id, turn_id, kind, seq, text) "
                "SELECT rowid, thread_id, item_id, turn_id, kind, seq, "
                "COALESCE(search_text, '') FROM items"
            )
            conn.execute(
                "INSERT INTO thread_search(rowid, thread_id, text) "
                "SELECT rowid, thread_id, COALESCE(title, '') FROM threads"
            )
        conn.commit()
        return True

    @staticmethod
    def _backfill_search_text(conn: sqlite3.Connection) -> None:
        """Compute ``search_text`` for rows written before the column existed.

        ``NULL`` means "never computed" — an item whose content yields no
        searchable text stores ``''`` — so this is exactly the set of
        pre-upgrade rows and re-running it is a no-op.
        """
        rows = conn.execute(
            "SELECT rowid, kind, content_json FROM items " "WHERE search_text IS NULL"
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "UPDATE items SET search_text = ? WHERE rowid = ?",
            [
                (derive_search_text(kind, _loads(content)), rowid)
                for rowid, kind, content in rows
            ],
        )
        conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # -- threads -------------------------------------------------------

    def mutate_queue(self, thread_id, update, *, submission_id=None):
        with self._lock:
            conn = self._connect()
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                thread_row = conn.execute(
                    "SELECT status FROM threads WHERE thread_id=?", (thread_id,)
                ).fetchone()
                if thread_row is None:
                    raise KeyError(f"Unknown thread: {thread_id}")
                row = conn.execute(
                    "SELECT state FROM command_inboxes WHERE thread_id=?", (thread_id,)
                ).fetchone()
                state = json.loads(row[0]) if row else {"version": 1, "items": []}
                state["archived"] = thread_row[0] == "archived"
                row = (
                    conn.execute(
                        "SELECT receipt FROM submission_receipts WHERE thread_id=? AND submission_id=?",
                        (thread_id, submission_id),
                    ).fetchone()
                    if submission_id
                    else None
                )
                result, receipt = update(state, _loads(row[0]) if row else None)
                conn.execute(
                    "INSERT INTO command_inboxes VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state",
                    (thread_id, json.dumps(state)),
                )
                if receipt is not None:
                    if submission_id is None:
                        raise ValueError("Receipt requires submission identity")
                    conn.execute(
                        "INSERT INTO submission_receipts VALUES (?,?,?)",
                        (thread_id, submission_id, json.dumps(receipt)),
                    )
                return result

    def submission_receipt(
        self,
        thread_id: str,
        submission_id: str,
        claim: dict | None = None,
        result: dict | None = None,
    ) -> dict | None:
        with self._lock:
            conn = self._connect()
            if claim is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO submission_receipts VALUES (?, ?, ?)",
                    (thread_id, submission_id, _dumps(claim)),
                )
            if result is not None:
                conn.execute(
                    "UPDATE submission_receipts SET receipt = ? WHERE thread_id = ? AND submission_id = ?",
                    (_dumps(result), thread_id, submission_id),
                )
            row = conn.execute(
                "SELECT receipt FROM submission_receipts WHERE thread_id = ? AND submission_id = ?",
                (thread_id, submission_id),
            ).fetchone()
            conn.commit()
            return _loads(row[0]) if row else None

    def get_thread(self, thread_id: str) -> Optional[Thread]:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"SELECT {', '.join(_THREAD_COLUMNS)} FROM threads "
                    "WHERE thread_id = ?",
                    (thread_id,),
                )
                .fetchone()
            )
        return _thread_from_row(row) if row else None

    def upsert_thread(self, thread: Thread) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(_SQLITE_UPSERT_THREAD, _thread_values(thread))
            conn.commit()

    def mutate_thread(self, thread_id, update):
        with self._lock:
            conn = self._connect()
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    f"SELECT {', '.join(_THREAD_COLUMNS)} FROM threads WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if row is None:
                    return None
                thread = _thread_from_row(row)
                update(thread)
                if thread.thread_id != thread_id:
                    raise ValueError("Thread mutation cannot change identity")
                conn.execute(_SQLITE_UPSERT_THREAD, _thread_values(thread))
                return thread

    def delete_thread(self, thread_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "DELETE FROM threads WHERE thread_id = ?", (thread_id,)
            )
            conn.execute("DELETE FROM turns WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM items WHERE thread_id = ?", (thread_id,))
            conn.execute(
                "DELETE FROM command_inboxes WHERE thread_id = ?", (thread_id,)
            )
            conn.execute(
                "DELETE FROM submission_receipts WHERE thread_id = ?", (thread_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _thread_predicate(
        thread_filter: ThreadFilter,
    ) -> Tuple[List[str], List[Any]]:
        """The filter as SQL. Every field lands here — including the scope.

        There is no code path that applies part of this predicate in Python
        after the fact: the listing, the count and the search all build
        their WHERE from this one function, so a page and its ``total``
        cannot disagree and an ownership scope cannot be forgotten at one
        of three call sites.
        """
        clauses: List[str] = ["1 = 1"]
        params: List[Any] = []
        if thread_filter.user_id is not UNSCOPED:
            # ``user_id = ?`` never matches a NULL owner, which is the
            # intended behaviour: an unowned thread is not accessible to a
            # named principal (see rpc.auth.principal_can_access).
            clauses.append("threads.user_id = ?")
            params.append(thread_filter.user_id)
        if thread_filter.status is not None:
            clauses.append("threads.status = ?")
            params.append(thread_filter.status.value)
        if thread_filter.pinned is not None:
            clauses.append("threads.pinned = ?")
            params.append(1 if thread_filter.pinned else 0)
        if thread_filter.source is not None:
            clauses.append("threads.source = ?")
            params.append(thread_filter.source)
        else:
            clauses.append("threads.source <> 'worker'")
        if thread_filter.parent_thread_id is not None:
            clauses.append("threads.parent_thread_id = ?")
            params.append(thread_filter.parent_thread_id)
        if thread_filter.created_after is not None:
            clauses.append("threads.created_at >= ?")
            params.append(float(thread_filter.created_after))
        if thread_filter.created_before is not None:
            clauses.append("threads.created_at < ?")
            params.append(float(thread_filter.created_before))
        if thread_filter.updated_after is not None:
            clauses.append("threads.updated_at >= ?")
            params.append(float(thread_filter.updated_after))
        if thread_filter.updated_before is not None:
            clauses.append("threads.updated_at < ?")
            params.append(float(thread_filter.updated_before))
        if thread_filter.model is not None:
            # The model is a property of a Turn, not of a Thread, and
            # denormalizing it onto the thread row would add a write-path
            # field that can go stale. An EXISTS keeps one source of truth.
            clauses.append(
                "EXISTS (SELECT 1 FROM turns WHERE turns.thread_id = "
                "threads.thread_id AND turns.model = ?)"
            )
            params.append(thread_filter.model)
        return clauses, params

    def list_threads(
        self,
        thread_filter: ThreadFilter,
        *,
        limit: int,
        after: Optional[Tuple[float, str]] = None,
        descending: bool = True,
    ) -> List[Thread]:
        clauses, params = self._thread_predicate(thread_filter)
        if after is not None:
            sort_value, thread_id = after
            comparison = "<" if descending else ">"
            clauses.append(
                f"(threads.updated_at {comparison} ? OR "
                f"(threads.updated_at = ? AND threads.thread_id {comparison} ?))"
            )
            params.extend([float(sort_value), float(sort_value), thread_id])
        order = "DESC" if descending else "ASC"
        params.append(limit)
        columns = ", ".join(f"threads.{column}" for column in _THREAD_COLUMNS)
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    f"SELECT {columns} FROM threads WHERE {' AND '.join(clauses)} "
                    f"ORDER BY threads.updated_at {order}, threads.thread_id {order} "
                    "LIMIT ?",
                    params,
                )
                .fetchall()
            )
        return [_thread_from_row(row) for row in rows]

    def count_threads(self, thread_filter: ThreadFilter) -> int:
        clauses, params = self._thread_predicate(thread_filter)
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"SELECT COUNT(*) FROM threads WHERE {' AND '.join(clauses)}",
                    params,
                )
                .fetchone()
            )
        return int(row[0]) if row else 0

    def get_threads(self, thread_ids: Sequence[str]) -> Dict[str, Thread]:
        ids = list(dict.fromkeys(thread_ids))
        if not ids:
            return {}
        columns = ", ".join(_THREAD_COLUMNS)
        found: Dict[str, Thread] = {}
        with self._lock:
            conn = self._connect()
            for start in range(0, len(ids), _COUNT_BATCH):
                chunk = ids[start : start + _COUNT_BATCH]
                placeholders = ", ".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT {columns} FROM threads "
                    f"WHERE thread_id IN ({placeholders})",
                    chunk,
                ).fetchall():
                    thread = _thread_from_row(row)
                    found[thread.thread_id] = thread
        return found

    def compact_search_index(self) -> None:
        if not self._fts_ready:
            self._connect()
        if not self._fts_ready:
            return
        with self._lock:
            conn = self._connect()
            for table in ("item_search", "thread_search"):
                try:
                    conn.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
                except sqlite3.OperationalError:  # pragma: no cover - defensive
                    logger.exception("FTS optimize failed for %s", table)
            conn.commit()

    def _count_for_threads(
        self, table: str, thread_ids: Sequence[str]
    ) -> Dict[str, int]:
        ids = list(dict.fromkeys(thread_ids))
        if not ids:
            return {}
        counts: Dict[str, int] = {thread_id: 0 for thread_id in ids}
        with self._lock:
            conn = self._connect()
            for start in range(0, len(ids), _COUNT_BATCH):
                chunk = ids[start : start + _COUNT_BATCH]
                placeholders = ", ".join("?" * len(chunk))
                for thread_id, total in conn.execute(
                    f"SELECT thread_id, COUNT(*) FROM {table} "
                    f"WHERE thread_id IN ({placeholders}) GROUP BY thread_id",
                    chunk,
                ).fetchall():
                    counts[thread_id] = int(total)
        return counts

    def count_items_for_threads(self, thread_ids: Sequence[str]) -> Dict[str, int]:
        return self._count_for_threads("items", thread_ids)

    def count_turns_for_threads(self, thread_ids: Sequence[str]) -> Dict[str, int]:
        return self._count_for_threads("turns", thread_ids)

    def descendant_thread_rows(
        self, root_thread_id: str
    ) -> List[Tuple[str, Optional[str], Optional[str]]]:
        # The recursive row is the thread id ALONE, so ``UNION`` dedupes on
        # it and a parent_thread_id cycle terminates without needing a
        # depth ceiling to stop it. Depth is derived from the returned
        # parent links in Python (ConversationService.descendant_nodes),
        # which keeps this the only walk and lets the same rows serve the
        # capped read and the uncapped guard.
        sql = """
            WITH RECURSIVE subtree(thread_id) AS (
                SELECT thread_id FROM threads
                 WHERE parent_thread_id = ? AND thread_id <> ?
                UNION
                SELECT c.thread_id
                  FROM threads c
                  JOIN subtree s ON c.parent_thread_id = s.thread_id
                 WHERE c.thread_id <> ?
            )
            SELECT t.thread_id, t.user_id, t.parent_thread_id
              FROM subtree s JOIN threads t ON t.thread_id = s.thread_id
             ORDER BY t.thread_id ASC
        """
        with self._lock:
            rows = (
                self._connect()
                .execute(sql, (root_thread_id, root_thread_id, root_thread_id))
                .fetchall()
            )
        return [(row[0], row[1], row[2]) for row in rows]

    def search(
        self,
        query: str,
        thread_filter: ThreadFilter,
        *,
        limit: int,
        offset: int = 0,
    ) -> Tuple[List[SearchHit], int]:
        if not self._fts_ready:
            self._connect()
        if not self._fts_ready:
            raise SearchUnavailable(
                "This SQLite build has no FTS5 module; conversation search "
                "is unavailable."
            )
        terms = search_terms(query)
        if not terms:
            return [], 0
        # Each term is re-quoted as an FTS5 string literal (doubling any
        # embedded quote), so no character a user types is ever parsed as
        # FTS5 syntax. Adjacent quoted strings are an implicit AND.
        match = " ".join('"' + term.replace('"', '""') + '"' for term in terms)
        clauses, params = self._thread_predicate(thread_filter)
        matches_sql = f"""
            WITH matches AS (
                SELECT m.thread_id AS thread_id,
                       m.item_id   AS item_id,
                       m.turn_id   AS turn_id,
                       m.kind      AS match_kind,
                       m.seq       AS seq,
                       m.preview   AS preview,
                       m.rank_value AS rank_value
                  FROM (
                        SELECT thread_id, item_id, turn_id, kind, seq,
                               snippet(item_search, 5, '', '', '…', 12) AS preview,
                               bm25(item_search) AS rank_value
                          FROM item_search WHERE item_search MATCH ?
                        UNION ALL
                        SELECT thread_id, NULL, NULL, 'thread_title', NULL,
                               snippet(thread_search, 1, '', '', '…', 12),
                               bm25(thread_search)
                          FROM thread_search WHERE thread_search MATCH ?
                       ) m
                  JOIN threads ON threads.thread_id = m.thread_id
                 WHERE {' AND '.join(clauses)}
            )
        """
        page_sql = matches_sql + """
            SELECT thread_id, COUNT(*) AS match_count, MIN(rank_value) AS best
              FROM matches GROUP BY thread_id
             ORDER BY best ASC, thread_id ASC LIMIT ? OFFSET ?
            """
        total_sql = matches_sql + "SELECT COUNT(DISTINCT thread_id) FROM matches"
        match_params = [match, match, *params]
        with self._lock:
            conn = self._connect()
            grouped = conn.execute(
                page_sql, [*match_params, limit, max(0, offset)]
            ).fetchall()
            total_row = conn.execute(total_sql, match_params).fetchone()
            total = int(total_row[0]) if total_row else 0
            if not grouped:
                return [], total
            thread_ids = [row[0] for row in grouped]
            placeholders = ", ".join("?" * len(thread_ids))
            best_rows = conn.execute(
                matches_sql
                + "SELECT thread_id, item_id, turn_id, match_kind, seq, preview, "
                "rank_value FROM matches "
                f"WHERE thread_id IN ({placeholders}) "
                "ORDER BY thread_id ASC, rank_value ASC",
                [*match_params, *thread_ids],
            ).fetchall()
        best_by_thread: Dict[str, Sequence[Any]] = {}
        for row in best_rows:
            best_by_thread.setdefault(row[0], row)
        hits: List[SearchHit] = []
        for thread_id, match_count, _best in grouped:
            best = best_by_thread.get(thread_id)
            if best is None:  # pragma: no cover - grouped implies a member
                continue
            hits.append(
                SearchHit(
                    thread_id=thread_id,
                    match_count=int(match_count),
                    match_kind=str(best[3]),
                    item_id=best[1],
                    turn_id=best[2],
                    seq=int(best[4]) if best[4] is not None else None,
                    preview=str(best[5] or ""),
                )
            )
        return hits, total

    def allocate_seq(self, thread_id: str, count: int = 1) -> int:
        if count < 1:
            raise ValueError("count must be >= 1")
        with self._lock:
            conn = self._connect()
            # Single UPDATE ... RETURNING: the bump and the read are one
            # statement, so two writers cannot observe the same high-water
            # mark. Requires SQLite 3.35+ (Python 3.10's bundled sqlite is
            # 3.37+); the fallback keeps older builds correct because the
            # whole block already runs under the store lock.
            try:
                row = conn.execute(
                    "UPDATE threads SET last_seq = last_seq + ? "
                    "WHERE thread_id = ? RETURNING last_seq",
                    (count, thread_id),
                ).fetchone()
            except sqlite3.OperationalError:
                conn.execute(
                    "UPDATE threads SET last_seq = last_seq + ? WHERE thread_id = ?",
                    (count, thread_id),
                )
                row = conn.execute(
                    "SELECT last_seq FROM threads WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyError(f"Unknown thread: {thread_id}")
            conn.commit()
            return int(row[0]) - count + 1

    # -- turns ---------------------------------------------------------

    def get_turn(self, thread_id: str, turn_id: str) -> Optional[Turn]:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"SELECT {', '.join(_TURN_COLUMNS)} FROM turns "
                    "WHERE thread_id = ? AND turn_id = ?",
                    (thread_id, turn_id),
                )
                .fetchone()
            )
        return _turn_from_row(row) if row else None

    def upsert_turn(self, turn: Turn) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(_SQLITE_UPSERT_TURN, _turn_values(turn))
            conn.commit()

    def mutate_turn(self, thread_id, turn_id, update, *, initial_item=None):
        with self._lock:
            conn = self._connect()
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    f"SELECT {', '.join(_THREAD_COLUMNS)} FROM threads WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown thread: {thread_id}")
                thread = _thread_from_row(row)
                row = conn.execute(
                    f"SELECT {', '.join(_TURN_COLUMNS)} FROM turns WHERE thread_id = ? AND turn_id = ?",
                    (thread_id, turn_id),
                ).fetchone()
                ordinal = conn.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM turns WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()[0]
                turn = update(
                    thread, _turn_from_row(row) if row else None, int(ordinal)
                )
                if turn is not None:
                    if (
                        turn.thread_id != thread_id
                        or turn.turn_id != turn_id
                        or thread.thread_id != thread_id
                    ):
                        raise ValueError("Turn mutation cannot change identity")
                    if initial_item is not None:
                        exists = conn.execute(
                            "SELECT 1 FROM items WHERE thread_id = ? AND item_id = ?",
                            (thread_id, initial_item.item_id),
                        ).fetchone()
                        if not exists:
                            item = _attach_initial_item(thread, turn, initial_item)
                            conn.execute(_SQLITE_UPSERT_ITEM, _item_values(item))
                    conn.execute(_SQLITE_UPSERT_TURN, _turn_values(turn))
                    conn.execute(_SQLITE_UPSERT_THREAD, _thread_values(thread))
                return turn

    def list_turns(
        self,
        thread_id: str,
        *,
        limit: int,
        after_ordinal: Optional[int] = None,
        before_ordinal: Optional[int] = None,
        descending: bool = False,
    ) -> List[Turn]:
        clauses = ["thread_id = ?"]
        params: List[Any] = [thread_id]
        if after_ordinal is not None:
            clauses.append("ordinal > ?")
            params.append(after_ordinal)
        if before_ordinal is not None:
            clauses.append("ordinal < ?")
            params.append(before_ordinal)
        params.append(limit)
        order = "DESC" if descending else "ASC"
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    f"SELECT {', '.join(_TURN_COLUMNS)} FROM turns "
                    f"WHERE {' AND '.join(clauses)} "
                    f"ORDER BY ordinal {order} LIMIT ?",
                    params,
                )
                .fetchall()
            )
        return [_turn_from_row(row) for row in rows]

    def count_turns(self, thread_id: str) -> int:
        with self._lock:
            row = (
                self._connect()
                .execute("SELECT COUNT(*) FROM turns WHERE thread_id = ?", (thread_id,))
                .fetchone()
            )
        return int(row[0]) if row else 0

    def max_turn_ordinal(self, thread_id: str) -> int:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    "SELECT COALESCE(MAX(ordinal), 0) FROM turns WHERE thread_id = ?",
                    (thread_id,),
                )
                .fetchone()
            )
        return int(row[0]) if row else 0

    # -- items ---------------------------------------------------------

    def get_item(self, thread_id: str, item_id: str) -> Optional[Item]:
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items "
                    "WHERE thread_id = ? AND item_id = ?",
                    (thread_id, item_id),
                )
                .fetchone()
            )
        return _item_from_row(row) if row else None

    def upsert_item(self, item: Item) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(_SQLITE_UPSERT_ITEM, _item_values(item))
            conn.commit()

    def append_item(self, item: Item) -> Item:
        with self._lock:
            conn = self._connect()
            # Protect the identity check across independent store instances,
            # not merely callers sharing this object's Python lock.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items "
                    "WHERE thread_id = ? AND item_id = ?",
                    (item.thread_id, item.item_id),
                ).fetchone()
                if row is not None:
                    return _item_from_row(row)
                row = conn.execute(
                    "UPDATE threads SET last_seq = last_seq + 1, "
                    "item_count = item_count + 1, updated_at = ? "
                    "WHERE thread_id = ? RETURNING last_seq",
                    (item.updated_at, item.thread_id),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown thread: {item.thread_id}")
                created = replace(item, seq=int(row[0]))
                conn.execute(_SQLITE_UPSERT_ITEM, _item_values(created))
                if created.turn_id:
                    conn.execute(
                        "UPDATE turns SET item_count = item_count + 1, "
                        "first_seq = CASE WHEN first_seq IS NULL THEN ? ELSE MIN(first_seq, ?) END, "
                        "last_seq = CASE WHEN last_seq IS NULL THEN ? ELSE MAX(last_seq, ?) END, "
                        "updated_at = ? WHERE thread_id = ? AND turn_id = ?",
                        (
                            created.seq,
                            created.seq,
                            created.seq,
                            created.seq,
                            created.updated_at,
                            created.thread_id,
                            created.turn_id,
                        ),
                    )
            return created

    def mutate_item(self, thread_id, item_id, update):
        with self._lock:
            conn = self._connect()
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items WHERE thread_id = ? AND item_id = ?",
                    (thread_id, item_id),
                ).fetchone()
                if row is None:
                    return None
                existing = _item_from_row(row)
                identity = (thread_id, item_id, existing.seq, existing.turn_id)
                updated = update(existing)
                if (
                    updated.thread_id,
                    updated.item_id,
                    updated.seq,
                    updated.turn_id,
                ) != identity:
                    raise ValueError("Item mutation cannot change identity or position")
                conn.execute(_SQLITE_UPSERT_ITEM, _item_values(updated))
                return updated

    def _item_predicate(
        self,
        thread_id: str,
        turn_id: Optional[str],
        kinds: Optional[Sequence[ItemKind]],
        after_seq: Optional[int],
        before_seq: Optional[int],
    ) -> Tuple[List[str], List[Any]]:
        clauses = ["thread_id = ?"]
        params: List[Any] = [thread_id]
        if turn_id is not None:
            clauses.append("turn_id = ?")
            params.append(turn_id)
        if kinds:
            placeholders = ", ".join("?" * len(kinds))
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kind.value for kind in kinds)
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(after_seq)
        if before_seq is not None:
            clauses.append("seq < ?")
            params.append(before_seq)
        return clauses, params

    def list_items(
        self,
        thread_id: str,
        *,
        limit: int,
        turn_id: Optional[str] = None,
        kinds: Optional[Sequence[ItemKind]] = None,
        after_seq: Optional[int] = None,
        before_seq: Optional[int] = None,
        descending: bool = False,
    ) -> List[Item]:
        clauses, params = self._item_predicate(
            thread_id, turn_id, kinds, after_seq, before_seq
        )
        params.append(limit)
        order = "DESC" if descending else "ASC"
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items "
                    f"WHERE {' AND '.join(clauses)} "
                    f"ORDER BY seq {order} LIMIT ?",
                    params,
                )
                .fetchall()
            )
        return [_item_from_row(row) for row in rows]

    def read_snapshot(self, thread_id: str) -> dict:
        with self._lock:
            conn = self._connect()
            with conn:
                conn.execute("BEGIN")
                rows = conn.execute(
                    f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items WHERE thread_id=? ORDER BY seq",
                    (thread_id,),
                ).fetchall()
                inbox = conn.execute(
                    "SELECT state FROM command_inboxes WHERE thread_id=?", (thread_id,)
                ).fetchone()
        return {
            "items": [_item_from_row(row) for row in rows],
            "inbox": _loads(inbox[0]) if inbox else {"version": 1, "items": []},
        }

    def count_items(
        self,
        thread_id: str,
        *,
        turn_id: Optional[str] = None,
        kinds: Optional[Sequence[ItemKind]] = None,
    ) -> int:
        clauses, params = self._item_predicate(thread_id, turn_id, kinds, None, None)
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"SELECT COUNT(*) FROM items WHERE {' AND '.join(clauses)}", params
                )
                .fetchone()
            )
        return int(row[0]) if row else 0

    def open_items(
        self, thread_id: str, *, turn_id: Optional[str] = None
    ) -> List[Item]:
        clauses = ["thread_id = ?"]
        params: List[Any] = [thread_id]
        if turn_id is not None:
            clauses.append("turn_id = ?")
            params.append(turn_id)
        placeholders = ", ".join("?" * len(_TERMINAL_STATUS_VALUES))
        clauses.append(f"status NOT IN ({placeholders})")
        params.extend(_TERMINAL_STATUS_VALUES)
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items "
                    f"WHERE {' AND '.join(clauses)} ORDER BY seq ASC",
                    params,
                )
                .fetchall()
            )
        return [_item_from_row(row) for row in rows]

    def write_projection(
        self, thread: Thread, turns: Iterable[Turn], items: Iterable[Item]
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(_SQLITE_UPSERT_THREAD, _thread_values(thread))
                conn.executemany(
                    _SQLITE_UPSERT_TURN, [_turn_values(turn) for turn in turns]
                )
                conn.executemany(
                    _SQLITE_UPSERT_ITEM, [_item_values(item) for item in items]
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise


# ---------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------

DEFAULT_TENANT = "local"

#: Postgres column names in ``_THREAD_COLUMNS`` order. The two schemas
#: differ only in the JSON columns' names (``usage``/``compaction``/
#: ``metadata`` rather than ``*_json``), and ``_thread_from_row`` decodes
#: by POSITION, so the orders must be kept identical — this mapping is what
#: makes one decoder correct for both backends.
_PG_THREAD_FIELD_NAMES = tuple(
    {
        "usage_json": "usage",
        "compaction_json": "compaction",
        "metadata_json": "metadata",
    }.get(column, column)
    for column in _THREAD_COLUMNS
)

#: ``ts_headline`` has no way to emit an unmarked fragment, so the markers
#: are chosen to be unambiguous and stripped here. The wire preview is then
#: plain text on both backends — a client rendering a SQLite preview and a
#: Postgres one must not have to know which produced it.
_HEADLINE_START = "<<"
_HEADLINE_STOP = ">>"


def _strip_headline_markers(text: str) -> str:
    return text.replace(_HEADLINE_START, "").replace(_HEADLINE_STOP, "")


_PG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_inboxes (tenant_id TEXT NOT NULL, project_slug TEXT NOT NULL, agent_slug TEXT NOT NULL, thread_id TEXT NOT NULL, state JSONB NOT NULL, PRIMARY KEY(tenant_id, project_slug, agent_slug, thread_id));
CREATE TABLE IF NOT EXISTS conversation_submissions (tenant_id TEXT NOT NULL, project_slug TEXT NOT NULL, agent_slug TEXT NOT NULL, thread_id TEXT NOT NULL, submission_id TEXT NOT NULL, receipt JSONB NOT NULL, PRIMARY KEY(tenant_id, project_slug, agent_slug, thread_id, submission_id));
CREATE TABLE IF NOT EXISTS conversation_threads (
    tenant_id             TEXT   NOT NULL,
    project_slug          TEXT   NOT NULL,
    agent_slug            TEXT   NOT NULL,
    thread_id             TEXT   NOT NULL,
    user_id               TEXT,
    status                TEXT   NOT NULL DEFAULT 'active',
    title                 TEXT,
    created_at            DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at            DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_seq              BIGINT NOT NULL DEFAULT 0,
    turn_count            BIGINT NOT NULL DEFAULT 0,
    item_count            BIGINT NOT NULL DEFAULT 0,
    parent_thread_id      TEXT,
    branched_from_item_id TEXT,
    usage                 JSONB  NOT NULL DEFAULT '{}'::jsonb,
    compaction            JSONB  NOT NULL DEFAULT '{}'::jsonb,
    source                TEXT   NOT NULL DEFAULT 'recorded',
    projected_at          DOUBLE PRECISION,
    schema_version        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, project_slug, agent_slug, thread_id)
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    tenant_id      TEXT   NOT NULL,
    project_slug   TEXT   NOT NULL,
    agent_slug     TEXT   NOT NULL,
    thread_id      TEXT   NOT NULL,
    turn_id        TEXT   NOT NULL,
    ordinal        BIGINT NOT NULL,
    status         TEXT   NOT NULL DEFAULT 'running',
    created_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    completed_at   DOUBLE PRECISION,
    prompt         TEXT,
    prompt_role    TEXT,
    end_reason     TEXT,
    error          JSONB,
    usage          JSONB  NOT NULL DEFAULT '{}'::jsonb,
    model          TEXT,
    model_ref      TEXT,
    item_count     BIGINT NOT NULL DEFAULT 0,
    first_seq      BIGINT,
    last_seq       BIGINT,
    attempts       BIGINT NOT NULL DEFAULT 0,
    source         TEXT   NOT NULL DEFAULT 'recorded',
    schema_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, project_slug, agent_slug, thread_id, turn_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_ordinal
    ON conversation_turns (tenant_id, project_slug, agent_slug, thread_id, ordinal);

CREATE TABLE IF NOT EXISTS conversation_items (
    tenant_id      TEXT   NOT NULL,
    project_slug   TEXT   NOT NULL,
    agent_slug     TEXT   NOT NULL,
    thread_id      TEXT   NOT NULL,
    item_id        TEXT   NOT NULL,
    turn_id        TEXT,
    seq            BIGINT NOT NULL,
    kind           TEXT   NOT NULL,
    status         TEXT   NOT NULL DEFAULT 'started',
    role           TEXT,
    created_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    completed_at   DOUBLE PRECISION,
    revision       BIGINT NOT NULL DEFAULT 0,
    content        JSONB  NOT NULL DEFAULT '{}'::jsonb,
    source_ref     JSONB  NOT NULL DEFAULT '{}'::jsonb,
    long_lived     BOOLEAN NOT NULL DEFAULT false,
    source         TEXT   NOT NULL DEFAULT 'recorded',
    schema_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, project_slug, agent_slug, thread_id, item_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_items_seq
    ON conversation_items (tenant_id, project_slug, agent_slug, thread_id, seq);

CREATE INDEX IF NOT EXISTS idx_conversation_items_turn
    ON conversation_items
       (tenant_id, project_slug, agent_slug, thread_id, turn_id, seq);

ALTER TABLE conversation_items
    ADD COLUMN IF NOT EXISTS long_lived BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS projected_at DOUBLE PRECISION;

ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Full-text search. ``search_text`` is written by the store (the same
-- derivation SQLite indexes, so a query matches identically on both
-- backends); the tsvector is GENERATED from it so the index can never
-- disagree with the column it summarizes — there is no trigger to forget
-- and no second write path to keep in step.
--
-- The 'simple' configuration is deliberate: it does no stemming, which is
-- what SQLite's default FTS5 tokenizer also does. A stemming config here
-- would make the same query return different rows on the two backends.
--
-- The regexp_replace is the OTHER half of that agreement. Postgres' text
-- parser recognizes compound tokens — `widgets.py` is one `file` lexeme,
-- `a@b.com` one `email` — so a search for `widgets` matched on SQLite
-- (whose unicode61 tokenizer splits on every non-alphanumeric) and missed
-- on Postgres. Flattening punctuation to whitespace before tokenizing
-- gives both backends the same token stream, and ``search_terms`` splits
-- the query the same way.
ALTER TABLE conversation_items
    ADD COLUMN IF NOT EXISTS search_text TEXT;

ALTER TABLE conversation_items
    ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            regexp_replace(coalesce(search_text, ''), '[^[:alnum:]]+', ' ', 'g')
        )
    ) STORED;

ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS title_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            regexp_replace(coalesce(title, ''), '[^[:alnum:]]+', ' ', 'g')
        )
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_conversation_items_search
    ON conversation_items USING GIN (search_tsv);

CREATE INDEX IF NOT EXISTS idx_conversation_threads_title_search
    ON conversation_threads USING GIN (title_tsv);

CREATE INDEX IF NOT EXISTS idx_conversation_threads_listing
    ON conversation_threads
       (tenant_id, project_slug, agent_slug, user_id, status,
        updated_at, thread_id);

CREATE INDEX IF NOT EXISTS idx_conversation_threads_parent
    ON conversation_threads
       (tenant_id, project_slug, agent_slug, parent_thread_id);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_model
    ON conversation_turns (tenant_id, project_slug, agent_slug, model, thread_id);

ALTER TABLE conversation_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_turns   ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_items   ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_inboxes ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target TEXT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'conversation_threads', 'conversation_turns', 'conversation_items', 'conversation_inboxes'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE schemaname = current_schema()
            AND tablename = target AND policyname = 'tenant_isolation'
        ) THEN
            EXECUTE format(
                'CREATE POLICY tenant_isolation ON %I '
                'USING (tenant_id = current_setting(''app.current_tenant'', true))',
                target
            );
        END IF;
    END LOOP;
END $$;
"""


class PgConversationStore(ConversationStore):
    """Postgres store, tenant- and shard-scoped like ``PgSessionStorage``."""

    def __init__(
        self,
        dsn: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        tenant_id: str | None = None,
        min_size: int = 1,
        max_size: int = 8,
    ) -> None:
        try:
            from psycopg.types.json import Jsonb  # noqa: F401 — used in queries
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "PgConversationStore requires the 'session-postgres' extra. "
                "Install with: pip install 'omniagents[session-postgres]'"
            ) from exc

        self._tenant_id = (
            tenant_id or os.getenv("OMNIAGENTS_TENANT_ID") or DEFAULT_TENANT
        )
        self._project = project_slug or "_"
        self._agent = agent_slug or "_"
        self._pool = ConnectionPool(
            conninfo=dsn, min_size=min_size, max_size=max_size, open=True
        )
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_PG_SCHEMA_SQL)
            conn.commit()
        self._backfill_search_text()

    def _backfill_search_text(self) -> None:
        """Populate ``search_text`` for rows written before the column existed.

        Without this every transcript a deployment already held — which, on
        the Postgres/cloud topology, is all of them — indexes as empty and
        the search box returns nothing for the entire existing history. The
        SQLite store has always done this; the generated ``tsvector`` made
        it look unnecessary here, but a GENERATED column can only summarize
        a column that has been filled in.

        ``NULL`` means "never computed" — an item whose content yields no
        searchable text stores ``''`` — so this is exactly the pre-upgrade
        set and re-running it is a no-op. Paged, and guarded by a cheap
        existence probe so a caught-up deployment pays one indexed lookup
        per store open, not a scan.
        """
        from psycopg.types.json import Jsonb  # noqa: F401 - parity import

        migrated = 0
        while True:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT thread_id, item_id, kind, content "
                        "FROM conversation_items "
                        f"WHERE {self._SHARD_SCOPE} AND search_text IS NULL "
                        "LIMIT %s",
                        (*self._scope, _PG_BACKFILL_BATCH),
                    )
                    rows = cur.fetchall()
                    if not rows:
                        return
                    for thread_id, item_id, kind, content in rows:
                        cur.execute(
                            "UPDATE conversation_items SET search_text = %s "
                            f"WHERE {self._THREAD_SCOPE} AND item_id = %s",
                            (
                                derive_search_text(kind, _loads(content)),
                                *self._scope,
                                thread_id,
                                item_id,
                            ),
                        )
                conn.commit()
            migrated += len(rows)
            logger.info("conversation search backfill: %s item(s) indexed", migrated)

    def close(self) -> None:
        try:
            self._pool.close()
        except Exception:  # pragma: no cover - shutdown best effort
            pass

    @property
    def _scope(self) -> Tuple[str, str, str]:
        return (self._tenant_id, self._project, self._agent)

    def _conn(self, *, snapshot=False):
        cm = self._pool.connection()
        conn = cm.__enter__()
        try:
            with conn.cursor() as cur:
                if snapshot:
                    cur.execute(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                    )
                cur.execute(
                    "SELECT set_config('app.current_tenant', %s, true)",
                    (self._tenant_id,),
                )
        except Exception:
            cm.__exit__(None, None, None)
            raise
        return _PgConn(cm, conn)

    # -- threads -------------------------------------------------------

    #: The column list, in ``_THREAD_COLUMNS`` order, so ``_thread_from_row``
    #: decodes a PG row exactly as it decodes a SQLite one.
    _THREAD_SELECT = (
        f"SELECT {', '.join(_PG_THREAD_FIELD_NAMES)} FROM conversation_threads"
    )
    _THREAD_SCOPE = (
        "tenant_id = %s AND project_slug = %s AND agent_slug = %s AND thread_id = %s"
    )
    #: The shard predicate without the per-thread term, for listings.
    _SHARD_SCOPE = "tenant_id = %s AND project_slug = %s AND agent_slug = %s"

    def mutate_queue(self, thread_id, update, *, submission_id=None):
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            with conn.cursor() as cur:
                scope = (*self._scope, thread_id)
                cur.execute(
                    f"{self._THREAD_SELECT} WHERE {self._THREAD_SCOPE} FOR UPDATE",
                    scope,
                )
                thread_row = cur.fetchone()
                if thread_row is None:
                    raise KeyError(f"Unknown thread: {thread_id}")
                cur.execute(
                    f"SELECT state FROM conversation_inboxes WHERE {self._THREAD_SCOPE}",
                    scope,
                )
                row = cur.fetchone()
                state = row[0] if row else {"version": 1, "items": []}
                state["archived"] = (
                    _thread_from_row(thread_row).status is ThreadStatus.ARCHIVED
                )
                receipt = None
                if submission_id:
                    cur.execute(
                        f"SELECT receipt FROM conversation_submissions WHERE {self._THREAD_SCOPE} AND submission_id=%s FOR UPDATE",
                        (*scope, submission_id),
                    )
                    row = cur.fetchone()
                    receipt = row[0] if row else None
                result, receipt = update(state, receipt)
                cur.execute(
                    "INSERT INTO conversation_inboxes VALUES (%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,project_slug,agent_slug,thread_id) DO UPDATE SET state=excluded.state",
                    (*scope, Jsonb(state)),
                )
                if receipt is not None:
                    if submission_id is None:
                        raise ValueError("Receipt requires submission identity")
                    cur.execute(
                        "INSERT INTO conversation_submissions VALUES (%s,%s,%s,%s,%s,%s)",
                        (*scope, submission_id, Jsonb(receipt)),
                    )
            conn.commit()
            return result

    def submission_receipt(
        self,
        thread_id: str,
        submission_id: str,
        claim: dict | None = None,
        result: dict | None = None,
    ) -> dict | None:
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            with conn.cursor() as cur:
                scope = (*self._scope, thread_id, submission_id)
                if claim is not None:
                    cur.execute(
                        "INSERT INTO conversation_submissions VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (*scope, Jsonb(claim)),
                    )
                where = self._THREAD_SCOPE + " AND submission_id = %s"
                if result is not None:
                    cur.execute(
                        f"UPDATE conversation_submissions SET receipt = %s WHERE {where}",
                        (Jsonb(result), *scope),
                    )
                cur.execute(
                    f"SELECT receipt FROM conversation_submissions WHERE {where}", scope
                )
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else None

    def get_thread(self, thread_id: str) -> Optional[Thread]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._THREAD_SELECT} WHERE {self._THREAD_SCOPE}",
                    (*self._scope, thread_id),
                )
                row = cur.fetchone()
        return _thread_from_row(row) if row else None

    def upsert_thread(self, thread: Thread) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                self._upsert_thread(cur, thread)
            conn.commit()

    def mutate_thread(self, thread_id, update):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._THREAD_SELECT} WHERE {self._THREAD_SCOPE} FOR UPDATE",
                    (*self._scope, thread_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                thread = _thread_from_row(row)
                update(thread)
                if thread.thread_id != thread_id:
                    raise ValueError("Thread mutation cannot change identity")
                self._upsert_thread(cur, thread)
            conn.commit()
            return thread

    def _upsert_thread(self, cur: Any, thread: Thread) -> None:
        from psycopg.types.json import Jsonb

        cur.execute(
            """
            INSERT INTO conversation_threads (
                tenant_id, project_slug, agent_slug, thread_id, user_id, status,
                title, created_at, updated_at, last_seq, turn_count, item_count,
                parent_thread_id, branched_from_item_id, usage, compaction,
                source, projected_at, schema_version, pinned, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, project_slug, agent_slug, thread_id)
            DO UPDATE SET
                user_id = EXCLUDED.user_id,
                status = EXCLUDED.status,
                title = EXCLUDED.title,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                last_seq = GREATEST(
                    conversation_threads.last_seq, EXCLUDED.last_seq
                ),
                turn_count = EXCLUDED.turn_count,
                item_count = EXCLUDED.item_count,
                parent_thread_id = EXCLUDED.parent_thread_id,
                branched_from_item_id = EXCLUDED.branched_from_item_id,
                usage = EXCLUDED.usage,
                compaction = EXCLUDED.compaction,
                source = EXCLUDED.source,
                projected_at = COALESCE(
                    conversation_threads.projected_at, EXCLUDED.projected_at
                ),
                schema_version = EXCLUDED.schema_version,
                pinned = EXCLUDED.pinned,
                metadata = EXCLUDED.metadata
            """,
            (
                *self._scope,
                thread.thread_id,
                thread.user_id,
                thread.status.value,
                thread.title,
                float(thread.created_at),
                float(thread.updated_at),
                int(thread.last_seq),
                int(thread.turn_count),
                int(thread.item_count),
                thread.parent_thread_id,
                thread.branched_from_item_id,
                Jsonb(thread.usage),
                Jsonb(thread.compaction),
                thread.source,
                thread.projected_at,
                int(thread.schema_version),
                bool(thread.pinned),
                Jsonb(thread.metadata),
            ),
        )

    def delete_thread(self, thread_id: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                for table in (
                    "conversation_items",
                    "conversation_turns",
                    "conversation_submissions",
                    "conversation_inboxes",
                ):
                    cur.execute(
                        f"DELETE FROM {table} WHERE {self._THREAD_SCOPE}",
                        (*self._scope, thread_id),
                    )
                cur.execute(
                    f"DELETE FROM conversation_threads WHERE {self._THREAD_SCOPE}",
                    (*self._scope, thread_id),
                )
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def _thread_predicate(
        self, thread_filter: ThreadFilter
    ) -> Tuple[List[str], List[Any]]:
        """The filter as SQL — the Postgres twin of the SQLite version.

        Same contract: every field, ownership scope included, is applied
        here and nowhere else, so the page and its ``total`` are drawn
        under one predicate.
        """
        clauses: List[str] = [
            "conversation_threads.tenant_id = %s "
            "AND conversation_threads.project_slug = %s "
            "AND conversation_threads.agent_slug = %s"
        ]
        params: List[Any] = [*self._scope]
        if thread_filter.user_id is not UNSCOPED:
            clauses.append("conversation_threads.user_id = %s")
            params.append(thread_filter.user_id)
        if thread_filter.status is not None:
            clauses.append("conversation_threads.status = %s")
            params.append(thread_filter.status.value)
        if thread_filter.pinned is not None:
            clauses.append("conversation_threads.pinned = %s")
            params.append(bool(thread_filter.pinned))
        if thread_filter.source is not None:
            clauses.append("conversation_threads.source = %s")
            params.append(thread_filter.source)
        else:
            clauses.append("conversation_threads.source <> 'worker'")
        if thread_filter.parent_thread_id is not None:
            clauses.append("conversation_threads.parent_thread_id = %s")
            params.append(thread_filter.parent_thread_id)
        if thread_filter.created_after is not None:
            clauses.append("conversation_threads.created_at >= %s")
            params.append(float(thread_filter.created_after))
        if thread_filter.created_before is not None:
            clauses.append("conversation_threads.created_at < %s")
            params.append(float(thread_filter.created_before))
        if thread_filter.updated_after is not None:
            clauses.append("conversation_threads.updated_at >= %s")
            params.append(float(thread_filter.updated_after))
        if thread_filter.updated_before is not None:
            clauses.append("conversation_threads.updated_at < %s")
            params.append(float(thread_filter.updated_before))
        if thread_filter.model is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM conversation_turns ct "
                "WHERE ct.tenant_id = conversation_threads.tenant_id "
                "AND ct.project_slug = conversation_threads.project_slug "
                "AND ct.agent_slug = conversation_threads.agent_slug "
                "AND ct.thread_id = conversation_threads.thread_id "
                "AND ct.model = %s)"
            )
            params.append(thread_filter.model)
        return clauses, params

    def list_threads(
        self,
        thread_filter: ThreadFilter,
        *,
        limit: int,
        after: Optional[Tuple[float, str]] = None,
        descending: bool = True,
    ) -> List[Thread]:
        clauses, params = self._thread_predicate(thread_filter)
        if after is not None:
            sort_value, thread_id = after
            comparison = "<" if descending else ">"
            clauses.append(
                f"(conversation_threads.updated_at {comparison} %s OR "
                f"(conversation_threads.updated_at = %s AND "
                f"conversation_threads.thread_id {comparison} %s))"
            )
            params.extend([float(sort_value), float(sort_value), thread_id])
        order = "DESC" if descending else "ASC"
        params.append(limit)
        columns = ", ".join(
            f"conversation_threads.{column}" for column in _PG_THREAD_FIELD_NAMES
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {columns} FROM conversation_threads "
                    f"WHERE {' AND '.join(clauses)} "
                    f"ORDER BY conversation_threads.updated_at {order}, "
                    f"conversation_threads.thread_id {order} LIMIT %s",
                    params,
                )
                rows = cur.fetchall()
        return [_thread_from_row(row) for row in rows]

    def count_threads(self, thread_filter: ThreadFilter) -> int:
        clauses, params = self._thread_predicate(thread_filter)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversation_threads "
                    f"WHERE {' AND '.join(clauses)}",
                    params,
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def get_threads(self, thread_ids: Sequence[str]) -> Dict[str, Thread]:
        ids = list(dict.fromkeys(thread_ids))
        if not ids:
            return {}
        found: Dict[str, Thread] = {}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._THREAD_SELECT} WHERE {self._SHARD_SCOPE} "
                    "AND thread_id = ANY(%s)",
                    (*self._scope, ids),
                )
                for row in cur.fetchall():
                    thread = _thread_from_row(row)
                    found[thread.thread_id] = thread
        return found

    def _count_for_threads(
        self, table: str, thread_ids: Sequence[str]
    ) -> Dict[str, int]:
        ids = list(dict.fromkeys(thread_ids))
        if not ids:
            return {}
        counts: Dict[str, int] = {thread_id: 0 for thread_id in ids}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT thread_id, COUNT(*) FROM {table} "
                    f"WHERE {self._SHARD_SCOPE} AND thread_id = ANY(%s) "
                    "GROUP BY thread_id",
                    (*self._scope, ids),
                )
                for thread_id, total in cur.fetchall():
                    counts[thread_id] = int(total)
        return counts

    def count_items_for_threads(self, thread_ids: Sequence[str]) -> Dict[str, int]:
        return self._count_for_threads("conversation_items", thread_ids)

    def count_turns_for_threads(self, thread_ids: Sequence[str]) -> Dict[str, int]:
        return self._count_for_threads("conversation_turns", thread_ids)

    def descendant_thread_rows(
        self, root_thread_id: str
    ) -> List[Tuple[str, Optional[str], Optional[str]]]:
        sql = f"""
            WITH RECURSIVE subtree(thread_id) AS (
                SELECT thread_id FROM conversation_threads
                 WHERE {self._SHARD_SCOPE}
                   AND parent_thread_id = %s AND thread_id <> %s
                UNION
                SELECT c.thread_id
                  FROM conversation_threads c
                  JOIN subtree s ON c.parent_thread_id = s.thread_id
                 WHERE c.tenant_id = %s AND c.project_slug = %s
                   AND c.agent_slug = %s AND c.thread_id <> %s
            )
            SELECT t.thread_id, t.user_id, t.parent_thread_id
              FROM subtree s
              JOIN conversation_threads t ON t.thread_id = s.thread_id
             WHERE t.tenant_id = %s AND t.project_slug = %s AND t.agent_slug = %s
             ORDER BY t.thread_id ASC
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        *self._scope,
                        root_thread_id,
                        root_thread_id,
                        *self._scope,
                        root_thread_id,
                        *self._scope,
                    ),
                )
                rows = cur.fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def search(
        self,
        query: str,
        thread_filter: ThreadFilter,
        *,
        limit: int,
        offset: int = 0,
    ) -> Tuple[List[SearchHit], int]:
        terms = search_terms(query)
        if not terms:
            return [], 0
        # Terms are ANDed as a tsquery built from parameters, never from
        # interpolated text: ``plainto_tsquery`` escapes whatever it is
        # given, so no character a user types reaches the tsquery parser.
        tsquery = " && ".join(["plainto_tsquery('simple', %s)"] * len(terms))
        clauses, predicate_params = self._thread_predicate(thread_filter)
        headline_options = (
            f"StartSel={_HEADLINE_START},StopSel={_HEADLINE_STOP},"
            "MaxWords=24,MinWords=8,MaxFragments=1"
        )
        matches_sql = f"""
            WITH q AS (SELECT ({tsquery}) AS query),
            matches AS (
                SELECT i.thread_id AS thread_id,
                       i.item_id   AS item_id,
                       i.turn_id   AS turn_id,
                       i.kind      AS match_kind,
                       i.seq       AS seq,
                       ts_headline('simple', coalesce(i.search_text, ''),
                                   q.query, %s) AS preview,
                       -ts_rank_cd(i.search_tsv, q.query) AS rank_value
                  FROM conversation_items i
                  JOIN q ON true
                  JOIN conversation_threads
                    ON conversation_threads.tenant_id = i.tenant_id
                   AND conversation_threads.project_slug = i.project_slug
                   AND conversation_threads.agent_slug = i.agent_slug
                   AND conversation_threads.thread_id = i.thread_id
                 WHERE i.search_tsv @@ q.query AND {' AND '.join(clauses)}
                UNION ALL
                SELECT conversation_threads.thread_id, NULL, NULL,
                       'thread_title', NULL,
                       ts_headline('simple',
                                   coalesce(conversation_threads.title, ''),
                                   q.query, %s),
                       -ts_rank_cd(conversation_threads.title_tsv, q.query)
                  FROM conversation_threads
                  JOIN q ON true
                 WHERE conversation_threads.title_tsv @@ q.query
                   AND {' AND '.join(clauses)}
            )
        """
        match_params = [
            *terms,
            headline_options,
            *predicate_params,
            headline_options,
            *predicate_params,
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    matches_sql + "SELECT thread_id, COUNT(*), MIN(rank_value) AS best "
                    "FROM matches GROUP BY thread_id "
                    "ORDER BY best ASC, thread_id ASC LIMIT %s OFFSET %s",
                    [*match_params, limit, max(0, offset)],
                )
                grouped = cur.fetchall()
                cur.execute(
                    matches_sql + "SELECT COUNT(DISTINCT thread_id) FROM matches",
                    match_params,
                )
                total_row = cur.fetchone()
                total = int(total_row[0]) if total_row else 0
                if not grouped:
                    return [], total
                thread_ids = [row[0] for row in grouped]
                cur.execute(
                    matches_sql
                    + "SELECT thread_id, item_id, turn_id, match_kind, seq, "
                    "preview, rank_value FROM matches "
                    "WHERE thread_id = ANY(%s) "
                    "ORDER BY thread_id ASC, rank_value ASC",
                    [*match_params, thread_ids],
                )
                best_rows = cur.fetchall()
        best_by_thread: Dict[str, Sequence[Any]] = {}
        for row in best_rows:
            best_by_thread.setdefault(row[0], row)
        hits: List[SearchHit] = []
        for thread_id, match_count, _best in grouped:
            best = best_by_thread.get(thread_id)
            if best is None:  # pragma: no cover - grouped implies a member
                continue
            hits.append(
                SearchHit(
                    thread_id=thread_id,
                    match_count=int(match_count),
                    match_kind=str(best[3]),
                    item_id=best[1],
                    turn_id=best[2],
                    seq=int(best[4]) if best[4] is not None else None,
                    preview=_strip_headline_markers(str(best[5] or "")),
                )
            )
        return hits, total

    def allocate_seq(self, thread_id: str, count: int = 1) -> int:
        if count < 1:
            raise ValueError("count must be >= 1")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversation_threads SET last_seq = last_seq + %s "
                    f"WHERE {self._THREAD_SCOPE} RETURNING last_seq",
                    (count, *self._scope, thread_id),
                )
                row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    raise KeyError(f"Unknown thread: {thread_id}")
            conn.commit()
        return int(row[0]) - count + 1

    # -- turns ---------------------------------------------------------

    _TURN_SELECT = (
        "SELECT thread_id, turn_id, ordinal, status, created_at, updated_at, "
        "completed_at, prompt, prompt_role, end_reason, error, usage, model, "
        "model_ref, item_count, first_seq, last_seq, attempts, source, "
        "schema_version FROM conversation_turns"
    )

    def get_turn(self, thread_id: str, turn_id: str) -> Optional[Turn]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._TURN_SELECT} WHERE {self._THREAD_SCOPE} AND turn_id = %s",
                    (*self._scope, thread_id, turn_id),
                )
                row = cur.fetchone()
        return _turn_from_row(row) if row else None

    def _upsert_turn(self, cur: Any, turn: Turn) -> None:
        from psycopg.types.json import Jsonb

        cur.execute(
            """
            INSERT INTO conversation_turns (
                tenant_id, project_slug, agent_slug, thread_id, turn_id, ordinal,
                status, created_at, updated_at, completed_at, prompt, prompt_role,
                end_reason, error, usage, model, model_ref, item_count, first_seq,
                last_seq, attempts, source, schema_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, project_slug, agent_slug, thread_id, turn_id)
            DO UPDATE SET
                ordinal = EXCLUDED.ordinal,
                status = EXCLUDED.status,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                completed_at = EXCLUDED.completed_at,
                prompt = EXCLUDED.prompt,
                prompt_role = EXCLUDED.prompt_role,
                end_reason = EXCLUDED.end_reason,
                error = EXCLUDED.error,
                usage = EXCLUDED.usage,
                model = EXCLUDED.model,
                model_ref = EXCLUDED.model_ref,
                item_count = EXCLUDED.item_count,
                first_seq = EXCLUDED.first_seq,
                last_seq = EXCLUDED.last_seq,
                attempts = EXCLUDED.attempts,
                source = EXCLUDED.source,
                schema_version = EXCLUDED.schema_version
            """,
            (
                *self._scope,
                turn.thread_id,
                turn.turn_id,
                int(turn.ordinal),
                turn.status.value,
                float(turn.created_at),
                float(turn.updated_at),
                turn.completed_at,
                turn.prompt,
                turn.prompt_role,
                turn.end_reason,
                Jsonb(turn.error) if turn.error is not None else None,
                Jsonb(turn.usage),
                turn.model,
                turn.model_ref,
                int(turn.item_count),
                turn.first_seq,
                turn.last_seq,
                int(turn.attempts),
                turn.source,
                int(turn.schema_version),
            ),
        )

    def upsert_turn(self, turn: Turn) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                self._upsert_turn(cur, turn)
            conn.commit()

    def mutate_turn(self, thread_id, turn_id, update, *, initial_item=None):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._THREAD_SELECT} WHERE {self._THREAD_SCOPE} FOR UPDATE",
                    (*self._scope, thread_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"Unknown thread: {thread_id}")
                thread = _thread_from_row(row)
                cur.execute(
                    f"{self._TURN_SELECT} WHERE {self._THREAD_SCOPE} AND turn_id = %s FOR UPDATE",
                    (*self._scope, thread_id, turn_id),
                )
                row = cur.fetchone()
                cur.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM conversation_turns "
                    f"WHERE {self._THREAD_SCOPE}",
                    (*self._scope, thread_id),
                )
                ordinal = int(cur.fetchone()[0])
                turn = update(thread, _turn_from_row(row) if row else None, ordinal)
                if turn is not None:
                    if (
                        turn.thread_id != thread_id
                        or turn.turn_id != turn_id
                        or thread.thread_id != thread_id
                    ):
                        raise ValueError("Turn mutation cannot change identity")
                    if initial_item is not None:
                        cur.execute(
                            "SELECT 1 FROM conversation_items "
                            f"WHERE {self._THREAD_SCOPE} AND item_id = %s",
                            (*self._scope, thread_id, initial_item.item_id),
                        )
                        if cur.fetchone() is None:
                            item = _attach_initial_item(thread, turn, initial_item)
                            self._upsert_item(cur, item)
                    self._upsert_turn(cur, turn)
                    self._upsert_thread(cur, thread)
            conn.commit()
            return turn

    def list_turns(
        self,
        thread_id: str,
        *,
        limit: int,
        after_ordinal: Optional[int] = None,
        before_ordinal: Optional[int] = None,
        descending: bool = False,
    ) -> List[Turn]:
        clauses = [self._THREAD_SCOPE]
        params: List[Any] = [*self._scope, thread_id]
        if after_ordinal is not None:
            clauses.append("ordinal > %s")
            params.append(after_ordinal)
        if before_ordinal is not None:
            clauses.append("ordinal < %s")
            params.append(before_ordinal)
        params.append(limit)
        order = "DESC" if descending else "ASC"
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._TURN_SELECT} WHERE {' AND '.join(clauses)} "
                    f"ORDER BY ordinal {order} LIMIT %s",
                    params,
                )
                rows = cur.fetchall()
        return [_turn_from_row(row) for row in rows]

    def count_turns(self, thread_id: str) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversation_turns "
                    f"WHERE {self._THREAD_SCOPE}",
                    (*self._scope, thread_id),
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def max_turn_ordinal(self, thread_id: str) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) FROM conversation_turns "
                    f"WHERE {self._THREAD_SCOPE}",
                    (*self._scope, thread_id),
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    # -- items ---------------------------------------------------------

    _ITEM_SELECT = (
        "SELECT thread_id, item_id, turn_id, seq, kind, status, role, "
        "created_at, updated_at, completed_at, revision, content, source_ref, "
        "long_lived, source, schema_version FROM conversation_items"
    )

    def get_item(self, thread_id: str, item_id: str) -> Optional[Item]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._ITEM_SELECT} WHERE {self._THREAD_SCOPE} AND item_id = %s",
                    (*self._scope, thread_id, item_id),
                )
                row = cur.fetchone()
        return _item_from_row(row) if row else None

    def _upsert_item(self, cur: Any, item: Item) -> None:
        from psycopg.types.json import Jsonb

        cur.execute(
            """
            INSERT INTO conversation_items (
                tenant_id, project_slug, agent_slug, thread_id, item_id, turn_id,
                seq, kind, status, role, created_at, updated_at, completed_at,
                revision, content, source_ref, long_lived, source, schema_version,
                search_text
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, project_slug, agent_slug, thread_id, item_id)
            DO UPDATE SET
                turn_id = EXCLUDED.turn_id,
                seq = EXCLUDED.seq,
                kind = EXCLUDED.kind,
                status = EXCLUDED.status,
                role = EXCLUDED.role,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                completed_at = EXCLUDED.completed_at,
                revision = EXCLUDED.revision,
                content = EXCLUDED.content,
                source_ref = EXCLUDED.source_ref,
                long_lived = EXCLUDED.long_lived,
                source = EXCLUDED.source,
                schema_version = EXCLUDED.schema_version,
                search_text = EXCLUDED.search_text
            """,
            (
                *self._scope,
                item.thread_id,
                item.item_id,
                item.turn_id,
                int(item.seq),
                item.kind.value,
                item.status.value,
                item.role,
                float(item.created_at),
                float(item.updated_at),
                item.completed_at,
                int(item.revision),
                Jsonb(item.content),
                Jsonb(item.source_ref),
                bool(item.long_lived),
                item.source,
                int(item.schema_version),
                derive_search_text(item.kind, item.content),
            ),
        )

    def upsert_item(self, item: Item) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                self._upsert_item(cur, item)
            conn.commit()

    def append_item(self, item: Item) -> Item:
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Serialize writers for this thread before checking identity.
                cur.execute(
                    "SELECT last_seq FROM conversation_threads "
                    f"WHERE {self._THREAD_SCOPE} FOR UPDATE",
                    (*self._scope, item.thread_id),
                )
                thread = cur.fetchone()
                if thread is None:
                    raise KeyError(f"Unknown thread: {item.thread_id}")
                cur.execute(
                    f"{self._ITEM_SELECT} WHERE {self._THREAD_SCOPE} AND item_id = %s",
                    (*self._scope, item.thread_id, item.item_id),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return _item_from_row(existing)
                created = replace(item, seq=int(thread[0]) + 1)
                self._upsert_item(cur, created)
                cur.execute(
                    "UPDATE conversation_threads SET last_seq = %s, "
                    "item_count = item_count + 1, updated_at = %s "
                    f"WHERE {self._THREAD_SCOPE}",
                    (created.seq, created.updated_at, *self._scope, created.thread_id),
                )
                if created.turn_id:
                    cur.execute(
                        "UPDATE conversation_turns SET item_count = item_count + 1, "
                        "first_seq = LEAST(first_seq, %s), last_seq = GREATEST(last_seq, %s), "
                        "updated_at = %s "
                        f"WHERE {self._THREAD_SCOPE} AND turn_id = %s",
                        (
                            created.seq,
                            created.seq,
                            created.updated_at,
                            *self._scope,
                            created.thread_id,
                            created.turn_id,
                        ),
                    )
            conn.commit()
            return created

    def mutate_item(self, thread_id, item_id, update):
        with self._conn() as conn:
            with conn.cursor() as cur:
                # All runtime writers take thread then item/turn locks.
                cur.execute(
                    f"{self._THREAD_SELECT} WHERE {self._THREAD_SCOPE} FOR UPDATE",
                    (*self._scope, thread_id),
                )
                if cur.fetchone() is None:
                    return None
                cur.execute(
                    f"{self._ITEM_SELECT} WHERE {self._THREAD_SCOPE} AND item_id = %s FOR UPDATE",
                    (*self._scope, thread_id, item_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                existing = _item_from_row(row)
                identity = (thread_id, item_id, existing.seq, existing.turn_id)
                updated = update(existing)
                if (
                    updated.thread_id,
                    updated.item_id,
                    updated.seq,
                    updated.turn_id,
                ) != identity:
                    raise ValueError("Item mutation cannot change identity or position")
                self._upsert_item(cur, updated)
            conn.commit()
            return updated

    def _item_predicate(
        self,
        thread_id: str,
        turn_id: Optional[str],
        kinds: Optional[Sequence[ItemKind]],
        after_seq: Optional[int],
        before_seq: Optional[int],
    ) -> Tuple[List[str], List[Any]]:
        clauses = [self._THREAD_SCOPE]
        params: List[Any] = [*self._scope, thread_id]
        if turn_id is not None:
            clauses.append("turn_id = %s")
            params.append(turn_id)
        if kinds:
            clauses.append("kind = ANY(%s)")
            params.append([kind.value for kind in kinds])
        if after_seq is not None:
            clauses.append("seq > %s")
            params.append(after_seq)
        if before_seq is not None:
            clauses.append("seq < %s")
            params.append(before_seq)
        return clauses, params

    def list_items(
        self,
        thread_id: str,
        *,
        limit: int,
        turn_id: Optional[str] = None,
        kinds: Optional[Sequence[ItemKind]] = None,
        after_seq: Optional[int] = None,
        before_seq: Optional[int] = None,
        descending: bool = False,
    ) -> List[Item]:
        clauses, params = self._item_predicate(
            thread_id, turn_id, kinds, after_seq, before_seq
        )
        params.append(limit)
        order = "DESC" if descending else "ASC"
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._ITEM_SELECT} WHERE {' AND '.join(clauses)} "
                    f"ORDER BY seq {order} LIMIT %s",
                    params,
                )
                rows = cur.fetchall()
        return [_item_from_row(row) for row in rows]

    def read_snapshot(self, thread_id: str) -> dict:
        with self._conn(snapshot=True) as conn:
            with conn.cursor() as cur:
                scope = (*self._scope, thread_id)
                cur.execute(
                    f"{self._ITEM_SELECT} WHERE {self._THREAD_SCOPE} ORDER BY seq",
                    scope,
                )
                rows = cur.fetchall()
                cur.execute(
                    f"SELECT state FROM conversation_inboxes WHERE {self._THREAD_SCOPE}",
                    scope,
                )
                inbox = cur.fetchone()
        return {
            "items": [_item_from_row(row) for row in rows],
            "inbox": _loads(inbox[0]) if inbox else {"version": 1, "items": []},
        }

    def count_items(
        self,
        thread_id: str,
        *,
        turn_id: Optional[str] = None,
        kinds: Optional[Sequence[ItemKind]] = None,
    ) -> int:
        clauses, params = self._item_predicate(thread_id, turn_id, kinds, None, None)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversation_items "
                    f"WHERE {' AND '.join(clauses)}",
                    params,
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def open_items(
        self, thread_id: str, *, turn_id: Optional[str] = None
    ) -> List[Item]:
        clauses = [self._THREAD_SCOPE]
        params: List[Any] = [*self._scope, thread_id]
        if turn_id is not None:
            clauses.append("turn_id = %s")
            params.append(turn_id)
        clauses.append("NOT (status = ANY(%s))")
        params.append(list(_TERMINAL_STATUS_VALUES))
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._ITEM_SELECT} WHERE {' AND '.join(clauses)} "
                    "ORDER BY seq ASC",
                    params,
                )
                rows = cur.fetchall()
        return [_item_from_row(row) for row in rows]

    def write_projection(
        self, thread: Thread, turns: Iterable[Turn], items: Iterable[Item]
    ) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                self._upsert_thread(cur, thread)
                for turn in turns:
                    self._upsert_turn(cur, turn)
                for item in items:
                    self._upsert_item(cur, item)
            conn.commit()


class _PgConn:
    """Context manager that returns the pooled connection to the pool."""

    def __init__(self, cm: Any, conn: Any) -> None:
        self._cm = cm
        self._conn = conn

    def __enter__(self) -> Any:
        return self._conn

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return self._cm.__exit__(exc_type, exc, tb)


# ---------------------------------------------------------------------
# Backend resolution
# ---------------------------------------------------------------------

_STORES: Dict[Tuple[str, str, str], ConversationStore] = {}
_STORES_LOCK = Lock()


def get_conversation_store(
    project_slug: str | None = None, agent_slug: str | None = None
) -> ConversationStore:
    """Resolve (and cache) the store for one (project, agent) shard.

    Backend selection reads ``OMNIAGENTS_HISTORY_BACKEND`` at *call* time,
    not import time, so the same fixtures that flip the history backend mid
    test-run steer the domain store too. The cache key includes the fully
    resolved location (SQLite file path / Postgres DSN + tenant), not just
    the slugs, so redirecting ``OMNIAGENTS_HISTORY_DB`` between tests hands
    back a *new* store instead of one still pointing at the previous
    fixture's temp file.
    """
    kind = (os.getenv("OMNIAGENTS_HISTORY_BACKEND") or "sqlite").strip().lower()
    if kind == "postgres":
        dsn = os.getenv("OMNIAGENTS_HISTORY_URL")
        if not dsn:
            raise RuntimeError(
                "OMNIAGENTS_HISTORY_BACKEND=postgres requires OMNIAGENTS_HISTORY_URL"
            )
        location = f"{dsn}#{os.getenv('OMNIAGENTS_TENANT_ID') or DEFAULT_TENANT}"
    else:
        location = f"{get_sessions_db_path(project_slug, agent_slug)}.domain"
    key = (kind, location, f"{project_slug or ''}/{agent_slug or ''}")
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is not None:
            return store
        if kind == "postgres":
            store = PgConversationStore(
                os.environ["OMNIAGENTS_HISTORY_URL"],
                project_slug=project_slug,
                agent_slug=agent_slug,
            )
        else:
            store = SqliteConversationStore(path=location)
        _STORES[key] = store
        return store


def reset_conversation_store() -> None:
    """Close and forget every cached store. Test-fixture entry point."""
    with _STORES_LOCK:
        for store in _STORES.values():
            try:
                store.close()
            except Exception:  # pragma: no cover - teardown best effort
                pass
        _STORES.clear()
