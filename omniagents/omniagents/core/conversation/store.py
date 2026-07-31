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

The store is deliberately dumb: it persists and queries rows. Lifecycle
rules (terminal-once transitions, ordinal allocation policy) live in
:mod:`~omniagents.core.conversation.model` and
:mod:`~omniagents.core.conversation.recorder`. The one exception is
:meth:`ConversationStore.allocate_seq`, which must be atomic with respect to
concurrent writers and therefore belongs in SQL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
import sqlite3
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from omniagents.core.paths import get_sessions_db_path

from .model import (
    ITEM_SCHEMA_VERSION,
    Item,
    ItemKind,
    ItemStatus,
    Thread,
    ThreadStatus,
    Turn,
    TurnStatus,
)

__all__ = [
    "ConversationStore",
    "PgConversationStore",
    "SqliteConversationStore",
    "get_conversation_store",
    "reset_conversation_store",
]


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
    def delete_thread(self, thread_id: str) -> bool:
        """Remove a thread and everything under it. True if it existed."""

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
_SQLITE_UPSERT_ITEM = _sqlite_upsert("items", _ITEM_COLUMNS, ("thread_id", "item_id"))


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


_TERMINAL_STATUS_VALUES = tuple(
    status.value
    for status in (ItemStatus.COMPLETED, ItemStatus.FAILED, ItemStatus.CANCELLED)
)


# ---------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------

_SQLITE_SCHEMA = (
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
        schema_version        INTEGER NOT NULL DEFAULT 1
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
        PRIMARY KEY (thread_id, item_id)
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_thread_seq ON items(thread_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_items_turn ON items(thread_id, turn_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_turns_ordinal ON turns(thread_id, ordinal)",
)

#: Additive column migrations for stores created by an earlier build. Each
#: is attempted once per connection and ignored when the column exists.
_SQLITE_MIGRATIONS = (
    "ALTER TABLE items ADD COLUMN long_lived INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE threads ADD COLUMN projected_at REAL",
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
        self._conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # -- threads -------------------------------------------------------

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

    def delete_thread(self, thread_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "DELETE FROM threads WHERE thread_id = ?", (thread_id,)
            )
            conn.execute("DELETE FROM turns WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM items WHERE thread_id = ?", (thread_id,))
            conn.commit()
            return cursor.rowcount > 0

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

_PG_SCHEMA_SQL = """
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

ALTER TABLE conversation_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_turns   ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_items   ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target TEXT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'conversation_threads', 'conversation_turns', 'conversation_items'
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

    def close(self) -> None:
        try:
            self._pool.close()
        except Exception:  # pragma: no cover - shutdown best effort
            pass

    @property
    def _scope(self) -> Tuple[str, str, str]:
        return (self._tenant_id, self._project, self._agent)

    def _conn(self):
        cm = self._pool.connection()
        conn = cm.__enter__()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.current_tenant', %s, true)",
                    (self._tenant_id,),
                )
        except Exception:
            cm.__exit__(None, None, None)
            raise
        return _PgConn(cm, conn)

    # -- threads -------------------------------------------------------

    _THREAD_SELECT = (
        "SELECT thread_id, user_id, status, title, created_at, updated_at, "
        "last_seq, turn_count, item_count, parent_thread_id, "
        "branched_from_item_id, usage, compaction, source, projected_at, "
        "schema_version FROM conversation_threads"
    )
    _THREAD_SCOPE = (
        "tenant_id = %s AND project_slug = %s AND agent_slug = %s AND thread_id = %s"
    )

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

    def _upsert_thread(self, cur: Any, thread: Thread) -> None:
        from psycopg.types.json import Jsonb

        cur.execute(
            """
            INSERT INTO conversation_threads (
                tenant_id, project_slug, agent_slug, thread_id, user_id, status,
                title, created_at, updated_at, last_seq, turn_count, item_count,
                parent_thread_id, branched_from_item_id, usage, compaction,
                source, projected_at, schema_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
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
                schema_version = EXCLUDED.schema_version
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
            ),
        )

    def delete_thread(self, thread_id: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                for table in (
                    "conversation_items",
                    "conversation_turns",
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
                revision, content, source_ref, long_lived, source, schema_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
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
                schema_version = EXCLUDED.schema_version
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
            ),
        )

    def upsert_item(self, item: Item) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                self._upsert_item(cur, item)
            conn.commit()

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
