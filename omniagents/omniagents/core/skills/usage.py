"""The skills a session's agent was offered and loaded, for a run's record.

Offered skills are rendered into the agent's instructions each turn; loaded
ones are those its ``load_skill`` read. Both are noted here per session with
when they happened, and a factory takes those of a run's own turn when it
finishes (:func:`since`). Paths are where the skill lives (on the AgentHost
when it has a copy there), so a run can say where each came from.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional

__all__ = ["record_loaded", "record_offered", "since"]

_MAX_SESSIONS = 256
_lock = threading.Lock()
_offered: "OrderedDict[str, tuple[float, List[Dict[str, str]]]]" = OrderedDict()
_loaded: "OrderedDict[str, List[tuple[float, Dict[str, str]]]]" = OrderedDict()


def _trim(table: "OrderedDict[str, Any]", session_id: str) -> None:
    table.move_to_end(session_id)
    while len(table) > _MAX_SESSIONS:
        table.popitem(last=False)


def record_offered(session_id: Optional[str], skills: Iterable[Any]) -> None:
    """``skills`` (:class:`~omniagents.core.skills.placement.RunSkill`) were offered now."""
    if not session_id:
        return
    listed = [
        {"name": s.name, "path": str(s.host_dir or s.path)}
        for s in skills
        if not getattr(s, "error", None)
    ]
    with _lock:
        _offered[session_id] = (time.time(), listed)
        _trim(_offered, session_id)


def record_loaded(session_id: Optional[str], name: str, path: str) -> None:
    """The skill ``name`` (its folder ``path``) was loaded now."""
    if not session_id:
        return
    with _lock:
        _loaded.setdefault(session_id, []).append(
            (time.time(), {"name": name, "path": path})
        )
        _trim(_loaded, session_id)


def since(session_id: str, started: float) -> Dict[str, List[Dict[str, str]]]:
    """What ``session_id``'s agent was offered and loaded from ``started`` on."""
    with _lock:
        at, offered = _offered.get(session_id, (0.0, []))
        loaded = [s for t, s in _loaded.get(session_id, []) if t >= started]
    return {"offered": list(offered) if at >= started else [], "loaded": loaded}
