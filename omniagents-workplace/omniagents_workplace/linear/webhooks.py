"""Linear's webhooks for agents: ``AgentSessionEvent``, delivered to the app.

A session created (an issue delegated to the app, or the app @-mentioned in
a comment) or prompted (a person replied in the session's thread) is POSTed
to the app's webhook with Linear's headers (``linear-delivery``,
``linear-event``, ``linear-signature``: the hex HMAC-SHA256 of the body with
the webhook's secret) and the payload ``@linear/sdk`` types as
``AgentSessionEventWebhookPayload`` (``reference/linear_agent_sessions.md``).
A ``created`` session the app does not acknowledge with an activity within
10 seconds goes stale, as on Linear.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional, Set

from ..wire import Wire
from .state import Activity, LinearState, LinearWebhook, Session, iso

__all__ = ["LinearWebhook", "LinearWebhookDelivery", "sign"]

ACKNOWLEDGE_SECONDS = 10.0


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class LinearWebhookDelivery:
    def __init__(self, state: LinearState, wire: Wire, base_url: Callable[[], str]):
        self.state = state
        self.wire = wire
        self.base_url = base_url
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: Set[asyncio.Task] = set()
        state.subscribe(self._on_change)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- events -----------------------------------------------------------------

    def _on_change(self, kind: str, data: Dict[str, Any]) -> None:
        if kind == "session_created":
            session = data["session"]
            timer = threading.Timer(
                ACKNOWLEDGE_SECONDS + 0.5,
                self.state.mark_stale,
                kwargs={"max_wait": ACKNOWLEDGE_SECONDS},
            )
            timer.daemon = True
            timer.start()
            self._send(session, "created")
        elif kind == "session_prompted":
            self._send(data["session"], "prompted", data["activity"])

    def _send(
        self, session: Session, action: str, activity: Optional[Activity] = None
    ) -> None:
        if self._loop is None:
            return
        for hook in self.state.hooks:
            if hook.app_user_id in (None, session.app_user_id):
                payload = self.payload(session, action, activity, hook)
                self._loop.call_soon_threadsafe(self._start, hook, payload)

    def payload(
        self,
        session: Session,
        action: str,
        activity: Optional[Activity],
        hook: LinearWebhook,
    ) -> Dict[str, Any]:
        s = self.state
        issue = s.issues[session.issue_id]
        team = s.teams[issue.team_id]
        creator = s.users.get(session.creator_id)
        base = self.base_url().rstrip("/")
        comment = s.comments.get(session.comment_id or "")

        def user(u):
            return (
                {
                    "id": u.id,
                    "name": u.name,
                    "email": u.email,
                    "avatarUrl": None,
                    "url": f"{base}/linear/{s.url_key}/profiles/{u.display_name}",
                }
                if u
                else None
            )

        now = time.time()
        out: Dict[str, Any] = {
            "type": "AgentSessionEvent",
            "action": action,
            "createdAt": iso(now),
            "organizationId": s.org_id,
            "oauthClientId": s.oauth_client_id,
            "appUserId": session.app_user_id,
            "agentSession": {
                "id": session.id,
                "createdAt": iso(session.created_at),
                "updatedAt": iso(session.updated_at),
                "archivedAt": None,
                "startedAt": iso(session.started_at),
                "endedAt": iso(session.ended_at),
                "status": session.status,
                "type": "commentThread",
                "summary": None,
                "sourceMetadata": None,
                "appUserId": session.app_user_id,
                "organizationId": s.org_id,
                "creatorId": session.creator_id,
                "creator": user(creator),
                "issueId": issue.id,
                "issue": {
                    "id": issue.id,
                    "identifier": issue.identifier,
                    "title": issue.title,
                    "description": issue.description,
                    "url": s.issue_url(base, issue),
                    "teamId": team.id,
                    "team": {"id": team.id, "key": team.key, "name": team.name},
                },
                "commentId": session.comment_id,
                "comment": (
                    {
                        "id": comment.id,
                        "body": comment.body,
                        "userId": comment.user_id,
                        "issueId": comment.issue_id,
                    }
                    if comment and comment.body
                    else None
                ),
                "sourceCommentId": (
                    session.comment_id if comment and comment.body else None
                ),
                "url": f"{s.issue_url(base, issue)}#agent-session-{session.id}",
            },
            "guidance": [],
            "webhookId": hook.id,
            "webhookTimestamp": int(now * 1000),
        }
        if action == "created":
            out["promptContext"] = s.prompt_context(session)
            out["previousComments"] = []
        if activity is not None:
            author = s.users.get(activity.user_id or "")
            out["agentActivity"] = {
                "id": activity.id,
                "agentSessionId": session.id,
                "content": activity.content,
                "signal": activity.signal,
                "signalMetadata": None,
                "sourceCommentId": None,
                "userId": activity.user_id,
                "user": user(author),
                "createdAt": iso(activity.created_at),
                "updatedAt": iso(activity.created_at),
                "archivedAt": None,
            }
        return out

    # -- delivery -------------------------------------------------------------------

    def _start(self, hook: LinearWebhook, payload: Dict[str, Any]) -> None:
        task = asyncio.ensure_future(self.deliver(hook, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver(self, hook: LinearWebhook, payload: Dict[str, Any]) -> bool:
        import httpx

        body = json.dumps(payload).encode()
        delivery = str(uuid.uuid4())
        session = payload["agentSession"]
        entry = self.wire.record(
            service="linear",
            direction="out",
            kind="event",
            title=f"AgentSessionEvent.{payload['action']}",
            actor=session["issue"]["identifier"],
            status="pending",
            request=payload,
            detail={"url": hook.url, "delivery": delivery},
        )
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Linear-Webhook",
            "linear-delivery": delivery,
            "linear-event": "AgentSessionEvent",
        }
        if hook.secret:
            headers["linear-signature"] = sign(hook.secret, body)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(hook.url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            self.wire.update(
                entry,
                status="error",
                response=f"{type(exc).__name__}: could not reach {hook.url}",
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return False
        ok = 200 <= response.status_code < 300
        self.wire.update(
            entry,
            status="ok" if ok else "error",
            response={"status": response.status_code, "body": response.text[:300]},
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return ok

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
