from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import ssl
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace

from .power_automate_policy import parse_timestamp
from .power_automate_transport import InvalidDelivery, load_delivery


class BridgeRepository:
    def __init__(self, connect, sources, not_before):
        if not isinstance(sources, dict) or not sources:
            raise ValueError("explicit_sources_required")
        for source, policy in sources.items():
            if (
                not isinstance(source, str)
                or not source
                or not isinstance(policy, dict)
            ):
                raise ValueError("invalid_source_policy")
            operations = policy.get("operations", [])
            if not isinstance(operations, list) or not all(
                isinstance(operation, str) and operation for operation in operations
            ):
                raise ValueError("invalid_operations")
            if "teams.reply" in operations and not all(
                isinstance(policy.get(key), str) and policy[key]
                for key in ("tenant_id", "team_id", "channel_id")
            ):
                raise ValueError("teams_destination_required")
        self.connect = connect
        self.sources = sources
        self.not_before = parse_timestamp(not_before)

    def identity(self, value):
        if not isinstance(value, dict):
            raise ValueError("invalid_request")
        source = value.get("source")
        identity = value.get("id")
        if not isinstance(source, str) or source not in self.sources:
            raise ValueError("source_not_authorized")
        if not isinstance(identity, str) or not identity or "\x00" in identity:
            raise ValueError("invalid_identity")
        return identity, source

    async def poll(self):
        deliveries = []
        async with await self.connect() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT id,source,payload FROM omni_bridge.inbound_events "
                    "WHERE accepted_at IS NULL AND rejected_at IS NULL "
                    "AND available_at <= now() AND created_at >= %s "
                    "AND source=ANY(%s) ORDER BY available_at,created_at,id "
                    "LIMIT 100 FOR UPDATE SKIP LOCKED",
                    (self.not_before, sorted(self.sources)),
                )
                for identity, source, payload in await cursor.fetchall():
                    try:
                        delivery = load_delivery(identity, source, payload)
                    except InvalidDelivery:
                        await connection.execute(
                            "UPDATE omni_bridge.inbound_events SET rejected_at=now(), "
                            "error_code='invalid_delivery' WHERE id=%s AND source=%s",
                            (identity, source),
                        )
                    else:
                        deliveries.append(
                            {
                                "id": delivery.id,
                                "source": delivery.source,
                                "payload": delivery.payload,
                            }
                        )
        return {"deliveries": deliveries}

    async def acknowledge(self, value):
        identity, source = self.identity(value)
        disposition = value.get("disposition")
        if set(value) != {"id", "source", "disposition"} or disposition not in {
            "accepted",
            "rejected",
        }:
            raise ValueError("invalid_acknowledgement")
        statement = (
            "SET accepted_at=now(),error_code=NULL "
            if disposition == "accepted"
            else "SET rejected_at=now(),error_code='accept_rejected' "
        )
        async with await self.connect() as connection:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE omni_bridge.inbound_events "
                    + statement
                    + "WHERE id=%s AND source=%s AND created_at >= %s "
                    "AND available_at <= now() AND accepted_at IS NULL AND rejected_at IS NULL",
                    (identity, source, self.not_before),
                )
        return {"ok": True}

    async def requeue(self, value):
        identity, source = self.identity(value)
        if set(value) != {"id", "source"}:
            raise ValueError("invalid_requeue")
        async with await self.connect() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "UPDATE omni_bridge.inbound_events SET accepted_at=NULL,available_at=now() "
                    "WHERE id=%s AND source=%s AND created_at >= %s "
                    "AND accepted_at IS NOT NULL AND rejected_at IS NULL RETURNING id",
                    (identity, source, self.not_before),
                )
                if await cursor.fetchone() is not None:
                    return {"available": True}
                cursor = await connection.execute(
                    "SELECT 1 FROM omni_bridge.inbound_events WHERE id=%s AND source=%s "
                    "AND created_at >= %s AND rejected_at IS NULL",
                    (identity, source, self.not_before),
                )
                return {"available": await cursor.fetchone() is not None}

    async def submit(self, value):
        identity, source = self.identity(value)
        if set(value) != {"id", "source", "operation", "payload"}:
            raise ValueError("invalid_action")
        operation = value["operation"]
        payload = value["payload"]
        policy = self.sources[source]
        if not isinstance(operation, str) or operation not in policy.get(
            "operations", []
        ):
            raise ValueError("operation_not_authorized")
        if not isinstance(payload, dict):
            raise ValueError("invalid_payload")
        if operation == "teams.reply":
            if any(
                payload.get(key) != policy[key]
                for key in ("tenant_id", "team_id", "channel_id")
            ):
                raise ValueError("destination_not_authorized")
            if not all(
                isinstance(payload.get(key), str) and payload[key]
                for key in ("root_message_id", "text")
            ):
                raise ValueError("invalid_reply")
            if payload.get("bridge_generated") is not True:
                raise ValueError("invalid_reply_origin")
        body = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        async with await self.connect() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO omni_bridge.outbound_actions (id,source,operation,payload) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (identity, source, operation, body),
                )
                cursor = await connection.execute(
                    "SELECT operation,payload,state,agent,session_id FROM omni_bridge.outbound_actions "
                    "WHERE id=%s AND source=%s",
                    (identity, source),
                )
                row = await cursor.fetchone()
                if (
                    row is None
                    or row[0] != operation
                    or json.loads(row[1]) != payload
                    or row[3] is not None
                    or row[4] is not None
                ):
                    raise ValueError("idempotency_conflict")
                return {"id": identity, "state": row[2]}

    async def health(self):
        async with await self.connect() as connection:
            await connection.execute("SELECT 1")
        return {"ok": True}


