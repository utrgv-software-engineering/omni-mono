"""Notifications to a Microsoft Teams channel, through an incoming webhook.

Workplace sends no email. A deployment that wants approval requests and
alerts posted to a channel sets ``WORKPLACE_TEAMS_WEBHOOK_URL`` to a Teams
workflow's "when a webhook request is received" URL; each notification is an
Adaptive Card. Without it, notifications are only logged.

Notifications carry what happened and who did it, never conversation content.
A failed post is reported without logging webhook credentials.
"""

from __future__ import annotations

import logging
import os
import asyncio
from contextvars import ContextVar
from typing import Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)
_sending = ContextVar("teams_webhook_sending", default=False)


class _WebhookLogFilter(logging.Filter):
    def filter(self, record):
        return not _sending.get()


for _name in (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(_name).addFilter(_WebhookLogFilter())


class Notifier:
    def __init__(self, environ: Optional[Mapping[str, str]] = None) -> None:
        env = os.environ if environ is None else environ
        self.url = env.get("WORKPLACE_TEAMS_WEBHOOK_URL", "").strip() or None
        self.proxy_url = None
        if self.url:
            parsed = urlsplit(self.url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or any(char.isspace() or ord(char) < 32 for char in self.url)
            ):
                raise ValueError(
                    "WORKPLACE_TEAMS_WEBHOOK_URL must be an HTTPS URL without userinfo or fragment"
                )

    @staticmethod
    def card(
        title: str,
        text: str,
        facts: Sequence[Tuple[str, str]] = (),
        url: Optional[str] = None,
    ) -> dict:
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
        card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": body,
        }
        if url:
            card["actions"] = [
                {"type": "Action.OpenUrl", "title": "Open in Workplace", "url": url}
            ]
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
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
        return await self.deliver(title, text, facts, client=client) == "accepted"

    async def deliver(self, title, text, facts=(), *, url=None, client=None):
        if not self.url:
            return "not_configured"
        own = client is None
        proxy = self.proxy_url() if self.proxy_url else None
        client = client or httpx.AsyncClient(
            timeout=10, proxy=proxy, trust_env=False, follow_redirects=False
        )
        context = _sending.set(True)
        try:
            for attempt in range(3):
                delay = 2**attempt
                try:
                    response = await client.post(
                        self.url, json=self.card(title, text, facts, url)
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    status = "failed"
                except httpx.InvalidURL:
                    logger.warning("notification: Teams webhook URL is invalid")
                    return "failed"
                except httpx.HTTPError:
                    logger.warning("notification: Teams delivery outcome unknown")
                    return "unknown"
                else:
                    if 200 <= response.status_code < 300:
                        return "accepted"
                    if response.status_code not in (429, 502, 503, 504):
                        logger.warning(
                            "notification: Teams rejected delivery (HTTP %s)",
                            response.status_code,
                        )
                        return "failed"
                    status = "failed"
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        if not retry_after.isdigit() or int(retry_after) > 5:
                            return "failed"
                        delay = int(retry_after)
                if attempt < 2:
                    await asyncio.sleep(delay)
            logger.warning("notification: Teams delivery retries exhausted")
            return status
        finally:
            if own:
                await client.aclose()
            _sending.reset(context)
