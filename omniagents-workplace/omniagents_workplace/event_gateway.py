"""Factory-owned durable delivery; provider stand-ins retain their own semantics."""

import asyncio
import hashlib
import hmac
import json
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)


class EventGateway:
    def __init__(self, workplace):
        self.w = workplace
        self.task = None
        self.socket_task = None
        self.socket_identity = None
        self.wake = asyncio.Event()
        with self.w.store._lock:
            self.w.store._db.execute("PRAGMA synchronous=FULL")
            self.w.store._db.executescript("""
                CREATE TABLE IF NOT EXISTS factory_events (
                    id INTEGER PRIMARY KEY, origin TEXT NOT NULL,
                    provider TEXT NOT NULL, delivery TEXT NOT NULL,
                    body BLOB NOT NULL, headers TEXT NOT NULL,
                    received REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    delivered REAL, error TEXT,
                    UNIQUE(origin, provider, delivery));
                CREATE INDEX IF NOT EXISTS factory_events_pending
                    ON factory_events(delivered, next_attempt);
            """)

    def config(self):
        return self.w.factory_config.load() if self.w.factory_config else {}

    def app(self):
        return self.w.apps.by_username(self.config().get("app", {}).get("username", ""))

    def enabled(self):
        return bool(
            self.app() and self.config().get("event_gateway", {}).get("enabled", True)
        )

    def endpoint(self, provider):
        return (
            (self.w.internal_url or self.w.base_url).rstrip("/")
            + "/factory/events/"
            + provider
        )

    def configure_destinations(self):
        """Repoint only delivery URLs, including adopted factories; never reseed."""
        app = self.app()
        if not app:
            return
        config = self.config().get("app", {})
        current = self.w.apps.settings(app)
        changes = {}
        for provider, key, actual in (
            ("github", "github_webhook", current["github"]["webhook_url"]),
            ("linear", "linear_webhook", current["linear"]["webhook_url"]),
            ("memory", "memory_webhook", app.memory_webhook),
            ("slack", "slack_request_url", current["slack"]["request_url"]),
        ):
            configured = config.get(key)
            if not configured and not actual:
                continue
            target = self.endpoint(provider) if self.enabled() else configured
            if target is not None and target != actual:
                argument = key if provider == "slack" else key + "_url"
                changes[argument] = target
        if changes:
            self.w.apps.configure(app, **changes)

    def source(self, provider):
        app = self.app()
        if not app:
            raise ValueError("Factory app is not configured")
        cfg = self.config().get("event_gateway", {}).get(provider, {})
        if cfg:

            def env(key):
                return self.w.environ.get(cfg.get(key + "_env", ""), "")

            return dict(
                secret=env("signing_secret"),
                app_token=env("app_token"),
                token=env("token"),
                api_url=cfg.get(
                    "api_url",
                    (
                        "https://slack.com/api"
                        if provider == "slack"
                        else "https://api.linear.app/graphql"
                    ),
                ),
            )
        if provider == "slack":
            s = self.w.slack.apps[app.slack_app_id]
            return dict(
                secret=s.signing_secret,
                app_token=s.app_token if s.socket_mode else "",
                token=s.user_token,
                api_url=(self.w.internal_url or self.w.base_url).rstrip("/")
                + "/slack/api",
            )
        if provider == "github":
            hook = self.w.apps._github_hook(app)
        elif provider == "linear":
            hook = self.w.apps._linear_hook(app)
        elif provider == "memory":
            return dict(secret=app.memory_webhook_secret)
        else:
            raise ValueError("Unsupported provider")
        return dict(secret=hook.secret if hook else "")

    def environment(self):
        if not self.enabled():
            return {"OMNIAGENTS_EVENT_GATEWAY": "0"}
        env = {"OMNIAGENTS_EVENT_GATEWAY": "1"}
        slack = self.source("slack")
        env.update(
            SLACK_SIGNING_SECRET=slack["secret"],
            SLACK_APP_TOKEN=slack["app_token"] or "",
            SLACK_API_URL=slack["api_url"],
            OMNIAGENTS_GATEWAY_SLACK_TOKEN=slack["token"] or "",
        )
        cfg = self.config().get("event_gateway", {})
        if cfg.get("linear"):
            linear = self.source("linear")
            env.update(
                LINEAR_WEBHOOK_SECRET=linear["secret"],
                LINEAR_ACCESS_TOKEN=linear["token"],
                LINEAR_API_URL=linear["api_url"],
            )
        return env

    def enqueue(self, origin, provider, delivery, body, headers):
        if not delivery or len(delivery) > 512:
            raise HTTPException(400, "A stable provider delivery ID is required")
        # No auth tokens or signatures are needed in the queue. Signing credentials
        # are resolved on each attempt, so rotations do not strand pending events.
        headers = {
            k: v for k, v in headers.items() if k in ("x-github-event", "linear-event")
        }
        with self.w.store.transaction():
            self.w.store._db.execute(
                "INSERT OR IGNORE INTO factory_events(origin,provider,delivery,body,headers,received) VALUES(?,?,?,?,?,?)",
                (origin, provider, delivery, body, json.dumps(headers), time.time()),
            )
        loop = getattr(self, "loop", None)
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self.wake.set)
        else:
            self.wake.set()

    def secret(self, origin, provider):
        if origin == "real-github":
            settings = self.w.repository_settings
            if settings.settings()["provider"] != "github":
                raise ValueError("Repository provider changed; delivery retained")
            return settings.oauth_config().get("webhook_secret") or self.w.environ.get(
                "WORKPLACE_GITHUB_WEBHOOK_SECRET", ""
            )
        if (
            provider == "github"
            and self.w.repository_settings.settings()["provider"] != "standin"
        ):
            raise ValueError("Repository provider changed; delivery retained")
        if origin != self.origin(provider):
            raise ValueError("Event source changed; delivery retained")
        return self.source(provider)["secret"]

    def origin(self, provider):
        # Distinguish app replacement and switching between local and external
        # accounts without binding queued events to rotating signing credentials.
        cfg = self.config().get("event_gateway", {}).get(provider, {})
        scope = json.dumps(cfg, sort_keys=True)
        return self.app().id + ":" + hashlib.sha256(scope.encode()).hexdigest()[:16]

    def signed(self, row):
        _, origin, provider, delivery, body, headers, *_ = row
        if provider == "shared":
            secret = self.config().get("factory", {}).get("auth_token", "")
            if not secret:
                raise ValueError("Factory runtime is not configured")
            return body, {
                "content-type": "application/json",
                "Authorization": "Bearer " + secret,
            }
        secret = self.secret(origin, provider)
        if not secret:
            raise ValueError("Signing secret is not configured; delivery retained")
        payload = json.loads(body)
        if provider == "github":
            settings = self.w.repository_settings.settings()
            if (
                payload.get("repository", {}).get("full_name")
                not in settings["repositories"]
            ):
                raise ValueError("Repository is no longer selected; delivery retained")
        headers = json.loads(headers)
        headers["content-type"] = "application/json"
        if provider == "linear":
            payload["webhookTimestamp"] = int(time.time() * 1000)
            body = json.dumps(payload).encode()
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if provider == "github":
            headers.update(
                {
                    "x-github-delivery": delivery,
                    "x-hub-signature-256": "sha256=" + digest,
                }
            )
        elif provider == "linear":
            headers.update({"linear-delivery": delivery, "linear-signature": digest})
        elif provider == "memory":
            app = self.app()
            if payload.get("memory", {}).get("store") not in {
                s["id"] for s in self.w.memory.stores(app.id)
            }:
                raise ValueError("Memory access changed; delivery retained")
            headers.update({"memory-delivery": delivery, "memory-signature": digest})
        elif provider == "slack":
            timestamp = str(int(time.time()))
            signature = hmac.new(
                secret.encode(),
                b"v0:" + timestamp.encode() + b":" + body,
                hashlib.sha256,
            ).hexdigest()
            headers.update(
                {
                    "x-slack-request-timestamp": timestamp,
                    "x-slack-signature": "v0=" + signature,
                }
            )
        return body, headers

    async def deliver_pending(self, client):
        with self.w.store._lock:
            rows = self.w.store._db.execute(
                "SELECT * FROM factory_events WHERE delivered IS NULL AND next_attempt<=? ORDER BY id LIMIT 20",
                (time.time(),),
            ).fetchall()
        for row in rows:
            error = None
            try:
                if not self.enabled():
                    raise ValueError("Gateway disabled; delivery retained")
                if row[2] == "shared":
                    payload = json.loads(row[4])
                    rule = self.w.shared.doc("rule", payload["workflow"]["id"])
                    if (
                        not rule
                        or not rule["enabled"]
                        or rule["revision"] != payload["workflow"]["revision"]
                    ):
                        with self.w.store.transaction():
                            self.w.store._db.execute(
                                "UPDATE factory_events SET delivered=?,error=? WHERE id=?",
                                (
                                    time.time(),
                                    "Cancelled: workflow changed or disabled",
                                    row[0],
                                ),
                            )
                        continue
                body, headers = self.signed(row)
                url = self.config().get("factory", {}).get("url", "")
                if not url:
                    raise ValueError("Factory runtime is not configured")
                response = await client.post(
                    url.rstrip("/") + "/integrations/" + row[2] + "/events",
                    content=body,
                    headers=headers,
                )
                if not 200 <= response.status_code < 300:
                    raise ValueError("Runtime HTTP " + str(response.status_code))
            except (httpx.HTTPError, ValueError) as exc:
                error = (
                    type(exc).__name__ if isinstance(exc, httpx.HTTPError) else str(exc)
                )
            attempts = row[7] + 1
            with self.w.store.transaction():
                self.w.store._db.execute(
                    "UPDATE factory_events SET attempts=?, next_attempt=?, delivered=?, error=? WHERE id=?",
                    (
                        attempts,
                        time.time() + min(60, 2 ** min(attempts, 6)),
                        None if error else time.time(),
                        error,
                        row[0],
                    ),
                )

    async def socket_loop(self, source, origin):
        import websockets

        while True:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    result = await client.post(
                        source["api_url"].rstrip("/") + "/apps.connections.open",
                        headers={"Authorization": "Bearer " + source["app_token"]},
                    )
                    data = result.json()
                    if not data.get("ok") or not data.get("url"):
                        raise ValueError("Slack Socket Mode connection refused")
                async with websockets.connect(data["url"]) as socket:
                    async for raw in socket:
                        message = json.loads(raw)
                        if message.get("type") == "disconnect":
                            break
                        await self.socket_message(socket, message, origin)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Factory gateway Slack reconnect: %s", type(exc).__name__)
            await asyncio.sleep(2)

    async def socket_message(self, socket, message, origin):
        if message.get("type") == "events_api":
            callback = message.get("payload") or {}
            self.enqueue(
                origin,
                "slack",
                callback.get("event_id", ""),
                json.dumps(callback).encode(),
                {},
            )
        if message.get("envelope_id"):
            # SQLite commit above must succeed before the provider receives ACK.
            await socket.send(json.dumps({"envelope_id": message["envelope_id"]}))

    async def sync_socket(self, client):
        source = self.source("slack") if self.enabled() else {}
        # Never compete with an older runtime's Socket Mode connection. Once a
        # gateway-aware runtime hands off ingestion, retain it through downtime.
        ready = self.w.store.meta("event_gateway.runtime_ready", False)
        if self.enabled():
            factory = self.config().get("factory", {})
            try:
                response = await client.get(
                    factory["url"].rstrip("/") + "/integrations/architecture",
                    headers={
                        "Authorization": "Bearer " + factory.get("auth_token", "")
                    },
                )
                response.raise_for_status()
                ready = response.json().get("event_gateway") is True
                self.w.store.set_meta("event_gateway.runtime_ready", ready)
            except (httpx.HTTPError, KeyError, ValueError):
                pass
        identity = (
            (self.origin("slack"), json.dumps(source, sort_keys=True))
            if source.get("app_token") and ready
            else None
        )
        if identity == self.socket_identity:
            return
        if self.socket_task:
            self.socket_task.cancel()
            await asyncio.gather(self.socket_task, return_exceptions=True)
        self.socket_identity = identity
        self.socket_task = (
            asyncio.create_task(self.socket_loop(source, identity[0]))
            if identity
            else None
        )

    async def run(self):
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            while True:
                self.wake.clear()
                try:
                    await self.sync_socket(client)
                    await self.deliver_pending(client)
                except Exception as exc:
                    log.error("Factory event gateway: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self.run())

    async def close(self):
        tasks = [t for t in (self.task, self.socket_task) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def router(self):
        router = APIRouter()

        @router.get("/workplace/api/factory/events")
        async def status(request: Request):
            if (
                not getattr(request.state, "account", None)
                or not request.state.account.admin
            ):
                raise HTTPException(403, "Administrator access required")
            with self.w.store._lock:
                rows = self.w.store._db.execute(
                    "SELECT id,provider,delivery,received,attempts,delivered,error FROM factory_events WHERE delivered IS NULL ORDER BY id LIMIT 100"
                ).fetchall()
                pending = self.w.store._db.execute(
                    "SELECT count(*) FROM factory_events WHERE delivered IS NULL"
                ).fetchone()[0]
            return {
                "pending": pending,
                "events": [
                    dict(
                        zip(
                            (
                                "id",
                                "provider",
                                "delivery",
                                "received",
                                "attempts",
                                "delivered",
                                "error",
                            ),
                            row,
                        )
                    )
                    for row in rows
                ],
            }

        @router.post("/factory/events/{provider}")
        async def accept(provider: str, request: Request):
            if (
                provider not in {"github", "slack", "linear", "memory"}
                or not self.enabled()
            ):
                raise HTTPException(404, "Factory gateway is not configured")
            body = await request.body()
            if len(body) > 2 * 1024 * 1024:
                raise HTTPException(413, "Event exceeds 2 MiB")
            origin = self.origin(provider)
            try:
                secret = self.secret(origin, provider)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Expected event object")
            except (ValueError, TypeError):
                raise HTTPException(400, "Invalid event or inactive source") from None
            headers = request.headers
            digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if provider == "github":
                valid = hmac.compare_digest(
                    headers.get("x-hub-signature-256", ""), "sha256=" + digest
                )
                delivery = headers.get("x-github-delivery", "")
            elif provider == "linear":
                valid = hmac.compare_digest(headers.get("linear-signature", ""), digest)
                stamp = payload.get("webhookTimestamp")
                valid = (
                    valid
                    and isinstance(stamp, (int, float))
                    and abs(time.time() * 1000 - stamp) < 300000
                )
                delivery = headers.get("linear-delivery", "")
            elif provider == "memory":
                valid = hmac.compare_digest(headers.get("memory-signature", ""), digest)
                delivery = headers.get("memory-delivery", "")
            else:
                timestamp = headers.get("x-slack-request-timestamp", "")
                expected = (
                    "v0="
                    + hmac.new(
                        secret.encode(),
                        b"v0:" + timestamp.encode() + b":" + body,
                        hashlib.sha256,
                    ).hexdigest()
                )
                valid = (
                    timestamp.isdigit()
                    and abs(time.time() - int(timestamp)) < 300
                    and hmac.compare_digest(
                        headers.get("x-slack-signature", ""), expected
                    )
                )
                delivery = payload.get("event_id", "")
            if not secret or not valid:
                raise HTTPException(401, "Invalid provider signature")
            if provider == "slack" and payload.get("type") == "url_verification":
                return {"challenge": payload.get("challenge")}
            if provider == "github":
                if headers.get("x-github-event") == "ping":
                    return {"ok": True}
                if (
                    payload.get("repository", {}).get("full_name")
                    not in self.w.repository_settings.settings()["repositories"]
                ):
                    return {"ignored": True}
            self.enqueue(origin, provider, delivery, body, dict(headers))
            return {"accepted": True}

        return router
