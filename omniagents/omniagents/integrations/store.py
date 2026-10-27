"""The durable record of what came in and what it started.

``events`` holds every event an integration delivered, once per delivery
(a provider's redelivery of the same event is recognised and dropped), with
what became of it, and ``event_details`` what else it came with (its trigger
filters and context), so it can be replayed into another factory
(``replay.py``). ``dispatches`` holds each automation run an event
started, ``run_outputs`` the last message of each that finished, and
``grades`` what the project's judges made of the runs they sampled (judges on
live runs, ``grading.py``), ``model_usage`` every model response's tokens
(``omniagents.core.usage``) and ``run_pull_requests`` the pull requests each
run opened or worked on. ``threads`` maps a provider's thread (a Slack
thread, an issue) and the agent it started to the conversation that
continues it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .events import IncomingEvent

__all__ = ["EventStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    event TEXT NOT NULL,
    thread_key TEXT,
    payload TEXT NOT NULL,
    received_at REAL NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    UNIQUE (provider, delivery_id)
);
CREATE TABLE IF NOT EXISTS dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    automation TEXT NOT NULL,
    agent TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS event_details (
    event_id INTEGER PRIMARY KEY REFERENCES events(id),
    filters TEXT NOT NULL,
    context TEXT,
    control TEXT
);
CREATE TABLE IF NOT EXISTS run_turns (
    dispatch_id INTEGER PRIMARY KEY REFERENCES dispatches(id),
    history TEXT NOT NULL, started_at REAL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS run_outputs (
    dispatch_id INTEGER PRIMARY KEY REFERENCES dispatches(id),
    output TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_id INTEGER NOT NULL REFERENCES dispatches(id),
    -- The session graded: the run's own, or that of an agent it called as a tool.
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    judge TEXT NOT NULL,
    passed INTEGER,
    reason TEXT,
    reasoning TEXT,
    graded_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    -- The trace's session (the run's), and what else it says (a judge's dispatch).
    session_id TEXT,
    trace_id TEXT,
    workflow TEXT,
    metadata TEXT NOT NULL,
    agent TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_usage_session ON model_usage (session_id, at);
CREATE TABLE IF NOT EXISTS run_details (
    dispatch_id INTEGER PRIMARY KEY REFERENCES dispatches(id),
    -- The factory's version that ran it (FACTORY_VERSION: its commit), and its input.
    version TEXT,
    prompt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_pull_requests (
    dispatch_id INTEGER NOT NULL REFERENCES dispatches(id),
    repository TEXT NOT NULL,
    number INTEGER NOT NULL,
    -- opened: the run opened it; worked: the run was about it (its event's thread).
    relation TEXT NOT NULL,
    PRIMARY KEY (dispatch_id, repository, number)
);
CREATE TABLE IF NOT EXISTS threads (
    provider TEXT NOT NULL,
    thread_key TEXT NOT NULL,
    agent TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (provider, thread_key, agent)
);
"""


