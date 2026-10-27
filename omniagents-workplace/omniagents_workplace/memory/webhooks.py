"""Signed memory events, filtered by each receiving app's store grants."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx


class MemoryWebhooks:
    def __init__(self, workplace):
        self.workplace = workplace
        self.loop = None
        self.tasks = set()
        workplace.memory.subscribe(self.changed)

    def bind(self, loop):
        self.loop = loop

    def changed(self, kind, data):
        if self.loop is None:
            return
        for app in self.workplace.apps.active():
            if not app.memory_webhook:
                continue
            allowed = {s["id"] for s in self.workplace.memory.stores(app.id)}
            if data["memory"]["store"] not in allowed:
                continue
            payload = {
                "id": uuid.uuid4().hex,
                "event": kind,
                "created_at": time.time(),
                **data,
            }
            self.loop.call_soon_threadsafe(self.start, app.id, payload)

    def start(self, app_id, payload):
        task = asyncio.create_task(self.deliver(app_id, payload))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def deliver(self, app_id, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        for delay in (0, 1, 5):
            if delay:
                await asyncio.sleep(delay)
            app = self.workplace.apps.apps.get(app_id)
            if not app or app.removed or not app.memory_webhook:
                return
            if payload["memory"]["store"] not in {
                s["id"] for s in self.workplace.memory.stores(app_id)
            }:
                return
            signature = hmac.new(
                app.memory_webhook_secret.encode(), body, hashlib.sha256
            ).hexdigest()
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        app.memory_webhook,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "Memory-Signature": signature,
                            "Memory-Delivery": payload["id"],
                        },
                    )
                    response.raise_for_status()
                    return
            except httpx.HTTPError:
                logging.getLogger(__name__).warning(
                    "Memory webhook delivery failed for app %s", app_id
                )

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
