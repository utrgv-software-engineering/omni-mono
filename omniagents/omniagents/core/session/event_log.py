"""Durable, sequenced per-session event journal for GUI-protocol replay.

Every *replayable* server→client notification is stamped with a replay
envelope before it leaves the process:

* ``session_id`` — the session the event belongs to,
* ``stream_id``  — the event-stream epoch (stable across reconnects *and*
  server restarts as long as the durable journal survives; regenerated
  when the journal is missing or destroyed, or when journal durability
  was lost — see *Durability loss* below),
* ``seq``        — a per-session monotonically increasing sequence id.

The stamped events are appended to a durable store so a client that
reconnects (or a server that restarted) can serve ``resume_session``
requests from the last acknowledged cursor. ``token`` usage deltas are
deliberately *not* journaled — they are transient, cumulative-totals
deltas superseded by the next ``token``/``run_end`` — so they carry no
``seq`` and are invisible to client gap detection.

Retention and compaction
------------------------

* **Bounded retention** — at most ``max_events`` events are retained per
  session (``OMNIAGENTS_EVENT_LOG_MAX_EVENTS``, default 1024). Trimming
  raises the compaction floor; a ``resume_session`` cursor below the
  floor gets an explicit :class:`ResyncRequiredError`.
* **Snapshot coalescing** — ``queue_changed`` is a full-replace snapshot,
  so recording a new one drops earlier retained ``queue_changed`` events.
  Coalescing does not raise the compaction floor (the dropped snapshots
  are superseded, not lost); replay consumers must therefore adopt the
  ``last_seq`` returned by ``resume_session`` instead of requiring a
  contiguous replay window.
* **Acknowledgement trim** — consumers register a cursor (via
  ``resume_session``) and advance it with ``ack_events``. Events at or
  below the minimum cursor across live consumers are compacted away.
  A consumer that vanished and returns with a pre-compaction cursor gets
  ``ResyncRequiredError`` and must refetch authoritative state
  (``get_session_history``).

Durability loss
---------------

``record`` never fails the live emission path on a journal I/O error —
live clients are served from the in-memory window, which stays complete
and correctly ordered. But once a durable append has failed, the durable
journal is *behind* the in-memory sequence: if the process then died and
the journal reloaded, the same ``stream_id`` would resume from the stale
durable ``last_seq`` and **reuse sequence ids** for genuinely new
events, which reconnecting clients would silently drop as duplicates.

To make that impossible, the first failed append marks the log
*durability-lost*: a dirty flag is persisted to the store (retried on
every subsequent append until it sticks) and the next reload of the
journal **rotates the stream epoch** — new ``stream_id``, sequence reset,
retained events discarded — so every stale cursor gets an explicit
:class:`ResyncRequiredError` (-32030) instead of a silently gapped
stream. Rotation happens at reload rather than immediately so a
transient disk blip does not force every live client into a resync
storm; the residual risk is a store so broken that even the one-row
dirty marker never persists before the process dies.

Server-restart recovery
-----------------------

On construction the journal reloads its durable state. If the retained
events describe an interrupted epoch — a ``run_started`` without a
matching ``run_end``, or unresolved approval / client_request prompts —
synthetic terminal events are appended (``run_end`` with
``end_reason="server_restart"`` and the matching ``*_resolved`` events
with ``reason="server_restart"``) so reconnecting clients converge on a
terminated-run state instead of waiting on prompts no live future backs.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections import deque
from threading import Lock
from typing import Any, Deque, Dict, Hashable, List, Optional, Tuple

from omniagents.core.paths import get_sessions_db_path

__all__ = [
    "REPLAYABLE_METHODS",
    "ResyncRequiredError",
    "SessionEventLog",
    "MemoryEventLogStore",
    "SqliteEventLogStore",
    "stamp_session_event",
    "get_default_store",
    "reset_default_store",
]

# Server→client notifications that are stamped + journaled for replay.
# Everything session-scoped except ``token`` (transient usage deltas).
REPLAYABLE_METHODS = frozenset(
    {
        "run_started",
        "run_status",
        "message_output",
        "tool_called",
        "tool_result",
        "run_end",
        "tool_approval_requested",
        "tool_approval_resolved",
        "mcp_approval_requested",
        "mcp_approval_resolved",
        "client_request",
        "client_request_resolved",
        "queue_changed",
        "session_forked",
        "session_variables_changed",
    }
)

# Full-replace snapshot notifications: recording a new one supersedes any
# retained older instance for the same session.
_SNAPSHOT_METHODS = frozenset({"queue_changed"})

_DEFAULT_MAX_EVENTS = 1024


class ResyncRequiredError(Exception):
    """The requested replay cursor cannot be served from retained history.

    Mapped to JSON-RPC error ``-32030`` ("Resync required") by the server
    endpoint. Clients must refetch authoritative state via
    ``get_session_history`` and resume from the returned ``last_seq``.
    """

    code = -32030

    def __init__(
        self,
        *,
        session_id: str,
        stream_id: str,
        retained_first_seq: int,
        retained_last_seq: int,
    ) -> None:
        super().__init__("Resync required")
        self.session_id = session_id
        self.stream_id = stream_id
        self.retained_first_seq = retained_first_seq
        self.retained_last_seq = retained_last_seq

    @property
    def data(self) -> Dict[str, Any]:
        return {
            "kind": "resync_required",
            "session_id": self.session_id,
            "stream_id": self.stream_id,
            "retained_first_seq": self.retained_first_seq,
            "retained_last_seq": self.retained_last_seq,
        }


# ---------------------------------------------------------------------------
# Durable stores
# ---------------------------------------------------------------------------


class MemoryEventLogStore:
    """Process-lifetime store. Survives reconnects, not process restarts."""

    def __init__(self) -> None:
        self._streams: Dict[str, Tuple[str, int, int]] = {}
        self._events: Dict[str, List[Tuple[int, str, str]]] = {}
        self._dirty: set[str] = set()
        self._lock = Lock()

    def load(
        self, session_id: str
    ) -> Optional[Tuple[str, int, int, List[Tuple[int, str, str]]]]:
        with self._lock:
            stream = self._streams.get(session_id)
            if stream is None:
                return None
            stream_id, last_seq, compacted_below = stream
            events = list(self._events.get(session_id, []))
            return stream_id, last_seq, compacted_below, events

    def create_stream(self, session_id: str, stream_id: str) -> None:
        with self._lock:
            self._streams[session_id] = (stream_id, 0, 0)
            self._events[session_id] = []
            self._dirty.discard(session_id)

    def mark_dirty(self, session_id: str) -> None:
        with self._lock:
            self._dirty.add(session_id)

    def is_dirty(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._dirty

    def append(self, session_id: str, seq: int, method: str, payload_json: str) -> None:
        with self._lock:
            stream_id, _, compacted_below = self._streams[session_id]
            self._streams[session_id] = (stream_id, seq, compacted_below)
            self._events.setdefault(session_id, []).append((seq, method, payload_json))

    def delete_events(self, session_id: str, seqs: List[int]) -> None:
        if not seqs:
            return
        drop = set(seqs)
        with self._lock:
            self._events[session_id] = [
                row for row in self._events.get(session_id, []) if row[0] not in drop
            ]

    def set_compacted_below(self, session_id: str, compacted_below: int) -> None:
        with self._lock:
            stream_id, last_seq, previous = self._streams[session_id]
            self._streams[session_id] = (
                stream_id,
                last_seq,
                max(previous, compacted_below),
            )
            self._events[session_id] = [
                row
                for row in self._events.get(session_id, [])
                if row[0] > compacted_below
            ]

    def destroy(self, session_id: str) -> None:
        with self._lock:
            self._streams.pop(session_id, None)
            self._events.pop(session_id, None)
            self._dirty.discard(session_id)


class SqliteEventLogStore:
    """SQLite-backed store colocated with the session-history shard.

    The journal lives in ``<sessions.db>.events`` so test fixtures that
    redirect ``OMNIAGENTS_HISTORY_DB`` isolate the journal automatically.
    Connections are opened per operation — journal traffic is low
    (``token`` deltas are excluded) and this keeps the store free of
    cross-test connection caches.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._explicit_path = path
        self._lock = Lock()

    def _path(
        self, project_slug: str | None = None, agent_slug: str | None = None
    ) -> str:
        if self._explicit_path:
            return self._explicit_path
        base = get_sessions_db_path(project_slug, agent_slug)
        return f"{base}.events"

    # The per-session slugs are fixed at SessionEventLog construction; the
    # log passes them through via a bound path.
    def bind(self, project_slug: str | None, agent_slug: str | None) -> None:
        self._explicit_path = self._path(project_slug, agent_slug)

    def _connect(self) -> sqlite3.Connection:
        path = self._path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS event_streams ("
            "session_id TEXT PRIMARY KEY,"
            "stream_id TEXT NOT NULL,"
            "last_seq INTEGER NOT NULL DEFAULT 0,"
            "compacted_below INTEGER NOT NULL DEFAULT 0,"
            "dirty INTEGER NOT NULL DEFAULT 0)"
        )
        try:
            # Migration for journals created before durability-loss
            # tracking existed.
            conn.execute(
                "ALTER TABLE event_streams "
                "ADD COLUMN dirty INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass  # column already present
        conn.execute(
            "CREATE TABLE IF NOT EXISTS session_events ("
            "session_id TEXT NOT NULL,"
            "seq INTEGER NOT NULL,"
            "method TEXT NOT NULL,"
            "payload_json TEXT NOT NULL,"
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "PRIMARY KEY (session_id, seq))"
        )
        return conn

    def load(
        self, session_id: str
    ) -> Optional[Tuple[str, int, int, List[Tuple[int, str, str]]]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT stream_id, last_seq, compacted_below FROM event_streams "
                    "WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return None
                events = conn.execute(
                    "SELECT seq, method, payload_json FROM session_events "
                    "WHERE session_id = ? ORDER BY seq",
                    (session_id,),
                ).fetchall()
                return (
                    row[0],
                    int(row[1]),
                    int(row[2]),
                    [
                        (int(seq), method, payload_json)
                        for seq, method, payload_json in events
                    ],
                )
            finally:
                conn.close()

    def create_stream(self, session_id: str, stream_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO event_streams "
                    "(session_id, stream_id, last_seq, compacted_below) "
                    "VALUES (?, ?, 0, 0)",
                    (session_id, stream_id),
                )
                conn.execute(
                    "DELETE FROM session_events WHERE session_id = ?", (session_id,)
                )
                conn.commit()
            finally:
                conn.close()

    def mark_dirty(self, session_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE event_streams SET dirty = 1 WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def is_dirty(self, session_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT dirty FROM event_streams WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                return bool(row and row[0])
            finally:
                conn.close()

    def append(self, session_id: str, seq: int, method: str, payload_json: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO session_events "
                    "(session_id, seq, method, payload_json) VALUES (?, ?, ?, ?)",
                    (session_id, seq, method, payload_json),
                )
                conn.execute(
                    "UPDATE event_streams SET last_seq = ? WHERE session_id = ?",
                    (seq, session_id),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_events(self, session_id: str, seqs: List[int]) -> None:
        if not seqs:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "DELETE FROM session_events WHERE session_id = ? AND seq = ?",
                    [(session_id, seq) for seq in seqs],
                )
                conn.commit()
            finally:
                conn.close()

    def set_compacted_below(self, session_id: str, compacted_below: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM session_events WHERE session_id = ? AND seq <= ?",
                    (session_id, compacted_below),
                )
                conn.execute(
                    "UPDATE event_streams SET compacted_below = "
                    "MAX(compacted_below, ?) WHERE session_id = ?",
                    (compacted_below, session_id),
                )
                conn.commit()
            finally:
                conn.close()

    def destroy(self, session_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM session_events WHERE session_id = ?", (session_id,)
                )
                conn.execute(
                    "DELETE FROM event_streams WHERE session_id = ?", (session_id,)
                )
                conn.commit()
            finally:
                conn.close()


_default_memory_store: MemoryEventLogStore | None = None
_default_store_lock = Lock()


def get_default_store(project_slug: str | None = None, agent_slug: str | None = None):
    """Resolve the journal store from ``OMNIAGENTS_EVENT_LOG_BACKEND``.

    ``sqlite`` (default) keeps the journal next to the session-history
    shard; ``memory`` keeps it process-local (reconnects survive, restarts
    do not). Postgres session-history deployments should currently pair
    with ``memory`` — a shared-database journal store is a follow-up.
    """
    kind = (os.getenv("OMNIAGENTS_EVENT_LOG_BACKEND") or "sqlite").strip().lower()
    if kind == "memory":
        global _default_memory_store
        with _default_store_lock:
            if _default_memory_store is None:
                _default_memory_store = MemoryEventLogStore()
            return _default_memory_store
    store = SqliteEventLogStore()
    store.bind(project_slug, agent_slug)
    return store


def reset_default_store() -> None:
    """Drop the shared in-memory store (test isolation helper)."""
    global _default_memory_store
    with _default_store_lock:
        _default_memory_store = None


# ---------------------------------------------------------------------------
# Session event log
# ---------------------------------------------------------------------------


class SessionEventLog:
    """Sequenced, durable journal of replayable events for one session."""

    def __init__(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        store: Any | None = None,
        max_events: int | None = None,
    ) -> None:
        self.session_id = session_id
        self._store = store or get_default_store(project_slug, agent_slug)
        if max_events is None:
            try:
                max_events = int(
                    os.getenv("OMNIAGENTS_EVENT_LOG_MAX_EVENTS", "")
                    or _DEFAULT_MAX_EVENTS
                )
            except ValueError:
                max_events = _DEFAULT_MAX_EVENTS
        self.max_events = max(1, max_events)
        # consumer key -> acknowledged seq (live connections only)
        self._cursors: Dict[Hashable, int] = {}
        self._events: Deque[Tuple[int, str, Dict[str, Any]]] = deque()
        # Durability-loss tracking: set on the first failed durable append,
        # persisted as a dirty marker so the *next* reload rotates the
        # stream epoch instead of resuming (and reusing seqs on) an epoch
        # whose durable tail is behind the in-memory sequence.
        self._durability_lost = False
        self._durability_loss_marked = False

        loaded = None
        try:
            loaded = self._store.load(session_id)
        except Exception:
            loaded = None
        if loaded is not None and self._stored_dirty():
            # A previous epoch lost durability: its durable tail may be
            # behind the sequence live clients already applied. Resuming it
            # would reuse seqs on the same stream_id, so rotate — stale
            # cursors then get an explicit ResyncRequiredError.
            loaded = None
        if loaded is None:
            self.stream_id = uuid.uuid4().hex
            self.last_seq = 0
            self.compacted_below = 0
            try:
                self._store.create_stream(session_id, self.stream_id)
            except Exception:
                pass
        else:
            stream_id, last_seq, compacted_below, rows = loaded
            self.stream_id = stream_id
            self.last_seq = int(last_seq)
            self.compacted_below = int(compacted_below)
            for seq, method, payload_json in rows:
                try:
                    payload = json.loads(payload_json)
                except (TypeError, ValueError):
                    continue
                self._events.append((int(seq), method, payload))
            self._reconcile_interrupted_epoch()

    def _stored_dirty(self) -> bool:
        """Whether the durable store marked this session durability-lost."""
        is_dirty = getattr(self._store, "is_dirty", None)
        if is_dirty is None:
            return False
        try:
            return bool(is_dirty(self.session_id))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def durability_lost(self) -> bool:
        """True once a durable append has failed for this epoch.

        The in-memory window keeps serving live clients correctly, but the
        epoch is doomed: the next reload rotates the stream so no stale
        cursor can ever be answered from a journal with a silent gap.
        """
        return self._durability_lost

    @property
    def first_retained_seq(self) -> int:
        """Sequence id of the oldest retained event (``last_seq + 1`` when
        nothing is retained)."""
        if self._events:
            return self._events[0][0]
        return self.last_seq + 1

    def retained_events(self) -> List[Tuple[int, str, Dict[str, Any]]]:
        return list(self._events)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp *payload* in place with the replay envelope and journal it.

        Idempotent per event: a payload that already carries ``seq`` (a
        replayed copy) passes through untouched.
        """
        if not isinstance(payload, dict) or "seq" in payload:
            return payload
        self.last_seq += 1
        payload.setdefault("session_id", self.session_id)
        payload["seq"] = self.last_seq
        payload["stream_id"] = self.stream_id
        try:
            payload_json = json.dumps(payload, default=str)
        except (TypeError, ValueError):
            payload_json = json.dumps(
                {"session_id": self.session_id, "seq": self.last_seq}
            )
        self._events.append((self.last_seq, method, payload))
        try:
            self._store.append(self.session_id, self.last_seq, method, payload_json)
        except Exception:
            # The durable tail is now behind the in-memory sequence. Never
            # let this epoch be reloaded: mark it durability-lost so the
            # next reload rotates the stream instead of reusing seqs.
            self._durability_lost = True
        if self._durability_lost and not self._durability_loss_marked:
            self._mark_durability_lost()
        if method in _SNAPSHOT_METHODS:
            self._coalesce_snapshots(method, keep_seq=self.last_seq)
        self._trim_to_retention()
        return payload

    def _mark_durability_lost(self) -> None:
        """Best-effort persist of the dirty marker; retried from ``record``
        on every subsequent append until it sticks (a transient blip
        usually lets the very next attempt through)."""
        mark_dirty = getattr(self._store, "mark_dirty", None)
        if mark_dirty is None:
            return
        try:
            mark_dirty(self.session_id)
        except Exception:
            return
        self._durability_loss_marked = True

    def _coalesce_snapshots(self, method: str, *, keep_seq: int) -> None:
        superseded = [
            seq for seq, meth, _ in self._events if meth == method and seq != keep_seq
        ]
        if not superseded:
            return
        drop = set(superseded)
        self._events = deque(row for row in self._events if row[0] not in drop)
        try:
            self._store.delete_events(self.session_id, sorted(drop))
        except Exception:
            pass

    def _trim_to_retention(self) -> None:
        trimmed_below = None
        while len(self._events) > self.max_events:
            seq, _, _ = self._events.popleft()
            trimmed_below = seq
        if trimmed_below is not None:
            self.compacted_below = max(self.compacted_below, trimmed_below)
            try:
                self._store.set_compacted_below(self.session_id, self.compacted_below)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Replay + acknowledgement
    # ------------------------------------------------------------------

    def _resync_error(self) -> ResyncRequiredError:
        return ResyncRequiredError(
            session_id=self.session_id,
            stream_id=self.stream_id,
            retained_first_seq=self.first_retained_seq,
            retained_last_seq=self.last_seq,
        )

    def events_after(
        self, after_seq: int, *, stream_id: str | None = None
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Retained ``(method, payload)`` pairs with ``seq > after_seq``.

        Raises :class:`ResyncRequiredError` when the cursor cannot be
        served: unknown stream epoch, a nonzero cursor without a
        ``stream_id`` to verify it against (the cursor may belong to a
        previous epoch, so serving it could hand out wrong-epoch events),
        a cursor ahead of the stream, or a cursor below the compaction
        floor. A missing ``stream_id`` with cursor 0 is a fresh resume —
        that is how clients adopt a stream.
        """
        after_seq = int(after_seq)
        if stream_id is None:
            if after_seq > 0:
                raise self._resync_error()
        elif stream_id != self.stream_id:
            raise self._resync_error()
        if after_seq > self.last_seq or after_seq < self.compacted_below:
            raise self._resync_error()
        return [
            (method, payload)
            for seq, method, payload in self._events
            if seq > after_seq
        ]

    def register_consumer(self, consumer: Hashable, *, acked_seq: int = 0) -> None:
        current = self._cursors.get(consumer, 0)
        self._cursors[consumer] = max(current, int(acked_seq))

    def drop_consumer(self, consumer: Hashable) -> None:
        self._cursors.pop(consumer, None)

    def acknowledge(
        self, consumer: Hashable, seq: int, *, stream_id: str | None = None
    ) -> int:
        """Advance *consumer*'s cursor to *seq* and compact fully-acked events.

        Returns the effective (clamped) acknowledged sequence id.
        """
        if stream_id is not None and stream_id != self.stream_id:
            raise self._resync_error()
        effective = min(int(seq), self.last_seq)
        self.register_consumer(consumer, acked_seq=effective)
        self._compact_acknowledged()
        return effective

    def _compact_acknowledged(self) -> None:
        if not self._cursors:
            return
        floor = min(self._cursors.values())
        if floor <= self.compacted_below:
            return
        while self._events and self._events[0][0] <= floor:
            self._events.popleft()
        self.compacted_below = max(self.compacted_below, floor)
        try:
            self._store.set_compacted_below(self.session_id, self.compacted_below)
        except Exception:
            pass

    def destroy(self) -> None:
        """Delete the durable journal (session deletion)."""
        self._events.clear()
        self._cursors.clear()
        try:
            self._store.destroy(self.session_id)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Server-restart reconciliation
    # ------------------------------------------------------------------

    def _reconcile_interrupted_epoch(self) -> None:
        """Close out prompts and runs left open by a dead process.

        A freshly constructed :class:`SessionEventLog` that finds an
        unterminated run (or unresolved approval / client_request events)
        in its durable journal is, by construction, recovering state that
        no live asyncio future backs any more. Synthetic terminal events
        keep replaying clients from waiting forever.
        """
        open_runs: Dict[str, Dict[str, Any]] = {}
        open_tool_approvals: Dict[str, Dict[str, Any]] = {}
        open_mcp_approvals: Dict[str, Dict[str, Any]] = {}
        open_client_requests: Dict[str, Dict[str, Any]] = {}
        for _, method, payload in self._events:
            if method == "run_started":
                run_id = payload.get("run_id")
                if isinstance(run_id, str):
                    open_runs[run_id] = payload
            elif method == "run_end":
                open_runs.pop(str(payload.get("run_id")), None)
            elif method == "tool_approval_requested":
                call_id = payload.get("call_id")
                if isinstance(call_id, str):
                    open_tool_approvals[call_id] = payload
            elif method == "tool_approval_resolved":
                open_tool_approvals.pop(str(payload.get("call_id")), None)
            elif method == "mcp_approval_requested":
                request_id = payload.get("request_id")
                if isinstance(request_id, str):
                    open_mcp_approvals[request_id] = payload
            elif method == "mcp_approval_resolved":
                open_mcp_approvals.pop(str(payload.get("request_id")), None)
            elif method == "client_request":
                request_id = payload.get("request_id")
                if isinstance(request_id, str):
                    open_client_requests[request_id] = payload
            elif method == "client_request_resolved":
                open_client_requests.pop(str(payload.get("request_id")), None)
        for call_id in open_tool_approvals:
            self.record(
                "tool_approval_resolved",
                {
                    "call_id": call_id,
                    "session_id": self.session_id,
                    "reason": "server_restart",
                },
            )
        for request_id in open_mcp_approvals:
            self.record(
                "mcp_approval_resolved",
                {
                    "request_id": request_id,
                    "session_id": self.session_id,
                    "reason": "server_restart",
                },
            )
        for request_id in open_client_requests:
            self.record(
                "client_request_resolved",
                {
                    "request_id": request_id,
                    "session_id": self.session_id,
                    "reason": "server_restart",
                },
            )
        for run_id in open_runs:
            self.record(
                "run_end",
                {
                    "run_id": run_id,
                    "session_id": self.session_id,
                    "end_reason": "server_restart",
                },
            )


# ---------------------------------------------------------------------------
# Emission-site helper
# ---------------------------------------------------------------------------


def stamp_session_event(
    session: Any, method: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Stamp + journal *payload* when *method* is replayable.

    The single chokepoint used by every original emission site
    (``AgentService._broadcast_session``, the runtime bridge broadcast,
    and ``request_client_function``). Replay paths re-send stored stamped
    payloads and must NOT call this again — ``record`` is idempotent per
    payload anyway.
    """
    if method not in REPLAYABLE_METHODS:
        return payload
    log = getattr(session, "event_log", None)
    if log is None:
        return payload
    try:
        return log.record(method, payload)
    except Exception:
        return payload
