import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..credentials import decrypt, encrypt

EVENTS = (
    "pull_request_opened",
    "pull_request_updated",
    "pull_request_completed",
    "pull_request_abandoned",
    "pull_request_reactivated",
    "pull_request_thread_changed",
    "pull_request_review_changed",
    "pull_request_status_changed",
    "pull_request_policy_changed",
    "push",
    "refs_changed",
    "work_item_created",
    "work_item_updated",
    "work_item_comment_changed",
    "build_updated",
    "build_completed",
    "build_timeline_changed",
)


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    repositories: list[str] = Field(default_factory=list)
    boards: bool = False
    pipelines: list[int] = Field(default_factory=list)

    @field_validator("project")
    @classmethod
    def project_id(cls, value):
        return str(UUID(value))

    @field_validator("repositories")
    @classmethod
    def repository_ids(cls, values):
        if any(not re.fullmatch(r"[0-9a-fA-F-]{36}", value) for value in values):
            raise ValueError("Repositories must use GUIDs from discovery")
        return sorted({str(UUID(value)) for value in values})

    @field_validator("pipelines")
    @classmethod
    def pipeline_ids(cls, values):
        if any(value <= 0 for value in values):
            raise ValueError("Pipeline IDs must be positive")
        return sorted(set(values))


class ConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    label: str = Field(min_length=1, max_length=120)
    organization: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    auth_type: Literal["pat", "bearer_env", "azcli"] = "pat"
    token: str | None = Field(default=None, min_length=1, max_length=4096)
    credential_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,127}$")
    scopes: list[Scope] = Field(default_factory=list)
    app_ids: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=lambda: list(EVENTS))
    write_tools: list[str] = Field(default_factory=list)
    push_branches: list[str] = Field(default_factory=lambda: ["refs/heads/factory/*"])
    interval_seconds: int = Field(default=60, ge=15, le=3600)
    enabled: bool = False

    @field_validator("scopes")
    @classmethod
    def unique_projects(cls, values):
        if len({value.project for value in values}) != len(values):
            raise ValueError("Each project must have exactly one resource scope")
        return values

    @field_validator("events")
    @classmethod
    def event_names(cls, values):
        if set(values) - set(EVENTS):
            raise ValueError("Unknown Azure DevOps event")
        return sorted(set(values))

    @field_validator("push_branches")
    @classmethod
    def branch_names(cls, values):
        if any(
            not value.startswith("refs/heads/") or any(c in value for c in "\r\n\x00")
            for value in values
        ):
            raise ValueError("Push patterns must be refs/heads/ branch patterns")
        return values


