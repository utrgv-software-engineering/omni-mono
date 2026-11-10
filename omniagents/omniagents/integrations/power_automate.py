from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from uuid import UUID

from .power_automate_policy import actor_authorized, parse_timestamp
from .power_automate_threads import ThreadActivationStore

logger = logging.getLogger(__name__)


class TransferPending(Exception):
    pass


async def resolve_attachments(connection, event):
    identities = event.payload.get("attachment_ids", [])
    if not isinstance(identities, list) or len(identities) > 100:
        raise ValueError("invalid_attachments")
    attachments = []
    for identity in identities:
        identity = UUID(identity)
        cursor = await connection.execute(
            "SELECT filename, byte_size, state, blob_name, sha256 FROM omni_bridge.attachments WHERE id=%s AND source=%s",
            (identity, event.payload["bridge_source"]),
        )
        row = await cursor.fetchone()
        if row is None or row[2] == "rejected":
            raise ValueError("invalid_attachment")
        if row[2] != "ready":
            raise TransferPending()
        attachments.append(
            {
                "id": str(identity),
                "filename": row[0],
                "byte_size": row[1],
                "blob_name": row[3],
                "sha256": row[4],
            }
        )
    event.payload["attachments"] = attachments


@dataclass(frozen=True)
class PowerAutomateConfig:
    sources: dict[str, dict[str, Any]]
    connection_env: str = "OMNI_POWER_AUTOMATE_DSN"
    poll_seconds: float = 5
    maintenance_seconds: int = 60
    action_timeout_seconds: int = 3600
    transfer_expiry_seconds: int = 604800
    not_before: str | None = None
    bridge_url: str | None = None
    bridge_ca_file: str | None = None
    bridge_cert_file: str | None = None
    bridge_key_file: str | None = None

    @classmethod
    def parse(cls, entry):
        sources = entry.get("sources")
        if not isinstance(sources, dict) or not sources:
            raise ValueError("sources must explicitly authorize inbound sources")
        for source, policy in sources.items():
            from .power_automate_actions import validate_access_policy
            from .power_automate_handlers import validate_handler

            if (
                not isinstance(source, str)
                or not source
                or not isinstance(policy, dict)
            ):
                raise ValueError("invalid source policy")
            validate_access_policy(policy)
            validate_handler(policy)
        interval = float(entry.get("poll_seconds", 5))
        if not 1 <= interval <= 300:
            raise ValueError("poll_seconds must be between 1 and 300")
        connection_env = entry.get("connection_env", "OMNI_POWER_AUTOMATE_DSN")
        if not isinstance(connection_env, str) or not connection_env.isidentifier():
            raise ValueError("invalid connection environment variable")
        settings = {}
        for key, default, lower, upper in (
            ("maintenance_seconds", 60, 10, 3600),
            ("action_timeout_seconds", 3600, 60, 2592000),
            ("transfer_expiry_seconds", 604800, 3600, 7776000),
        ):
            value = entry.get(key, default)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"invalid {key}")
            settings[key] = value
        not_before = entry.get("not_before")
        if not_before is not None:
            parse_timestamp(not_before)
        if entry.get("bridge_url") is not None:
            from urllib.parse import urlsplit

            endpoint = urlsplit(entry["bridge_url"])
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.path not in ("", "/")
                or endpoint.query
                or endpoint.fragment
            ):
                raise ValueError("invalid private bridge URL")
            settings["bridge_url"] = entry["bridge_url"]
            for key in ("bridge_ca_file", "bridge_cert_file", "bridge_key_file"):
                value = entry.get(key)
                if (
                    not isinstance(value, str)
                    or not value
                    or not Path(value).is_absolute()
                ):
                    raise ValueError("private bridge requires absolute TLS file paths")
                settings[key] = value
            if any(policy.get("agent_access") for policy in sources.values()):
                raise ValueError("private bridge agent actions are not configured")
        return cls(sources, connection_env, interval, not_before=not_before, **settings)


def normalize_event(source, delivery_id, payload, config):
    from .power_automate_handlers import normalize_teams

    return normalize_teams(source, delivery_id, payload, config.sources.get(source))


class ReplySpool:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS replies (id TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self.db.commit()

    def enqueue(self, event, result):
        if not result.output or result.error or result.stopped:
            return
        payload = event.payload
        reply = {
            key: payload[key]
            for key in ("tenant_id", "team_id", "channel_id", "root_message_id")
        }
        reply["text"] = result.output[:6000] + (
            "\n[Response shortened; full output is retained in Factory.]"
            if len(result.output) > 6000
            else ""
        )
        reply["bridge_generated"] = True
        identity = hashlib.sha256(
            json.dumps([event.delivery_id, result.output]).encode()
        ).hexdigest()
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO replies VALUES (?, ?, ?)",
                (identity, payload["bridge_source"], json.dumps(reply)),
            )

    def pending(self):
        return self.db.execute(
            "SELECT id, source, payload FROM replies ORDER BY rowid LIMIT 100"
        ).fetchall()

    def acknowledge(self, identity):
        with self.db:
            self.db.execute("DELETE FROM replies WHERE id=?", (identity,))

    def close(self):
        self.db.close()


