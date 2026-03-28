"""Webhook notification provider — posts audit events to an HTTP endpoint.

Uses ``requests`` (already a core dependency) via ``asyncio.to_thread``
to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional, Set

from omniagents.core.providers.notification.base import NotificationProvider


class WebhookNotification(NotificationProvider):
    """Posts matching audit events as JSON to a configured URL.

    Config::

        security:
          providers:
            notification:
              type: webhook
              url: "https://hooks.example.com/omniagents"
              headers:
                Authorization: "Bearer ${WEBHOOK_TOKEN}"
              notify_on:          # optional — empty = all events
                - incident
                - guardrail_triggered
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        cfg = config or {}
        self._url: str = cfg.get("url", "")
        self._headers: Dict[str, str] = dict(cfg.get("headers", {}))
        self._notify_on: Set[str] = set(cfg.get("notify_on", []))

        # Expand ${ENV_VAR} in header values
        for k, v in self._headers.items():
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                self._headers[k] = os.getenv(v[2:-1], "")

    async def notify(self, event: Dict[str, Any]) -> None:
        """Post *event* to the webhook URL if it matches ``notify_on``."""
        if self._notify_on and event.get("event_type") not in self._notify_on:
            return
        if not self._url:
            return

        import requests

        try:
            await asyncio.to_thread(
                requests.post,
                self._url,
                json=event,
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=10,
            )
        except Exception:
            pass  # best-effort — don't block the audit pipeline