class AzureDevOpsConnections:
    def __init__(self, workplace):
        self.w = workplace
        self.locks: dict[str, asyncio.Lock] = {}
        self.clients = {}
        self.task = None
        self.wake = asyncio.Event()

    def records(self):
        return dict(self.w.store.docs("ado.connection"))

    def get(self, identifier):
        value = self.records().get(identifier)
        if value is None:
            raise HTTPException(404, "Azure DevOps connection not found")
        return value

    def key(self):
        path = self.w.data_dir / "azure-devops.key"
        if not path.exists():
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    output.write(secrets.token_hex(32))
            except FileExistsError:
                pass
        return path.read_text().strip()

    def credential(self, connection):
        if connection["auth_type"] == "pat":
            if not connection.get("sealed_token"):
                raise HTTPException(
                    409, "Connect a PAT before enabling this connection"
                )
            return decrypt(connection["sealed_token"], self.key())
        if connection["auth_type"] == "bearer_env":
            token = self.w.environ.get(connection.get("credential_env") or "", "")
            if not token:
                raise HTTPException(409, "The host bearer credential is unavailable")
            return token
        return None

    def public(self, connection):
        value = {key: val for key, val in connection.items() if key != "sealed_token"}
        value["credential_configured"] = bool(
            connection.get("sealed_token")
            or connection["auth_type"] == "azcli"
            or self.w.environ.get(connection.get("credential_env") or "")
        )
        value["health"] = self.w.store.meta("ado.health." + connection["id"]) or {}
        with self.w.store._lock:
            value["pending_deliveries"] = self.w.store._db.execute(
                "SELECT COUNT(*) FROM factory_events WHERE origin=? AND delivered IS NULL",
                ("ado:" + connection["id"],),
            ).fetchone()[0]
        return value

    def save(self, identifier, body: ConnectionBody):
        from .tools import WRITE_TOOLS

        if set(body.write_tools) - (set(WRITE_TOOLS) | {"git_push"}):
            raise HTTPException(422, "Unknown write capability")
        active = {app.id for app in self.w.apps.active()}
        if set(body.app_ids) - active:
            raise HTTPException(422, "Select active Workplace app IDs")
        if body.enabled and (not body.scopes or not body.app_ids):
            raise HTTPException(
                422, "Select resources and authorized apps before enabling"
            )
        with self.w.store.transaction():
            previous = self.records().get(identifier)
            if body.revision != (previous or {}).get("revision", 0):
                raise HTTPException(409, "Connection changed; reload before saving")
            value = body.model_dump(exclude={"token"})
            value.update(id=identifier, revision=body.revision + 1)
            if previous and (
                body.organization != previous["organization"]
                or body.auth_type != previous["auth_type"]
            ):
                if previous["enabled"]:
                    raise HTTPException(
                        409, "Disable before changing organization or authentication"
                    )
            if body.auth_type == "pat":
                value["sealed_token"] = (
                    encrypt(body.token, self.key())
                    if body.token
                    else (previous or {}).get("sealed_token")
                )
                if (
                    previous
                    and body.organization != previous["organization"]
                    and not body.token
                ):
                    raise HTTPException(
                        422, "Supply a credential for the new organization"
                    )
            if body.enabled:
                self.credential(value)
            identity = json.dumps(
                [value["organization"], value["scopes"]], sort_keys=True
            )
            value["scope_epoch"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
            value["activated_at"] = (previous or {}).get("activated_at") or time.time()
            self.w.store.put("ado.connection", identifier, value)
        self.wake.set()
        return self.public(value)

    def delete(self, identifier, revision):
        with self.w.store.transaction():
            connection = self.get(identifier)
            if connection["revision"] != revision:
                raise HTTPException(409, "Connection changed; reload before deleting")
            self.w.store.delete("ado.connection", identifier)
        self.wake.set()

    def authorize(self, identifier, app_id, project=None, repository=None, tool=None):
        connection = self.get(identifier)
        if not connection["enabled"] or app_id not in connection["app_ids"]:
            raise HTTPException(
                403, "App has no access to this Azure DevOps connection"
            )
        scope = next(
            (scope for scope in connection["scopes"] if scope["project"] == project),
            None,
        )
        if project and not scope:
            raise HTTPException(403, "Project is not selected")
        if repository and (not scope or repository not in scope["repositories"]):
            raise HTTPException(403, "Repository is not selected")
        if tool and tool not in connection["write_tools"]:
            raise HTTPException(403, "Write capability is not enabled")
        return connection

    def app_for_token(self, authorization):
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "A scoped Workplace app bearer token is required")
        supplied = authorization[7:].strip()
        for app in self.w.apps.active():
            if app.memory_token and hmac.compare_digest(supplied, app.memory_token):
                return app.id
        raise HTTPException(401, "A scoped Workplace app bearer token is required")

    def client(self, connection):
        from .client import AzureDevOpsClient

        identifier = connection["id"]
        if identifier not in self.clients:
            self.clients[identifier] = AzureDevOpsClient(self, identifier)
        return self.clients[identifier]

    def assert_current(self, identifier, revision):
        connection = self.get(identifier)
        if not connection["enabled"] or connection["revision"] != revision:
            raise HTTPException(
                409, "Connection changed while polling; retry with current settings"
            )

    async def maintain(self):
        from .polling import poll_connection

        while True:
            for identifier, connection in self.records().items():
                if not connection["enabled"]:
                    continue
                health = self.w.store.meta("ado.health." + identifier) or {}
                if health.get("next_poll", 0) > time.time():
                    continue
                async with self.locks.setdefault(identifier, asyncio.Lock()):
                    try:
                        await poll_connection(self, connection)
                        health.update(last_success=time.time(), error=None, failures=0)
                    except Exception as exc:
                        health["error"] = (
                            exc.detail
                            if isinstance(exc, HTTPException)
                            else "Azure DevOps request failed; inspect credentials, permissions and connectivity"
                        )
                        health["failures"] = health.get("failures", 0) + 1
                    health["next_poll"] = time.time() + min(
                        3600,
                        connection["interval_seconds"]
                        * 2 ** min(health.get("failures", 0), 6),
                    )
                    self.w.store.set_meta("ado.health." + identifier, health)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for client in self.clients.values():
            await client.close()