class PowerAutomateSource:
    def __init__(self, config, accept, spool_path, event_store=None):
        self.config = config
        self.accept = accept
        self.spool = ReplySpool(spool_path)
        self.threads = ThreadActivationStore(self.spool.db)
        from .factory_settings import pin_thread_namespaces

        pin_thread_namespaces(config, self.spool.db)
        self.delivery_lock = asyncio.Lock()
        self.task = None
        self.event_store = event_store
        self.last_maintenance = None
        from .teams_card_conversations import CardConversations

        self.cards = CardConversations(self)

    def register_notification(self, agent, session_id, event_id):
        return self.cards.register(agent, session_id, event_id)

    async def connect(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        connection = os.environ[self.config.connection_env]
        options = conninfo_to_dict(connection)
        if options.get("sslmode") != "verify-full":
            raise ValueError("bridge requires sslmode=verify-full")
        return await psycopg.AsyncConnection.connect(
            connection,
            connect_timeout=10,
            options="-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000",
        )

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.spool.close()

    async def finished(self, event, result):
        if self.reply_authorized(event):
            self.spool.enqueue(event, result)

    def reply_authorized(self, event):
        from .power_automate_policy import activation_scope

        payload = event.payload
        policy = self.config.sources.get(payload.get("bridge_source"), {})
        if not actor_authorized(policy, payload) or any(
            payload.get(key) != policy.get(key)
            for key in ("tenant_id", "team_id", "channel_id")
        ):
            return False
        if policy.get("thread_activation"):
            scope = activation_scope(
                payload["bridge_source"], policy, payload["root_message_id"]
            )
            return self.threads.delivered(scope, event.delivery_id)
        return True

    def recover_replies(self):
        if self.event_store is None:
            return
        for row in self.event_store.pending_bridge_replies():
            try:
                payload = json.loads(row["payload"])
                event = normalize_event(
                    payload["bridge_source"],
                    payload["bridge_event_id"],
                    payload,
                    self.config,
                )
            except (ValueError, TypeError, KeyError):
                event = None
            if event is not None and not self.reply_authorized(event):
                policy = self.config.sources.get(payload["bridge_source"], {})
                if policy.get("thread_activation") and parse_timestamp(
                    payload["created_at"]
                ) >= parse_timestamp(policy["thread_activation"]["not_before"]):
                    from .power_automate_policy import activation_scope

                    scope = activation_scope(
                        payload["bridge_source"], policy, payload["root_message_id"]
                    )
                    if (
                        self.threads.active(scope)
                        or self.threads.pending(scope) == event.delivery_id
                    ):
                        continue
                event = None
            if event is not None:
                self.spool.enqueue(
                    replace(event, delivery_id=row["delivery_id"]),
                    SimpleNamespace(output=row["output"], error=None, stopped=False),
                )
            self.event_store.acknowledge_bridge_reply(
                row["id"], "spooled" if event is not None else "suppressed"
            )

    async def deliver(self, connection):
        async with self.delivery_lock:
            async with connection.transaction():
                await self.requeue_parked(connection)
            await self._deliver(connection)

    async def requeue_parked(self, connection):
        for queue_id, source in self.threads.ready_parked():
            cursor = await connection.execute(
                "UPDATE omni_bridge.inbound_events SET accepted_at=NULL, available_at=now() WHERE id=%s AND source=%s AND accepted_at IS NOT NULL AND rejected_at IS NULL RETURNING id",
                (queue_id, source),
            )
            if await cursor.fetchone() is None:
                cursor = await connection.execute(
                    "SELECT rejected_at FROM omni_bridge.inbound_events WHERE id=%s AND source=%s",
                    (queue_id, source),
                )
                row = await cursor.fetchone()
                if row is None or row[0] is not None:
                    self.threads.forget_parked(queue_id)

    async def _deliver(self, connection):
        from .power_automate_transport import poll_deliveries

        async def accept_delivery(delivery):
            await self.accept_delivery(delivery)
            await self.requeue_parked(connection)

        await poll_deliveries(
            connection,
            set(self.config.sources),
            accept_delivery,
            not_before=self.config.not_before,
        )

    async def accept_delivery(self, delivery):
        from .power_automate_handlers import JsonHandler, TeamsHandler, validate_handler

        policy = self.config.sources.get(delivery.source)
        if policy is None:
            raise ValueError("unauthorized delivery source")
        if validate_handler(policy) == "json":
            event = JsonHandler(policy).prepare(delivery)
            await self.accept(event)
            return
        handler = TeamsHandler(policy, self.threads)
        self.cards.observe(delivery, policy)
        event, scope = handler.prepare(delivery)
        if event is not None:
            event.payload.pop("factory_conversation", None)
            conversation = self.cards.conversation(
                delivery.source, event.payload["root_message_id"]
            )
            if conversation is not None:
                event.payload["factory_conversation"] = conversation
            await self.accept(event)
            handler.accepted(event, scope)

    async def flush_replies(self, connection):
        for identity, source, payload in self.spool.pending():
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO omni_bridge.outbound_actions (id, source, operation, payload) VALUES (%s, %s, 'teams.reply', %s) ON CONFLICT (id) DO NOTHING",
                    (identity, source, payload),
                )
            self.spool.acknowledge(identity)

    async def acquire_delivery_lease(self, connection):
        async with connection.transaction():
            cursor = await connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended('omni-bridge-inbound-owner', 0))"
            )
            row = await cursor.fetchone()
            if row is None or row[0] is not True:
                raise RuntimeError("another bridge runtime owns this database")

    async def run(self):
        while True:
            try:
                async with await self.connect() as connection:
                    await self.acquire_delivery_lease(connection)
                    while True:
                        if (
                            self.last_maintenance is None
                            or time.monotonic() - self.last_maintenance
                            >= self.config.maintenance_seconds
                        ):
                            from .power_automate_maintenance import maintain

                            await maintain(connection, self.config)
                            self.last_maintenance = time.monotonic()
                        await self.deliver(connection)
                        self.recover_replies()
                        await self.flush_replies(connection)
                        await asyncio.sleep(self.config.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Power Automate bridge unavailable; retrying without acknowledging pending events"
                )
            await asyncio.sleep(self.config.poll_seconds)
