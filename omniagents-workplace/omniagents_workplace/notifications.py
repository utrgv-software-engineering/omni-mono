"""Notifications to a Microsoft Teams channel, through an incoming webhook.

Workplace sends no email. A deployment that wants approval requests and
alerts posted to a channel sets ``WORKPLACE_TEAMS_WEBHOOK_URL`` to a Teams
workflow's "when a webhook request is received" URL; each notification is an
Adaptive Card. Without it, notifications are only logged.

Notifications carry what happened and who did it, never conversation content.
A failed post is logged and dropped: notifying never blocks the action it
reports.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, environ: Optional[Mapping[str, str]] = None) -> None:
        env = os.environ if environ is None else environ
        self.url = env.get("WORKPLACE_TEAMS_WEBHOOK_URL", "").strip() or None
        if self.url and not self.url.startswith("https://"):
            raise ValueError("WORKPLACE_TEAMS_WEBHOOK_URL must be an HTTPS URL")

    @staticmethod
    def card(title: str, text: str, facts: Sequence[Tuple[str, str]] = ()) -> dict:
        body = [
            {
                "type": "TextBlock",
                "text": title,
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            {"type": "TextBlock", "text": text, "wrap": True},
        ]
        if facts:
            body.append(
                {
                    "type": "FactSet",
                    "facts": [{"title": k, "value": v} for k, v in facts],
                }
            )
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": body,
                    },
                }
            ],
        }

    def send_soon(self, title: str, text: str, facts=()) -> None:
        """Send without waiting: from async code or not."""
        import asyncio
        import threading

        try:
            asyncio.get_running_loop().create_task(self.send(title, text, facts))
        except RuntimeError:
            threading.Thread(
                target=lambda: asyncio.run(self.send(title, text, facts)), daemon=True
            ).start()

    async def send(
        self,
        title: str,
        text: str,
        facts: Sequence[Tuple[str, str]] = (),
        client: Optional[httpx.AsyncClient] = None,
    ) -> bool:
        logger.info("notification: %s: %s", title, text)
        if not self.url:
            return False
        own = client is None
        client = client or httpx.AsyncClient(timeout=10)
        try:
            response = await client.post(self.url, json=self.card(title, text, facts))
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.warning("notification: Teams webhook failed", exc_info=True)
            return False
        finally:
            if own:
                await client.aclose()
