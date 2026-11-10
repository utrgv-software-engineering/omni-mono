from __future__ import annotations

import asyncio
import json
import logging
import ssl

from .power_automate import PowerAutomateSource
from .power_automate_transport import load_delivery

logger = logging.getLogger(__name__)


class BridgeClient:
    def __init__(self, config):
        import httpx

        context = ssl.create_default_context(cafile=config.bridge_ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config.bridge_cert_file, config.bridge_key_file)
        self.client = httpx.AsyncClient(
            base_url=config.bridge_url,
            verify=context,
            timeout=30,
            follow_redirects=False,
        )

    async def call(self, path, value=None):
        response = await self.client.post(path, json=value or {})
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()


class RemotePowerAutomateSource(PowerAutomateSource):
    async def connect(self):
        raise RuntimeError("direct_database_access_disabled")

    async def requeue_remote(self, client):
        for identity, source in self.threads.ready_parked():
            result = await client.call(
                "/v1/requeue", {"id": identity, "source": source}
            )
            if not result["available"]:
                self.threads.forget_parked(identity)

    async def cycle(self, client):
        async with self.delivery_lock:
            await self.requeue_remote(client)
            result = await client.call("/v1/poll")
            for value in result["deliveries"]:
                delivery = load_delivery(
                    value["id"],
                    value["source"],
                    json.dumps(value["payload"], allow_nan=False),
                )
                disposition = "accepted"
                try:
                    await self.accept_delivery(delivery)
                except ValueError:
                    disposition = "rejected"
                await client.call(
                    "/v1/ack",
                    {
                        "id": delivery.id,
                        "source": delivery.source,
                        "disposition": disposition,
                    },
                )
                await self.requeue_remote(client)
            self.recover_replies()
            for identity, source, payload in self.spool.pending():
                await client.call(
                    "/v1/actions",
                    {
                        "id": identity,
                        "source": source,
                        "operation": "teams.reply",
                        "payload": json.loads(payload),
                    },
                )
                self.spool.acknowledge(identity)

    async def run(self):
        while True:
            client = None
            try:
                client = BridgeClient(self.config)
                while True:
                    await self.cycle(client)
                    await asyncio.sleep(self.config.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Private bridge unavailable; retaining unacknowledged events and replies"
                )
            finally:
                if client is not None:
                    await client.close()
            await asyncio.sleep(self.config.poll_seconds)
