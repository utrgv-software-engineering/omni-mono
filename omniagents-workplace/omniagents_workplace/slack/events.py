"""Events an installed app receives, delivered the two ways Slack delivers them.

When something happens in the workspace, each app subscribed to it gets an
``event_callback`` (the Events API payload, with ``authorizations`` naming
the account the app acts as), as Slack sends it:

- **Socket Mode** (``socket_mode: true``): over a WebSocket the app opened
  with ``apps.connections.open``, wrapped in an ``events_api`` envelope the
  app must acknowledge with its ``envelope_id`` within 3 seconds, or it is
  sent again (``retry_attempt``, ``retry_reason: timeout``), up to 3 retries.
- **HTTP**: POSTed to the app's ``request_url``, signed with its signing
  secret (``X-Slack-Signature``, ``X-Slack-Request-Timestamp``); a delivery
  that does not get a 2xx within 3 seconds is retried up to 3 times with
  ``X-Slack-Retry-Num`` and ``X-Slack-Retry-Reason``.

Every delivery and its outcome is on the wire.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..wire import Wire
from .workspace import App, Channel, Message, SlackWorkspace

__all__ = ["EventDelivery", "SocketConnection"]

logger = logging.getLogger(__name__)

ACK_TIMEOUT = 3.0
MAX_RETRIES = 3


@dataclass
class SocketConnection:
    """One open Socket Mode connection of an app."""

    app_id: str
    send: Any  # async (text) -> None
    close: Any  # async () -> None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    pending: Dict[str, asyncio.Future] = field(default_factory=dict)

    def acknowledge(self, envelope_id: str) -> bool:
        future = self.pending.pop(envelope_id, None)
        if future is not None and not future.done():
            future.set_result(True)
            return True
        return False


class EventDelivery:
    def __init__(
        self, workspace: SlackWorkspace, wire: Wire, *, retry_delays=(0.0, 1.0, 5.0)
    ):
        self.ws = workspace
        self.wire = wire
        self.retry_delays = retry_delays
        self.connections: Dict[str, List[SocketConnection]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: set = set()
        self._tickets: Dict[str, tuple] = {}
        workspace.subscribe(self._on_change)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Deliver on ``loop`` (the server's); changes may come from any thread."""
        self._loop = loop

    # -- Socket Mode connections ---------------------------------------------

    def issue_ticket(self, app: App) -> str:
        ticket = uuid.uuid4().hex
        self._tickets[ticket] = (app.id, time.time())
        return ticket

    def redeem_ticket(self, ticket: str) -> Optional[App]:
        """A ticket is good once, for 30 seconds, as Slack's are."""
        entry = self._tickets.pop(ticket, None)
        if entry is None or time.time() - entry[1] > 30:
            return None
        return self.ws.apps.get(entry[0])

    def connect(self, connection: SocketConnection) -> None:
        self.connections.setdefault(connection.app_id, []).append(connection)

    def disconnect(self, connection: SocketConnection) -> None:
        conns = self.connections.get(connection.app_id, [])
        if connection in conns:
            conns.remove(connection)
        for future in connection.pending.values():
            if not future.done():
                future.set_result(False)

    def disconnect_app(self, app_id: str) -> None:
        """Close the app's connections (its app-level token changed, or it was removed)."""
        for connection in list(self.connections.get(app_id, [])):
            self.disconnect(connection)
            if self._loop is not None:
                self._loop.call_soon_threadsafe(
                    lambda c=connection: self._loop.create_task(c.close())
                )

    def is_connected(self, app_id: str) -> bool:
        return bool(self.connections.get(app_id))

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # -- what each app is sent ----------------------------------------------

    def _on_change(self, kind: str, data: Dict[str, Any]) -> None:
        for app in self.ws.apps.values():
            event = self._event_for(app, kind, data)
            if event is None:
                continue
            callback = self._callback(app, event)
            if self._loop is None:
                continue
            self._loop.call_soon_threadsafe(self._start, app, callback)

    def _start(self, app: App, callback: Dict[str, Any]) -> None:
        task = asyncio.ensure_future(self.deliver(app, callback))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _event_for(
        self, app: App, kind: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        channel: Optional[Channel] = data.get("channel")
        if channel is None or app.user not in channel.members:
            return None
        if kind in ("message_changed", "message_deleted"):
            return self._change_event(app, kind, data, channel)
        if kind == "message":
            subscription = {
                "channel": "message.channels",
                "group": "message.groups",
                "im": "message.im",
                "mpim": "message.mpim",
            }[channel.event_channel_type]
            if subscription not in app.events:
                return None
            message: Message = data["message"]
            event: Dict[str, Any] = {
                "type": "message",
                "user": message.user,
                "text": message.text,
                "ts": message.ts,
                "team": self.ws.team_id,
                "channel": message.channel,
                "event_ts": message.ts,
                "channel_type": channel.event_channel_type,
            }
            if message.subtype:
                event["subtype"] = message.subtype
            else:
                event["client_msg_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, message.ts))
            if message.thread_ts:
                event["thread_ts"] = message.thread_ts
                parent = next(
                    (
                        m
                        for m in self.ws.messages(message.channel)
                        if m.ts == message.thread_ts
                    ),
                    None,
                )
                if parent is not None:
                    event["parent_user_id"] = parent.user
            return event
        if kind in ("reaction_added", "reaction_removed"):
            if kind not in app.events:
                return None
            message = data["message"]
            return {
                "type": kind,
                "user": data["user"],
                "reaction": data["reaction"],
                "item_user": message.user,
                "item": {
                    "type": "message",
                    "channel": message.channel,
                    "ts": message.ts,
                },
                "event_ts": f"{time.time():.6f}",
            }
        return None

    def _change_event(
        self, app: App, kind: str, data: Dict[str, Any], channel: Channel
    ) -> Optional[Dict[str, Any]]:
        """Slack's ``message_changed`` (an edit) and ``message_deleted`` events."""
        from .api import message_json

        subscription = {
            "channel": "message.channels",
            "group": "message.groups",
            "im": "message.im",
            "mpim": "message.mpim",
        }[channel.event_channel_type]
        if subscription not in app.events:
            return None
        message: Message = data["message"]
        now = f"{time.time():.6f}"
        event: Dict[str, Any] = {
            "type": "message",
            "subtype": kind,
            "hidden": True,
            "channel": channel.id,
            "ts": now,
            "event_ts": now,
            "channel_type": channel.event_channel_type,
        }
        if kind == "message_changed":
            if data.get("previous_text") == message.text:
                return None  # pinned or unpinned: no edit
            event["message"] = message_json(self.ws, message)
            event["previous_message"] = {
                **message_json(self.ws, message),
                "text": data.get("previous_text"),
            }
        else:
            event["deleted_ts"] = message.ts
            event["previous_message"] = message_json(self.ws, message)
        return event

    def _callback(self, app: App, event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "token": "workplace-verification-token",
            "team_id": self.ws.team_id,
            "context_team_id": self.ws.team_id,
            "api_app_id": app.id,
            "event": event,
            "type": "event_callback",
            "event_id": "Ev" + uuid.uuid4().hex[:10].upper(),
            "event_time": int(time.time()),
            "authorizations": [
                {
                    "enterprise_id": None,
                    "team_id": self.ws.team_id,
                    "user_id": app.user,
                    "is_bot": False,
                    "is_enterprise_install": False,
                }
            ],
            "is_ext_shared_channel": False,
            "event_context": uuid.uuid4().hex,
        }

    # -- delivery ------------------------------------------------------------

    async def deliver(self, app: App, callback: Dict[str, Any]) -> bool:
        title = _event_title(callback["event"])
        if app.socket_mode:
            return await self._deliver_socket(app, callback, title)
        if app.request_url:
            return await self._deliver_http(app, callback, title)
        self.wire.record(
            service="slack",
            direction="out",
            kind="event",
            title=title,
            actor=app.name,
            status="error",
            request=callback,
            detail={"error": "the app has neither Socket Mode nor a request URL"},
        )
        return False

    async def _deliver_socket(
        self, app: App, callback: Dict[str, Any], title: str
    ) -> bool:
        entry = self.wire.record(
            service="slack",
            direction="out",
            kind="event",
            title=title,
            actor=app.name,
            status="pending",
            request=callback,
            detail={"delivery": "socket"},
        )
        started = time.monotonic()
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                await asyncio.sleep(
                    self.retry_delays[min(attempt - 1, len(self.retry_delays) - 1)]
                )
            conns = self.connections.get(app.id) or []
            if not conns:
                # Slack holds events for a disconnected app briefly; then they are lost.
                await asyncio.sleep(1.0)
                conns = self.connections.get(app.id) or []
                if not conns:
                    continue
            connection = conns[attempt % len(conns)]
            envelope_id = str(uuid.uuid4())
            envelope = {
                "envelope_id": envelope_id,
                "payload": callback,
                "type": "events_api",
                "accepts_response_payload": False,
                "retry_attempt": attempt,
                "retry_reason": "timeout" if attempt else "",
            }
            future = asyncio.get_running_loop().create_future()
            connection.pending[envelope_id] = future
            try:
                await connection.send(json.dumps(envelope))
                acked = await asyncio.wait_for(future, ACK_TIMEOUT)
            except (asyncio.TimeoutError, Exception):
                acked = False
            finally:
                connection.pending.pop(envelope_id, None)
            if acked:
                self.wire.update(
                    entry,
                    status="ok",
                    response={"envelope_id": envelope_id},
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                    detail={"delivery": "socket", "attempts": attempt + 1},
                )
                return True
        self.wire.update(
            entry,
            status="error",
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            detail={
                "delivery": "socket",
                "error": (
                    "not acknowledged"
                    if self.connections.get(app.id)
                    else "app not connected"
                ),
            },
        )
        return False

    async def _deliver_http(
        self, app: App, callback: Dict[str, Any], title: str
    ) -> bool:
        import httpx

        body = json.dumps(callback).encode()
        entry = self.wire.record(
            service="slack",
            direction="out",
            kind="event",
            title=title,
            actor=app.name,
            status="pending",
            request=callback,
            detail={"delivery": "http", "url": app.request_url},
        )
        started = time.monotonic()
        last_error = ""
        async with httpx.AsyncClient(timeout=ACK_TIMEOUT) as client:
            for attempt in range(MAX_RETRIES + 1):
                if attempt:
                    await asyncio.sleep(
                        self.retry_delays[min(attempt - 1, len(self.retry_delays) - 1)]
                    )
                timestamp = str(int(time.time()))
                headers = {
                    "Content-Type": "application/json",
                    "X-Slack-Request-Timestamp": timestamp,
                    "X-Slack-Signature": sign(app.signing_secret, timestamp, body),
                }
                if attempt:
                    headers["X-Slack-Retry-Num"] = str(attempt)
                    headers["X-Slack-Retry-Reason"] = last_error or "http_timeout"
                try:
                    response = await client.post(
                        app.request_url, content=body, headers=headers
                    )
                except httpx.TimeoutException:
                    last_error = "http_timeout"
                    continue
                except httpx.HTTPError:
                    last_error = "http_error"
                    continue
                if 200 <= response.status_code < 300:
                    self.wire.update(
                        entry,
                        status="ok",
                        response={"status": response.status_code},
                        duration_ms=round((time.monotonic() - started) * 1000, 1),
                        detail={
                            "delivery": "http",
                            "url": app.request_url,
                            "attempts": attempt + 1,
                        },
                    )
                    return True
                last_error = "http_error"
        self.wire.update(
            entry,
            status="error",
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            detail={"delivery": "http", "url": app.request_url, "error": last_error},
        )
        return False


def sign(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _event_title(event: Dict[str, Any]) -> str:
    kind = event.get("type")
    if kind == "message":
        sub = event.get("subtype")
        return f"message.{event.get('channel_type')}" + (f" ({sub})" if sub else "")
    return str(kind)