def create_app(repository, maintenance=None):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(maintenance()) if maintenance is not None else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(ValueError)
    async def invalid_request(request, error):
        return JSONResponse({"error": "request_rejected"}, status_code=400)

    @app.exception_handler(Exception)
    async def unavailable(request, error):
        return JSONResponse({"error": "bridge_unavailable"}, status_code=503)

    async def dispatch(request: Request):
        value = await request.body()
        from .power_automate_transport import _json

        value = _json(value.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid_request")
        method = {
            "/v1/ack": repository.acknowledge,
            "/v1/requeue": repository.requeue,
            "/v1/actions": repository.submit,
        }[request.url.path]
        return await method(value)

    dispatch.__annotations__["request"] = Request
    for path in ("/v1/ack", "/v1/requeue", "/v1/actions"):
        app.add_api_route(path, dispatch, methods=["POST"])
    app.add_api_route("/v1/poll", repository.poll, methods=["POST"])
    app.add_api_route("/health", repository.health, methods=["GET"])
    return app


def main():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = json.loads(Path(args.config).read_text())
    connection_string = os.environ[
        settings.get("connection_env", "OMNI_POWER_AUTOMATE_DSN")
    ]
    if conninfo_to_dict(connection_string).get("sslmode") != "verify-full":
        raise ValueError("bridge_requires_verified_database_tls")

    async def connect():
        return await psycopg.AsyncConnection.connect(
            connection_string,
            connect_timeout=10,
            options="-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000",
        )

    repository = BridgeRepository(connect, settings["sources"], settings["not_before"])

    async def maintenance():
        from .power_automate_maintenance import maintain

        config = SimpleNamespace(
            sources=repository.sources,
            action_timeout_seconds=3600,
            transfer_expiry_seconds=604800,
        )
        while True:
            try:
                async with await connect() as connection:
                    await maintain(connection, config)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Private bridge maintenance failed; retrying"
                )
            await asyncio.sleep(60)

    uvicorn.run(
        create_app(repository, maintenance),
        host=settings["listen_host"],
        port=443,
        ssl_certfile=settings["certificate"],
        ssl_keyfile=settings["key"],
        ssl_ca_certs=settings["client_ca"],
        ssl_cert_reqs=ssl.CERT_REQUIRED,
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        limit_concurrency=32,
        timeout_keep_alive=5,
        log_level="critical",
    )


if __name__ == "__main__":
    main()
