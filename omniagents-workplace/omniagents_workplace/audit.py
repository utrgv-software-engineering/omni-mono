"""The audit log: who did what, kept in the store and never changed.

Every security and settings event is a row in ``audit_events`` (in a
factory's ``workplace.db``, or the installation's ``installation.db``),
written once: the database refuses updates and deletes. Each is also logged as
JSON to stdout (``security.audit``), which the deployment ships to its log
store (Log Analytics on Azure), where retention is set.

Events carry identifiers, outcomes and reasons, never passwords, cookies,
tokens, or conversation content.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .security import audit as log_event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    event TEXT NOT NULL,
    actor TEXT,
    factory TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_at ON audit_events(at);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
"""


#: Events that are also incidents: an ``alert.<kind>`` event and a notification.
ALERTS = {
    "auth.account_limited": ("sign_in_failures", "Repeated failed sign-ins"),
    "auth.source_limited": ("sign_in_failures", "Repeated failed sign-ins"),
    "factory.personal_data_found": (
        "personal_data",
        "Personal data in a change to a factory",
    ),
    "factory.stopped": ("kill_switch", "Factory stopped"),
    "installation.stopped": ("kill_switch", "All factories stopped"),
    "egress.denied_confidential": (
        "egress",
        "Blocked connection from a Confidential factory",
    ),
}


class AuditLog:
    def __init__(self, store: Any, factory: Optional[str] = None) -> None:
        self.store = store
        #: The factory these events belong to (an installation's factories).
        self.factory = factory
        #: Where incidents are announced (``notifications.Notifier``).
        self.notifier: Any = None
        with store._lock:
            store._db.executescript(_SCHEMA)

    def record(
        self,
        event: str,
        *,
        actor: Optional[str] = None,
        factory: Optional[str] = None,
        **fields: Any,
    ) -> None:
        """Keep ``event``; ``fields`` are identifiers, outcomes and reasons."""
        factory = factory or self.factory
        data = {k: v for k, v in fields.items() if v is not None}
        log_event(event, actor=actor, factory=factory, **data)
        with self.store._lock:
            self.store._db.execute(
                "INSERT INTO audit_events (at, event, actor, factory, data) VALUES (?, ?, ?, ?, ?)",
                (time.time(), event, actor, factory, json.dumps(data, sort_keys=True)),
            )
        if event in ALERTS:
            kind, title = ALERTS[event]
            self.record(f"alert.{kind}", actor=actor, factory=factory, cause=event)
            if self.notifier is not None:
                facts = [(k, str(v)) for k, v in sorted(data.items())][:8]
                if actor:
                    facts.insert(0, ("By", actor))
                self.notifier.send_soon(title, f"{title} ({event}).", facts)

    def events(
        self,
        since: float = 0,
        until: Optional[float] = None,
        event: Optional[str] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM audit_events WHERE at >= ?"
        params: list = [since]
        if until is not None:
            query += " AND at < ?"
            params.append(until)
        if event:
            query += " AND event = ?"
            params.append(event)
        query += " ORDER BY id LIMIT ?"
        params.append(limit)
        with self.store._lock:
            rows = self.store._db.execute(query, params).fetchall()
        return [
            {
                "id": row[0],
                "at": row[1],
                "event": row[2],
                "actor": row[3],
                "factory": row[4],
                **json.loads(row[5]),
            }
            for row in rows
        ]
