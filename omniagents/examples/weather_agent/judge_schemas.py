from __future__ import annotations

from typing_extensions import TypedDict


class FriendlinessDecision(TypedDict):
    is_friendly: bool
    explanation: str

