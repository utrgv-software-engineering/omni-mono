"""Client-side replay cursor: dedup + gap detection for sequenced events.

Servers stamp every replayable notification with a replay envelope
(``session_id``, ``stream_id``, ``seq`` — see
``omniagents.core.session.event_log``). Reference clients feed each
incoming notification through a :class:`ReplayCursor` per session:

* ``deliver``        — new event; apply it and the cursor advances.
* ``duplicate``      — already-applied ``seq`` (a replayed copy from the
  legacy pending-events path or an at-least-once redelivery); drop it so
  no duplicate UI items appear.
* ``gap``            — the sequence jumped; the event was applied, but
  earlier events were missed (disconnect window or transport shedding).
  The client should call ``resume_session`` with the pre-gap cursor to
  backfill, then :meth:`apply_resume` the result.
* ``stream_changed`` — the server's stream epoch changed (journal was
  reset). Sequence numbering restarted, so the client must resync its
  full state (``get_session_history``) and adopt the new stream.

Events without an integer ``seq`` (e.g. ``token`` usage deltas) are
transient and always ``deliver`` without moving the cursor.
"""

from __future__ import annotations

import asyncio
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Set,
    Tuple,
)

__all__ = ["ReplayCursor", "ReplayCoordinator", "Observation"]

Observation = Literal["deliver", "duplicate", "gap", "stream_changed"]


