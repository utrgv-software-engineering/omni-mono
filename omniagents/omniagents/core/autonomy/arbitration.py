"""Cross-feature tick arbitration.

Multiple autonomy features (goal autopilot, recurring loops, wakeups)
each install their own timers on a session. Some combinations should
not coexist — e.g. a self-scheduled wakeup while a goal autopilot is
driving the session. This module is the neutral meeting point: each
feature *registers* an active tick kind for a session while it is
live, and other features *consult* the registry before installing
their own timer.

The registry is process-local and in-memory, mirroring the lifetime of
the tick machinery itself (rehydration re-registers after a restart).
All operations are idempotent.
"""

from __future__ import annotations

_active_ticks: dict[str, set[str]] = {}


def register_active_tick(session_id: str, kind: str) -> None:
    """Record that ``kind`` (e.g. ``"goal"``, ``"loop"``) has an active
    tick on ``session_id``. Idempotent."""
    if not session_id or not kind:
        return
    _active_ticks.setdefault(session_id, set()).add(kind)


def unregister_active_tick(session_id: str, kind: str) -> None:
    """Remove ``kind`` from ``session_id``'s active ticks. Idempotent —
    unregistering something that was never registered is a no-op."""
    if not session_id or not kind:
        return
    kinds = _active_ticks.get(session_id)
    if kinds is None:
        return
    kinds.discard(kind)
    if not kinds:
        _active_ticks.pop(session_id, None)


def has_active_tick(session_id: str, kind: str) -> bool:
    """True when ``kind`` currently has an active tick on ``session_id``."""
    if not session_id:
        return False
    return kind in _active_ticks.get(session_id, ())


def active_tick_kinds(session_id: str) -> frozenset[str]:
    """The set of tick kinds currently active on ``session_id``."""
    if not session_id:
        return frozenset()
    return frozenset(_active_ticks.get(session_id, ()))
