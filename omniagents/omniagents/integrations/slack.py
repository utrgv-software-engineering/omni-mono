"""Slack as an event source: the Events API over HTTP, and Socket Mode.

Slack delivers the same event payloads both ways:

- **HTTP** (``delivery: http``): Slack POSTs each event to the factory's
  ``/integrations/slack/events``, signed with the app's signing secret
  (``X-Slack-Signature`` = ``v0=`` HMAC-SHA256 of
  ``v0:{X-Slack-Request-Timestamp}:{body}``). The server must be reachable
  from Slack.
- **Socket Mode** (``delivery: socket``): the factory opens the connection,
  outbound: it calls ``apps.connections.open`` with the app-level token
  (``xapp-…``) and Slack pushes envelopes down the returned WebSocket, each
  acknowledged by sending back its ``envelope_id``. No public URL is needed.

Slack's MCP server acts with user tokens only, so a factory that replies
through it is a Slack *user*: a dedicated account that authorized the app,
whose messages read "Sent using <app>". The app subscribes to that account's
events (``message.channels``, ``message.groups``, ``message.im``,
``reaction_added`` as user events) and Slack names the account in each
callback's ``authorizations``. A bot-token app's ``app_mention`` events work
too.

Events become :class:`IncomingEvent` with the names automations use:
``app_mention`` (a message that mentions the factory: a bot mention, or a
channel message naming the authorized account), ``message_dm`` (a direct
message to it) and ``reaction_added``. The factory's own messages and
reactions are not events. Filters: ``channels`` (channel ids, and names
when the integration has a ``token`` to read channels with), ``emojis`` and
``users``. The thread key is the team, channel and thread, so replies
in a thread continue the conversation it started.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .events import IncomingEvent

__all__ = [
    "SLACK_EVENTS_PATH",
    "ChannelNames",
    "SlackConfig",
    "SlackSocketMode",
    "normalize_slack_event",
    "slack_signature",
    "verify_slack_request",
]

logger = logging.getLogger(__name__)

SLACK_EVENTS_PATH = "/integrations/slack/events"
_MAX_SKEW_SECONDS = 60 * 5


@dataclass(frozen=True)
class SlackConfig:
    """``integrations: [{type: slack, ...}]`` in ``project.yml``."""

    delivery: str  # "socket" | "http"
    app_token: Optional[str] = None
    signing_secret: Optional[str] = None
    api_url: str = "https://slack.com/api"
    #: A token that reads channels (the app's): with it, an event's ``channels``
    #: filter holds the channel's name beside its id, so an automation can name
    #: the channel it watches (``channels: [factory-intake]``).
    token: Optional[str] = None


class ChannelNames:
    """Channel ids to names, with ``conversations.info``, each looked up once."""

    def __init__(self, config: SlackConfig) -> None:
        self.config = config
        self._names: Dict[str, Optional[str]] = {}

    async def name(self, channel_id: str) -> Optional[str]:
        if not self.config.token or not channel_id:
            return None
        if channel_id not in self._names:
            import httpx

            try:
                # Slack wants an event delivered over HTTP answered within 3 seconds.
                async with httpx.AsyncClient(timeout=2) as client:
                    response = await client.get(
                        f"{self.config.api_url.rstrip('/')}/conversations.info",
                        params={"channel": channel_id},
                        headers={"Authorization": f"Bearer {self.config.token}"},
                    )
                data = response.json()
            except Exception as exc:  # unnamed this time: asked again next time
                logger.warning("integrations: Slack channel %s: %s", channel_id, exc)
                return None
            channel = data.get("channel") or {} if data.get("ok") else {}
            if not data.get("ok"):
                logger.warning(
                    "integrations: conversations.info %s: %s",
                    channel_id,
                    data.get("error"),
                )
            self._names[channel_id] = channel.get("name")
        return self._names[channel_id]

    async def named(self, event: "IncomingEvent") -> "IncomingEvent":
        """``event`` with its channel's name in its ``channels`` filter."""
        ids = event.filters.get("channels") or []
        names = [n for n in [await self.name(i) for i in ids] if n]
        if not names:
            return event
        return dataclasses.replace(
            event, filters={**event.filters, "channels": [*ids, *names]}
        )


