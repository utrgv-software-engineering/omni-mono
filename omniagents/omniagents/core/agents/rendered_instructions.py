"""The instructions an agent was last given in a session, for its reviewers.

The guardian treats the acting agent's own instructions (its role, and the
``AGENTS.md`` and skills its context adds) as trusted developer content, as
codex's guardian does its developer messages. They are rendered per turn, so
the builder records the latest rendering per session here.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

__all__ = ["latest_instructions", "record_instructions"]

_MAX_SESSIONS = 256
_latest: "OrderedDict[str, str]" = OrderedDict()


def record_instructions(session_id: Optional[str], text: object) -> None:
    """Remember ``text`` as the instructions the agent acting in ``session_id`` has."""
    if not session_id or not isinstance(text, str) or not text.strip():
        return
    _latest[session_id] = text
    _latest.move_to_end(session_id)
    while len(_latest) > _MAX_SESSIONS:
        _latest.popitem(last=False)


def latest_instructions(session_id: Optional[str]) -> Optional[str]:
    """The instructions last rendered for ``session_id``, if any."""
    return _latest.get(session_id) if session_id else None
