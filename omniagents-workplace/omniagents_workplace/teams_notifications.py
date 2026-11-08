import hashlib
import json
import re
import secrets
import time
from urllib.parse import parse_qsl, unquote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TeamsNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=4000)
    path: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("event_id", "title", "text", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


def workplace_link(base, path):
    parsed = urlsplit(path)
    decoded = unquote(unquote(path))
    parts = urlsplit(decoded)
    roots = {
        "apps",
        "library",
        "factory",
        "automations",
        "agents",
        "chat",
        "shared",
        "canvas",
        "github",
        "linear",
        "slack",
    }
    forbidden = {
        "token",
        "access_token",
        "id_token",
        "signature",
        "sig",
        "key",
        "code",
        "workplace_ticket",
    }
    github_item = re.fullmatch(
        r"/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+/(?:pull|issues)/[1-9][0-9]*", parts.path
    )
    if parts.path.split("/")[1:2] in (["workplace"], ["auth"]):
        github_item = None
    if (
        not path.startswith("/")
        or path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parts.scheme
        or parts.netloc
        or "\\" in decoded
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
        or any(part in (".", "..") for part in parts.path.split("/"))
        or (parts.path.split("/")[1] not in roots and not github_item)
        or any(key.lower() in forbidden for key, _ in parse_qsl(parts.query))
    ):
        raise HTTPException(
            422, "Use a Workplace page path without credentials or redirects"
        )
    return base.rstrip("/") + path


class TeamsNotifications:
    def __init__(self, workplace):
        self.w = workplace
        with self.w.store._lock:
            self.w.store._db.execute(
                "CREATE TABLE IF NOT EXISTS teams_notifications ("
                "id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, "
                "status TEXT NOT NULL, updated_at REAL NOT NULL)"
            )

    def factory(self, request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        config = self.w.factory_config.load() if self.w.factory_config else {}
        app = self.w.apps.by_username((config.get("app") or {}).get("username", ""))
        if (
            scheme.lower() != "bearer"
            or not token
            or not app
            or app.removed
            or not secrets.compare_digest(token, app.memory_token)
        ):
            raise HTTPException(401, "A valid factory token is required")
        return app

    async def send(self, app, body):
        if getattr(self.w, "installation_factory_id", None):
            raise HTTPException(
                409,
                "Teams automation notifications currently require standalone Workplace",
            )
        return await self.deliver(app.id, app.username, body)

    async def system(self, body):
        return await self.deliver("workplace", None, body)

    async def deliver(self, source, actor, body):
        url = (
            workplace_link(self.w.base_url, body.path)
            if body.path is not None
            else None
        )
        notifier = self.w.notifier
        if not notifier.url:
            raise HTTPException(503, "Teams Workflow webhook is not configured")
        destination = hashlib.sha256(notifier.url.encode()).hexdigest()
        identity = hashlib.sha256(
            json.dumps([source, destination, body.event_id]).encode()
        ).hexdigest()
        payload_hash = hashlib.sha256(
            json.dumps(body.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        with self.w.store._lock:
            cursor = self.w.store._db.execute(
                "INSERT OR IGNORE INTO teams_notifications VALUES (?, ?, 'sending', ?)",
                (identity, payload_hash, time.time()),
            )
            if not cursor.rowcount:
                row = self.w.store._db.execute(
                    "SELECT payload_hash, status FROM teams_notifications WHERE id = ?",
                    (identity,),
                ).fetchone()
                if row[0] != payload_hash:
                    raise HTTPException(
                        409, "This event ID already belongs to a different notification"
                    )
                if row[1] != "failed":
                    return {"status": row[1], "duplicate": True, "url": url}
                self.w.store._db.execute(
                    "UPDATE teams_notifications SET status = 'sending', updated_at = ? WHERE id = ?",
                    (time.time(), identity),
                )
        status = "unknown"
        try:
            status = await notifier.deliver(body.title, body.text, url=url)
        finally:
            with self.w.store._lock:
                self.w.store._db.execute(
                    "UPDATE teams_notifications SET status = ?, updated_at = ? WHERE id = ?",
                    (status, time.time(), identity),
                )
            self.w.audit_log.record(
                "notification.teams",
                actor=actor,
                notification=identity,
                status=status,
            )
        return {"status": status, "duplicate": False, "url": url}

    def router(self):
        router = APIRouter()

        @router.post("/factory/notifications/teams")
        async def send(body: TeamsNotification, request: Request):
            return await self.send(self.factory(request), body)

        return router
