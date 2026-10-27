"""The wire: every exchange between the workplace and the outside.

An MCP tool call, a Web API call, an event delivered to an app and its
acknowledgement each become a :class:`WireEntry`. The UI's activity panel
shows them as they happen, so students see the protocol traffic behind
what the factory does: a mention going out as an event, the agent's tool
calls coming back in.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

__all__ = ["Wire", "WireEntry"]


@dataclass
class WireEntry:
    id: int
    at: float
    service: str  # "slack"
    #: "in": a client called the workplace (MCP, Web API, a socket ack);
    #: "out": the workplace called a client (an event delivery).
    direction: str
    kind: str  # "mcp", "api", "event", "socket"
    title: str
    actor: Optional[str] = None  # who: an app or a user
    status: str = "ok"  # "ok", "error", "pending"
    request: Any = None
    response: Any = None
    duration_ms: Optional[float] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Wire:
    def __init__(self, keep: int = 2000):
        self._entries: Deque[WireEntry] = deque(maxlen=keep)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._listeners: List[Callable[[WireEntry], None]] = []

    def record(self, **fields: Any) -> WireEntry:
        entry = WireEntry(id=next(self._ids), at=time.time(), **fields)
        with self._lock:
            self._entries.append(entry)
        self._notify(entry)
        return entry

    def update(self, entry: WireEntry, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(entry, key, value)
        self._notify(entry)

    def recent(self, limit: int = 200) -> List[WireEntry]:
        with self._lock:
            return list(self._entries)[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def subscribe(self, listener: Callable[[WireEntry], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self, entry: WireEntry) -> None:
        for listener in list(self._listeners):
            try:
                listener(entry)
            except Exception:
                pass
