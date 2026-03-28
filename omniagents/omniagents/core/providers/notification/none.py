"""No-op notification provider (default)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from omniagents.core.providers.notification.base import NotificationProvider


class NoneNotification(NotificationProvider):
    """Does nothing — default for open-source deployments."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})

    async def notify(self, event: Dict[str, Any]) -> None:
        pass