class EventStore:
    """SQLite store; safe to share across the server's tasks and threads."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def mark_interrupted(self) -> int:
        """Dispatches a previous server process left running: that process is gone, so
        they ended with it. Called as a server starts; how many there were."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE dispatches SET status = 'interrupted', error = 'the server stopped before it finished',"
                " finished_at = ? WHERE status = 'running'",
                (time.time(),),
            )
            self._db.commit()
            return cursor.rowcount

    def unsettled(self) -> int:
        """Events not yet matched, and runs not yet finished: work still to come."""
        with self._lock:
            events = self._db.execute(
                "SELECT count(*) FROM events WHERE status = 'received'"
            ).fetchone()[0]
            runs = self._db.execute(
                "SELECT count(*) FROM dispatches WHERE status = 'running'"
            ).fetchone()[0]
        return events + runs

    def running(self) -> int:
        """Dispatches in progress."""
        with self._lock:
            return self._db.execute(
                "SELECT count(*) FROM dispatches WHERE status = 'running'"
            ).fetchone()[0]

    def record(self, event: IncomingEvent) -> Optional[int]:
        """Store ``event``; ``None`` when this delivery was already recorded."""
        with self._lock:
            try:
                cursor = self._db.execute(
                    "INSERT INTO events (provider, delivery_id, event, thread_key,"
                    " payload, received_at, status) VALUES (?, ?, ?, ?, ?, ?, 'received')",
                    (
                        event.provider,
                        event.delivery_id,
                        event.event,
                        event.thread_key,
                        json.dumps(event.payload, default=str),
                        event.received_at,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            event_id = int(cursor.lastrowid)
            self._db.execute(
                "INSERT INTO event_details (event_id, filters, context, control) VALUES (?, ?, ?, ?)",
                (event_id, json.dumps(event.filters), event.context, event.control),
            )
            self._db.commit()
            return event_id

    def set_status(self, event_id: int, status: str, detail: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE events SET status = ?, detail = ? WHERE id = ?",
                (status, detail, event_id),
            )
            self._db.commit()

    def thread_session(
        self, provider: str, thread_key: str, agent: str
    ) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT session_id FROM threads WHERE provider = ? AND thread_key = ?"
                " AND agent = ?",
                (provider, thread_key, agent),
            ).fetchone()
        return row["session_id"] if row else None

    def remember_thread(
        self, provider: str, thread_key: str, agent: str, session_id: str
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO threads (provider, thread_key, agent, session_id,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (provider, thread_key, agent, session_id, time.time()),
            )
            self._db.commit()

    def start_dispatch(
        self,
        event_id: int,
        automation: str,
        agent: str,
        session_id: str,
        *,
        prompt: str = "",
        version: Optional[str] = None,
    ) -> int:
        """A run starting: ``prompt`` is its input, ``version`` the factory's (its commit)."""
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO dispatches (event_id, automation, agent, session_id,"
                " status, started_at) VALUES (?, ?, ?, ?, 'running', ?)",
                (event_id, automation, agent, session_id, time.time()),
            )
            dispatch_id = int(cursor.lastrowid)
            self._db.execute(
                "INSERT INTO run_details (dispatch_id, version, prompt) VALUES (?, ?, ?)",
                (dispatch_id, version, prompt),
            )
            self._db.commit()
            return dispatch_id

    def record_turn(self, dispatch_id, history, started_at, finished_at):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO run_turns VALUES (?, ?, ?, ?)",
                (dispatch_id, json.dumps(history), started_at, finished_at),
            )
            self._db.commit()

    def run_history(self, dispatch_id):
        with self._lock:
            row = self._db.execute(
                "SELECT history FROM run_turns WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
        return json.loads(row["history"]) if row else None

    def run_prompt(self, dispatch_id: int) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT prompt FROM run_details WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
        return row["prompt"] if row else None

    def finish_dispatch(
        self, dispatch_id: int, status: str, error: str | None = None
    ) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE dispatches SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, error, time.time(), dispatch_id),
            )
            self._db.commit()

    def record_output(self, dispatch_id: int, output: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO run_outputs (dispatch_id, output) VALUES (?, ?)",
                (dispatch_id, output),
            )
            self._db.commit()

    def record_grade(
        self,
        dispatch_id: int,
        judge: str,
        passed: Optional[bool],
        reason: str | None,
        reasoning: str | None,
        *,
        session_id: str,
        agent: str,
    ) -> None:
        """A judge's verdict on a session of a run: ``passed`` is ``None`` when it gave none."""
        with self._lock:
            self._db.execute(
                "INSERT INTO grades (dispatch_id, session_id, agent, judge, passed, reason, reasoning, graded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dispatch_id,
                    session_id,
                    agent,
                    judge,
                    None if passed is None else int(passed),
                    reason,
                    reasoning,
                    time.time(),
                ),
            )
            self._db.commit()

    def run_grades(self, dispatch_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT judge, agent, passed, reason FROM grades WHERE dispatch_id = ? ORDER BY id",
                (dispatch_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "passed": None if row["passed"] is None else bool(row["passed"]),
            }
            for row in rows
        ]

    def unmatched_since(self, since: float) -> List[Dict[str, Any]]:
        """The kinds of event received since ``since`` that started nothing (no
        automation matched), with how many of each, most first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT provider, event, count(*) AS n FROM events WHERE received_at >= ? AND status = 'ignored'"
                " GROUP BY provider, event ORDER BY n DESC, provider, event",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_usage(self, record: Dict[str, Any]) -> None:
        """One model response's tokens (a ``UsageRecorder`` sink)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO model_usage (at, session_id, trace_id, workflow, metadata, agent, model, input_tokens,"
                " cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["at"],
                    record.get("session_id"),
                    record.get("trace_id"),
                    record.get("workflow"),
                    json.dumps(record.get("metadata") or {}, default=str),
                    record.get("agent"),
                    record.get("model"),
                    record["input_tokens"],
                    record["cached_tokens"],
                    record["cache_write_tokens"],
                    record["output_tokens"],
                    record["reasoning_tokens"],
                ),
            )
            self._db.commit()

    def run_usage(self, dispatch_id: int) -> List[Dict[str, Any]]:
        """What a run's model calls used, by agent and model: those in its session's
        traces while it ran (its agents, the ones it called as tools, the guardian),
        and its judges' afterwards."""
        with self._lock:
            run = self._db.execute(
                "SELECT d.session_id, COALESCE(t.started_at, d.started_at) AS started_at,"
                " COALESCE(t.finished_at, d.finished_at) AS finished_at, t.dispatch_id AS scoped"
                " FROM dispatches d LEFT JOIN run_turns t ON t.dispatch_id = d.id WHERE d.id = ?",
                (dispatch_id,),
            ).fetchone()
            if run is None:
                return []
            from .architecture import PERSONAL_SESSION

            if run["session_id"] == PERSONAL_SESSION and run["scoped"] is None:
                return []  # Still queued/running: do not count another turn's usage.
            rows = self._db.execute(
                "SELECT agent, model, count(*) AS requests, sum(input_tokens) AS input_tokens,"
                " sum(cached_tokens) AS cached_tokens, sum(cache_write_tokens) AS cache_write_tokens,"
                " sum(output_tokens) AS output_tokens, sum(reasoning_tokens) AS reasoning_tokens FROM model_usage"
                " WHERE (session_id = ? AND at >= ? AND at <= ?)"
                " OR json_extract(metadata, '$.dispatch') = ?"
                " GROUP BY agent, model ORDER BY agent, model",
                (
                    run["session_id"],
                    run["started_at"],
                    run["finished_at"] or time.time(),
                    str(dispatch_id),
                ),
            ).fetchall()
        return [dict(r) for r in rows]

    def link_pull_request(
        self, dispatch_id: int, repository: str, number: int, relation: str
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO run_pull_requests (dispatch_id, repository, number, relation)"
                " VALUES (?, ?, ?, ?)",
                (dispatch_id, repository, int(number), relation),
            )
            self._db.commit()

    def run_pull_requests(self, dispatch_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT repository, number, relation FROM run_pull_requests WHERE dispatch_id = ?"
                " ORDER BY repository, number",
                (dispatch_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def runs_since(self, since: float) -> List[Dict[str, Any]]:
        """The runs that started at or after ``since``, oldest first: what started
        each, how it ended, its last message and its grades."""
        with self._lock:
            rows = self._db.execute(
                "SELECT d.id, d.automation, d.agent, d.session_id, d.status, d.error, d.started_at,"
                " d.finished_at, e.provider, e.event, e.delivery_id, e.thread_key, e.received_at, e.payload,"
                " o.output, x.version FROM dispatches d"
                " JOIN events e ON e.id = d.event_id LEFT JOIN run_outputs o ON o.dispatch_id = d.id"
                " LEFT JOIN run_details x ON x.dispatch_id = d.id"
                " WHERE d.started_at >= ? ORDER BY d.id",
                (since,),
            ).fetchall()
            out = []
            for row in rows:
                grades = self._db.execute(
                    "SELECT judge, agent, session_id, passed, reason, reasoning FROM grades"
                    " WHERE dispatch_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
                run = dict(row)
                run["payload"] = json.loads(run["payload"])
                run["grades"] = [
                    {
                        **dict(g),
                        "passed": None if g["passed"] is None else bool(g["passed"]),
                    }
                    for g in grades
                ]
                out.append(run)
        return out

    def events_since(self, since: float) -> List[IncomingEvent]:
        """The events received at or after ``since`` that started runs, oldest first,
        as they came (for replaying them into another factory)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT e.*, x.filters, x.context, x.control FROM events e"
                " JOIN event_details x ON x.event_id = e.id"
                " WHERE e.received_at >= ? AND EXISTS (SELECT 1 FROM dispatches d WHERE d.event_id = e.id)"
                " ORDER BY e.id",
                (since,),
            ).fetchall()
        return [
            IncomingEvent(
                provider=row["provider"],
                event=row["event"],
                delivery_id=row["delivery_id"],
                payload=json.loads(row["payload"]),
                thread_key=row["thread_key"],
                filters=json.loads(row["filters"]),
                received_at=row["received_at"],
                context=row["context"],
                control=row["control"],
            )
            for row in rows
        ]

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """The latest events with what they started, newest first."""
        with self._lock:
            events = self._db.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            out = []
            for row in events:
                dispatches = self._db.execute(
                    "SELECT automation, agent, session_id, status, error FROM dispatches"
                    " WHERE event_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
                out.append(
                    {
                        "id": row["id"],
                        "provider": row["provider"],
                        "event": row["event"],
                        "delivery_id": row["delivery_id"],
                        "thread_key": row["thread_key"],
                        "received_at": row["received_at"],
                        "status": row["status"],
                        "detail": row["detail"],
                        "dispatches": [dict(d) for d in dispatches],
                    }
                )
        return out

    def close(self) -> None:
        with self._lock:
            self._db.close()
