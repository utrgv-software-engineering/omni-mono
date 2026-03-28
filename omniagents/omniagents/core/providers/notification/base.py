"""Base notification provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class NotificationProvider(ABC):
    """Sends notifications when specific audit events occur.

    Enterprise implementations post to webhooks, Slack, PagerDuty, etc.
    The default (no-op) implementation does nothing.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    async def notify(self, event: Dict[str, Any]) -> None:
        """Send a notification for the given audit event."""
        ...
