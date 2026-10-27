"""The event every integration source produces, whatever the provider."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["IncomingEvent"]


@dataclass(frozen=True)
class IncomingEvent:
    """One event from an integration (a Slack mention, a GitHub webhook...).

    ``event`` is the name automation ``triggers`` use (``app_mention``,
    ``pull_request_opened``...). ``delivery_id`` is the provider's id for
    this delivery; a redelivery carries the same one, so it is recorded
    once. ``thread_key`` names the conversation the event belongs to (a
    Slack thread, an issue): later events with the same key continue that
    conversation. ``filters`` holds the values trigger filters compare
    (``channels``, ``emojis``, ``labels``...). ``payload`` is what the agent
    is given, with ``context`` when the source has some.
    """

    provider: str
    event: str
    delivery_id: str
    payload: Dict[str, Any]
    thread_key: Optional[str] = None
    filters: Dict[str, List[str]] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)
    #: Text the source wrote for the agent (Linear's ``promptContext``),
    #: given to the run as it is.
    context: Optional[str] = None
    #: ``"stop"``: someone stopped the conversation (Linear's stop signal):
    #: its run in progress is cancelled and no run starts.
    control: Optional[str] = None