def slack_signature(signing_secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()


def verify_slack_request(
    signing_secret: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    now: Optional[float] = None,
) -> bool:
    lowered = {k.lower(): v for k, v in headers.items()}
    timestamp = lowered.get("x-slack-request-timestamp")
    signature = lowered.get("x-slack-signature")
    if not timestamp or not signature:
        return False
    try:
        if (
            abs((now if now is not None else time.time()) - int(timestamp))
            > _MAX_SKEW_SECONDS
        ):
            return False
    except ValueError:
        return False
    expected = slack_signature(signing_secret, timestamp, body)
    return hmac.compare_digest(expected, signature)


def normalize_slack_event(callback: Mapping[str, Any]) -> Optional[IncomingEvent]:
    """An ``event_callback`` as an :class:`IncomingEvent`; ``None`` to ignore it."""
    if callback.get("type") != "event_callback":
        return None
    event = callback.get("event") or {}
    team = str(callback.get("team_id") or "")
    event_type = event.get("type")
    delivery_id = str(callback.get("event_id") or "")
    if not delivery_id:
        return None
    # Messages the app or another bot sent, edits, joins and the like are
    # not requests; nor is anything the factory's own account did.
    if event.get("bot_id") or event.get("subtype"):
        return None
    factory = _authorized_users(callback)
    if event.get("user") and str(event["user"]) in factory:
        return None
    filters: Dict[str, list] = {}
    if event.get("user"):
        filters["users"] = [str(event["user"])]
    text = str(event.get("text") or "")
    if event_type == "app_mention" or (
        event_type == "message"
        and event.get("channel_type") != "im"
        and any(f"<@{user}>" in text or f"<@{user}|" in text for user in factory)
    ):
        name = "app_mention"
        channel = str(event.get("channel") or "")
        thread = str(event.get("thread_ts") or event.get("ts") or "")
    elif event_type == "message" and event.get("channel_type") == "im":
        name = "message_dm"
        channel = str(event.get("channel") or "")
        # A DM conversation continues as one thread unless it is threaded.
        thread = str(event.get("thread_ts") or "dm")
    elif event_type == "reaction_added":
        name = "reaction_added"
        item = event.get("item") or {}
        channel = str(item.get("channel") or "")
        thread = str(item.get("ts") or "")
        if event.get("reaction"):
            filters["emojis"] = [str(event["reaction"])]
    else:
        return None
    if channel:
        filters["channels"] = [channel]
    payload = {
        "team_id": team,
        "event_id": delivery_id,
        "event_time": callback.get("event_time"),
        "event": dict(event),
    }
    return IncomingEvent(
        provider="slack",
        event=name,
        delivery_id=delivery_id,
        payload=payload,
        thread_key=f"{team}:{channel}:{thread}" if channel and thread else None,
        filters=filters,
    )


def _authorized_users(callback: Mapping[str, Any]) -> set:
    """The accounts the app acts as (user installs), from ``authorizations``."""
    return {
        str(a.get("user_id"))
        for a in callback.get("authorizations") or []
        if isinstance(a, Mapping) and a.get("user_id") and not a.get("is_bot")
    }


class SlackSocketMode:
    """A Socket Mode connection: outbound only, reconnecting as Slack asks."""

    def __init__(self, config: SlackConfig, accept, *, retry_seconds: float = 5.0):
        self.config = config
        self._accept = accept
        self._retry_seconds = retry_seconds
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.connected = asyncio.Event()
        self.names = ChannelNames(config)

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _open_url(self) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.config.api_url.rstrip('/')}/apps.connections.open",
                headers={"Authorization": f"Bearer {self.config.app_token}"},
            )
        data = response.json()
        if not data.get("ok") or not data.get("url"):
            raise RuntimeError(f"apps.connections.open failed: {data.get('error')}")
        return str(data["url"])

    async def _run(self) -> None:
        import websockets

        while not self._stopping:
            try:
                url = await self._open_url()
                async with websockets.connect(url) as socket:
                    logger.info("integrations: Slack Socket Mode connected")
                    async for raw in socket:
                        if not await self._handle(socket, raw):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("integrations: Slack Socket Mode: %s", exc)
            finally:
                self.connected.clear()
            if not self._stopping:
                await asyncio.sleep(self._retry_seconds)

    async def _handle(self, socket, raw) -> bool:
        """Handle one frame; ``False`` to reconnect."""
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return True
        kind = message.get("type")
        if kind == "hello":
            self.connected.set()
            return True
        if kind == "disconnect":
            logger.info(
                "integrations: Slack asked to reconnect (%s)", message.get("reason")
            )
            return False
        envelope_id = message.get("envelope_id")
        if envelope_id:
            # Acknowledge first: Slack redelivers what is not acknowledged
            # within 3 seconds, whatever the work it starts.
            await socket.send(json.dumps({"envelope_id": envelope_id}))
        if kind == "events_api":
            event = normalize_slack_event(message.get("payload") or {})
            if event is not None:
                await self._accept(await self.names.named(event))
        return True