class ReplayCursor:
    """Tracks (stream_id, last applied seq) for one session's event stream."""

    def __init__(self) -> None:
        self.stream_id: Optional[str] = None
        self.last_seq: int = 0
        self.gap_count: int = 0

    def observe(self, params: Mapping[str, Any]) -> Observation:
        """Classify an incoming notification payload and advance the cursor."""
        seq = params.get("seq")
        if not isinstance(seq, int):
            return "deliver"
        stream_id = params.get("stream_id")
        if isinstance(stream_id, str) and stream_id != self.stream_id:
            if self.stream_id is None:
                # First sequenced event this cursor has seen: adopt the
                # stream and start counting from here.
                self.stream_id = stream_id
                self.last_seq = seq
                return "deliver"
            self.stream_id = stream_id
            self.last_seq = seq
            return "stream_changed"
        if seq <= self.last_seq:
            return "duplicate"
        if self.last_seq > 0 and seq > self.last_seq + 1:
            # Do NOT advance: the caller either backfills via
            # ``resume_session`` (then ``apply_resume`` advances past the
            # missed window including this event) or opts out of backfill
            # by delivering the event and calling ``force_advance(seq)``.
            self.gap_count += 1
            return "gap"
        self.last_seq = seq
        return "deliver"

    def force_advance(self, seq: int) -> None:
        """Advance the cursor without backfilling (deliver-anyway clients)."""
        if isinstance(seq, int):
            self.last_seq = max(self.last_seq, seq)

    def apply_resume(
        self, result: Mapping[str, Any]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Apply a ``resume_session`` result; return the events to deliver.

        Skips events at or below the current cursor, tolerates
        non-contiguous ``seq`` inside the replay window (journal
        compaction of superseded snapshots), and adopts the advertised
        ``last_seq`` so live gap detection resumes from the stream head.
        """
        stream_id = result.get("stream_id")
        if isinstance(stream_id, str):
            self.stream_id = stream_id
        deliver: List[Tuple[str, Dict[str, Any]]] = []
        for entry in result.get("events") or []:
            if not isinstance(entry, Mapping):
                continue
            method = entry.get("method")
            params = entry.get("params")
            if not isinstance(method, str) or not isinstance(params, Mapping):
                continue
            seq = params.get("seq")
            if isinstance(seq, int):
                if seq <= self.last_seq:
                    continue
                self.last_seq = seq
            deliver.append((method, dict(params)))
        last_seq = result.get("last_seq")
        if isinstance(last_seq, int):
            self.last_seq = max(self.last_seq, last_seq)
        return deliver


ResumeFn = Callable[[str, Optional[str], int], Awaitable[Mapping[str, Any]]]
DeliverFn = Callable[[str, Dict[str, Any]], None]


class ReplayCoordinator:
    """Per-session cursor registry + resume orchestration.

    Python mirror of the TS ``SessionReplayCoordinator`` (see
    ``omniagents/backends/web/ui/src/rpc/replay.ts``): duplicates are
    dropped synchronously; on a gap the triggering event is buffered —
    the cursor is NOT advanced past the missed window — while a
    single-flight ``resume_session`` backfills; live events arriving
    mid-backfill are held and flushed (with dedup) once the resume result
    has been applied. On ``stream_changed`` or a failed resume the
    ``on_resync_required`` callback fires and the held events degrade to
    deliver-anyway (cursor force-advanced) so they can never re-trigger a
    resume loop.

    When constructed without a *resume* callable the coordinator cannot
    backfill: gaps degrade immediately to deliver-anyway (the historical
    behavior of the Python reference client).
    """

    def __init__(
        self,
        resume: ResumeFn | None = None,
        deliver: DeliverFn | None = None,
        on_resync_required: Callable[[str], None] | None = None,
    ) -> None:
        self._resume = resume
        self._deliver = deliver or (lambda method, params: None)
        self.on_resync_required = on_resync_required or (lambda session_id: None)
        self.cursors: Dict[str, ReplayCursor] = {}
        self._resuming: Set[str] = set()
        self._buffered: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}

    def cursor_for(self, session_id: str) -> ReplayCursor:
        cursor = self.cursors.get(session_id)
        if cursor is None:
            cursor = ReplayCursor()
            self.cursors[session_id] = cursor
        return cursor

    def handle(self, method: str, params: Dict[str, Any]) -> bool:
        """Route one incoming notification.

        Returns True when the event was handled here (dropped as a
        duplicate or deferred for backfill); False when the caller should
        deliver it normally.
        """
        session_id = params.get("session_id")
        seq = params.get("seq")
        if not isinstance(session_id, str) or not isinstance(seq, int):
            return False
        cursor = self.cursor_for(session_id)
        if session_id in self._resuming:
            # Hold live events while a backfill is in flight; they are
            # flushed (with dedup) once the resume result is applied.
            self._buffered.setdefault(session_id, []).append((method, params))
            return True
        observation = cursor.observe(params)
        if observation == "deliver":
            return False
        if observation == "duplicate":
            return True
        if observation == "stream_changed":
            self.on_resync_required(session_id)
            return False
        # gap
        if self._resume is None:
            # No backfill path available: deliver anyway (historical
            # Python-client behavior) so consumers are never starved.
            cursor.force_advance(seq)
            return False
        self._buffered[session_id] = [(method, params)]
        self._resuming.add(session_id)
        asyncio.ensure_future(self._run_resume(session_id, cursor))
        return True

    def request_backfill(self, session_id: str) -> bool:
        """Proactively backfill a session from its cursor (reconnect hook).

        Used after a lifecycle reconnect: events emitted while the client
        was away produce no gap trigger until the next live event, so the
        reconnect path asks for the missed tail explicitly. Live events
        arriving mid-backfill are buffered exactly as in the gap path.
        Returns False when there is nothing to do (no resume callable, no
        established cursor, or a backfill already in flight).
        """
        cursor = self.cursor_for(session_id)
        if (
            self._resume is None
            or cursor.stream_id is None
            or cursor.last_seq <= 0
            or session_id in self._resuming
        ):
            return False
        self._buffered[session_id] = []
        self._resuming.add(session_id)
        asyncio.ensure_future(self._run_resume(session_id, cursor))
        return True

    async def _run_resume(self, session_id: str, cursor: ReplayCursor) -> None:
        assert self._resume is not None  # gap path only runs when wired
        failed = False
        try:
            result = await self._resume(session_id, cursor.stream_id, cursor.last_seq)
            for method, payload in cursor.apply_resume(result):
                self._deliver(method, payload)
        except Exception:
            # Resync required (or transport failure): surface to the host,
            # which refetches authoritative state.
            failed = True
            self.on_resync_required(session_id)
        finally:
            held = self._buffered.pop(session_id, [])
            self._resuming.discard(session_id)
            for method, params in held:
                if failed:
                    # Degrade to deliver-anyway: force the cursor past the
                    # held events so they cannot re-trigger a resume loop
                    # while the host performs its full resync.
                    seq = params.get("seq")
                    if isinstance(seq, int):
                        cursor.force_advance(seq)
                    self._deliver(method, params)
                    continue
                if self.handle(method, params):
                    continue
                self._deliver(method, params)
