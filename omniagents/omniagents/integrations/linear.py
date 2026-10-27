"""Linear as an event source: agent sessions.

The factory is a Linear agent: an OAuth app installed with ``actor=app``,
which people delegate issues to or @-mention in comments. Each starts an
*agent session*, and Linear POSTs an ``AgentSessionEvent`` webhook to the
factory's ``/integrations/linear/events``: ``created`` for a new session,
``prompted`` when someone replies in it. A webhook is signed
(``linear-signature`` = hex HMAC-SHA256 of the body with the webhook's
secret) and dated (``webhookTimestamp`` in the signed body, accepted within
60 seconds), as ``@linear/sdk`` verifies them.

Events become ``agent_session_created`` and ``agent_session_prompted``,
filtered by ``teams`` (the issue's team, by name or key); a new session's
run is given Linear's ``promptContext`` as it is. A session is the thread: its prompts continue
the conversation it started.

A session expects its agent to report through *activities*, which Linear's
MCP server does not offer, so the server keeps that protocol
(:class:`LinearSessions`): a ``thought`` the moment a session arrives
(Linear marks a session unacknowledged after 10 seconds stale); as the run
goes, an ``action`` for each tool it calls and a ``thought`` for each
message it writes before more work; and when the run ends, its last message
as the ``response``, or an ``error``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .events import IncomingEvent
from .intake import RunResult, RunStep

__all__ = [
    "LINEAR_EVENTS_PATH",
    "LinearConfig",
    "LinearSessions",
    "linear_signature",
    "normalize_linear_event",
    "verify_linear_request",
]

logger = logging.getLogger(__name__)

LINEAR_EVENTS_PATH = "/integrations/linear/events"
_MAX_SKEW_MS = 60_000


@dataclass(frozen=True)
class LinearConfig:
    """``integrations: [{type: linear, webhook_secret: ..., access_token: ...}]``."""

    webhook_secret: str
    access_token: str = ""
    api_url: str = "https://api.linear.app/graphql"


def linear_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_linear_request(
    secret: str, headers: Mapping[str, str], body: bytes, now_ms: Optional[float] = None
) -> bool:
    signature = headers.get("linear-signature") or ""
    if not secret or not hmac.compare_digest(signature, linear_signature(secret, body)):
        return False
    try:
        stamp = json.loads(body).get("webhookTimestamp")
    except ValueError:
        return False
    if not isinstance(stamp, (int, float)):
        return False
    now = now_ms if now_ms is not None else time.time() * 1000
    return abs(now - stamp) <= _MAX_SKEW_MS


def normalize_linear_event(
    delivery_id: str, payload: Mapping[str, Any]
) -> Optional[IncomingEvent]:
    if payload.get("type") != "AgentSessionEvent":
        return None
    action = payload.get("action")
    if action not in ("created", "prompted"):
        return None
    session = payload.get("agentSession") or {}
    issue = session.get("issue") or {}
    team = issue.get("team") or {}
    summary: Dict[str, Any] = {
        "agent_session": {
            "id": session.get("id"),
            "url": session.get("url"),
            "status": session.get("status"),
        },
        "issue": {
            k: issue.get(k) for k in ("identifier", "title", "url", "description")
        },
        "team": {k: team.get(k) for k in ("key", "name")},
        "creator": (session.get("creator") or {}).get("name"),
    }
    if action == "created":
        if session.get("comment"):
            summary["comment"] = (session.get("comment") or {}).get("body")
    else:
        activity = payload.get("agentActivity") or {}
        summary["prompt"] = (activity.get("content") or {}).get("body")
        summary["from"] = (activity.get("user") or {}).get("name")
        if activity.get("signal") == "stop":
            # Someone pressed Stop: the session's run ends; no automation runs.
            return IncomingEvent(
                provider="linear",
                event="agent_session_stopped",
                delivery_id=f"{delivery_id or session.get('id')}:stop:{activity.get('id')}",
                payload=summary,
                thread_key=f"session:{session.get('id')}",
                control="stop",
            )
    teams = [str(v) for v in (team.get("name"), team.get("key")) if v]
    return IncomingEvent(
        provider="linear",
        event=f"agent_session_{action}",
        delivery_id=f"{delivery_id or session.get('id')}:{action}",
        payload=summary,
        thread_key=f"session:{session.get('id')}",
        filters={"teams": teams} if teams else {},
        context=payload.get("promptContext") if action == "created" else None,
    )


_ACTIVITY = (
    "mutation AgentActivityCreate($input: AgentActivityCreateInput!) "
    "{ agentActivityCreate(input: $input) { success agentActivity { id } } }"
)


class LinearSessions:
    """Keeps the agent-session protocol for the factory: acknowledge, then report."""

    def __init__(self, config: LinearConfig):
        self.config = config
        # Per session, the run's latest message: a thought once the run does
        # more, its response if the run ends there.
        self._said: Dict[str, str] = {}

    async def activity(
        self, session_id: str, content: Dict[str, Any], *, ephemeral: bool = False
    ) -> bool:
        import httpx

        if not self.config.access_token or not session_id:
            return False
        token = self.config.access_token
        auth = token if token.startswith("lin_api_") else f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.config.api_url,
                    json={
                        "query": _ACTIVITY,
                        "variables": {
                            "input": {
                                "agentSessionId": session_id,
                                "content": content,
                                "ephemeral": ephemeral,
                            }
                        },
                    },
                    headers={"Authorization": auth, "Content-Type": "application/json"},
                )
            data = response.json()
        except Exception as exc:
            logger.warning(
                "integrations: linear activity for session %s failed: %s",
                session_id,
                exc,
            )
            return False
        if data.get("errors"):
            logger.warning(
                "integrations: linear refused an activity: %s",
                data["errors"][0].get("message"),
            )
            return False
        return True

    async def acknowledge(self, event: IncomingEvent) -> None:
        session = (event.payload.get("agent_session") or {}).get("id")
        issue = (event.payload.get("issue") or {}).get("identifier") or "the issue"
        body = (
            f"Working on {issue}."
            if event.event == "agent_session_created"
            else "Picking up your reply."
        )
        await self.activity(session, {"type": "thought", "body": body})

    async def step(self, event: IncomingEvent, step: RunStep) -> None:
        session = (event.payload.get("agent_session") or {}).get("id")
        if not session:
            return
        if step.kind == "message":
            self._said[session] = step.text or ""
            return
        said = self._said.pop(session, None)
        if said:
            await self.activity(session, {"type": "thought", "body": said})
        if step.kind == "tool":
            await self.activity(
                session, {"type": "action", "action": "Calling", "parameter": step.name}
            )

    async def finished(self, event: IncomingEvent, result: RunResult) -> None:
        session = (event.payload.get("agent_session") or {}).get("id")
        said = self._said.pop(session, None) if session else None
        if result.error and said:
            await self.activity(session, {"type": "thought", "body": said})
        if result.stopped:
            await self.activity(
                session, {"type": "response", "body": "Stopped, as you asked."}
            )
        elif result.error:
            await self.activity(
                session,
                {"type": "error", "body": f"The run did not finish: {result.error}"},
            )
        else:
            await self.activity(
                session, {"type": "response", "body": result.output or "Done."}
            )
