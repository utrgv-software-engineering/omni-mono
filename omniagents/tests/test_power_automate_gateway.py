import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from omniagents.integrations.power_automate import PowerAutomateConfig
from omniagents.integrations.power_automate_gateway import BridgeRepository, create_app
from omniagents.integrations.power_automate_remote import RemotePowerAutomateSource


POLICY = {"intake": {"operations": ["sharepoint.get_file"]}}
CUTOFF = "2026-11-08T00:00:00Z"


@pytest.fixture
def repository():
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("OMNI_BRIDGE_TEST_DSN")
    if not dsn:
        pytest.skip("requires disposable PostgreSQL")
    root = Path(__file__).parents[1] / "omniagents/integrations"
    with psycopg.connect(dsn, autocommit=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname='omni_bridge'"
        ).fetchone():
            connection.execute((root / "power_automate.sql").read_text())
        connection.execute(
            "TRUNCATE omni_bridge.inbound_events,omni_bridge.outbound_actions CASCADE"
        )

    async def connect():
        return await psycopg.AsyncConnection.connect(dsn)

    return BridgeRepository(connect, POLICY, CUTOFF)


async def insert(
    repository, identity, source="intake", created="2026-11-09T00:00:00Z", payload=None
):
    async with await repository.connect() as connection:
        await connection.execute(
            "INSERT INTO omni_bridge.inbound_events(id,source,payload,created_at) VALUES(%s,%s,%s,%s)",
            (
                identity,
                source,
                json.dumps(
                    payload
                    if payload is not None
                    else {"nested": {"items": list(range(75))}}
                ),
                created,
            ),
        )


@pytest.mark.asyncio
async def test_poll_native_payload_redelivery_scope_cutoff(repository):
    await insert(repository, "current")
    await insert(repository, "other", source="other")
    await insert(repository, "old", created="2026-11-07T00:00:00Z")
    first = await repository.poll()
    assert [value["id"] for value in first["deliveries"]] == ["current"]
    assert len(first["deliveries"][0]["payload"]["nested"]["items"]) == 75
    assert await repository.poll() == first
    acknowledgement = {"id": "current", "source": "intake", "disposition": "accepted"}
    await repository.acknowledge(acknowledgement)
    await repository.acknowledge(acknowledgement)
    assert await repository.poll() == {"deliveries": []}
    assert await repository.requeue({"id": "current", "source": "intake"}) == {
        "available": True
    }
    assert await repository.poll() == first
    await repository.acknowledge({**acknowledgement, "disposition": "rejected"})
    assert await repository.requeue({"id": "current", "source": "intake"}) == {
        "available": False
    }
    assert await repository.requeue({"id": "old", "source": "intake"}) == {
        "available": False
    }


@pytest.mark.asyncio
async def test_action_idempotency_conflicts_and_source_boundaries(repository):
    action = {
        "id": "reply",
        "source": "intake",
        "operation": "sharepoint.get_file",
        "payload": {"value": [1, 2]},
    }
    assert await repository.submit(action) == {"id": "reply", "state": "pending"}
    assert await repository.submit(action) == {"id": "reply", "state": "pending"}
    with pytest.raises(ValueError, match="idempotency_conflict"):
        await repository.submit({**action, "payload": {"changed": True}})
    with pytest.raises(ValueError, match="source_not_authorized"):
        await repository.submit({**action, "source": "other"})
    with pytest.raises(ValueError, match="operation_not_authorized"):
        await repository.submit({**action, "operation": "sql"})
    with pytest.raises(ValueError, match="source_not_authorized"):
        await repository.acknowledge(
            {"id": "other", "source": "other", "disposition": "accepted"}
        )


@pytest.mark.asyncio
async def test_http_rejects_unknown_fields_invalid_json_and_routes(repository):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(repository)),
        base_url="https://bridge",
    ) as client:
        assert (
            await client.post(
                "/v1/ack",
                json={"id": "row", "source": "other", "disposition": "accepted"},
            )
        ).status_code == 400
        assert (
            await client.post("/v1/ack", content='{"id":"a","id":"b"}')
        ).status_code == 400
        assert (
            await client.post(
                "/v1/ack",
                json={
                    "id": "row",
                    "source": "intake",
                    "disposition": "accepted",
                    "sql": "SELECT 1",
                },
            )
        ).status_code == 400
        assert (await client.post("/sql", json={})).status_code == 404
        assert (await client.get("/health")).json() == {"ok": True}
        assert (await client.post("/v1/poll", json={})).json() == {"deliveries": []}


def test_private_config_requires_tls_and_disallows_database_agent_access():
    entry = {
        "sources": {"intake": {"handler": "json", "event_type": "automation.event"}},
        "bridge_url": "http://bridge",
    }
    with pytest.raises(ValueError):
        PowerAutomateConfig.parse(entry)
    entry.update(
        bridge_url="https://bridge",
        bridge_ca_file="/tls/ca.pem",
        bridge_cert_file="/tls/client.pem",
        bridge_key_file="/tls/key.pem",
    )
    config = PowerAutomateConfig.parse(entry)
    assert config.bridge_url == "https://bridge"
    entry["sources"]["intake"]["agent_access"] = {"generalist": {"operations": []}}
    with pytest.raises(ValueError, match="agent actions"):
        PowerAutomateConfig.parse(entry)


@pytest.mark.asyncio
async def test_remote_does_not_ack_failed_intake_or_lost_outbound(tmp_path):
    accepted = []

    async def accept(event):
        accepted.append(event)
        raise RuntimeError("local disk unavailable")

    config = PowerAutomateConfig.parse(
        {"sources": {"intake": {"handler": "json", "event_type": "automation.event"}}}
    )
    source = RemotePowerAutomateSource(config, accept, tmp_path / "spool.db")
    calls = []

    class Client:
        async def call(self, path, value=None):
            calls.append(path)
            if path == "/v1/poll":
                return {
                    "deliveries": [
                        {
                            "id": "row",
                            "source": "intake",
                            "payload": {"data": {"values": [1, 2]}},
                        }
                    ]
                }
            raise AssertionError("acknowledgement must not happen")

    try:
        with pytest.raises(RuntimeError, match="local disk"):
            await source.cycle(Client())
        assert calls == ["/v1/poll"]
        with pytest.raises(RuntimeError, match="direct_database_access_disabled"):
            await source.connect()
        with source.spool.db:
            source.spool.db.execute(
                "INSERT INTO replies VALUES ('reply','intake','{}')"
            )

        class OutboundClient:
            async def call(self, path, value=None):
                if path == "/v1/poll":
                    return {"deliveries": []}
                raise OSError("response lost")

        with pytest.raises(OSError):
            await source.cycle(OutboundClient())
        assert source.spool.pending() == [("reply", "intake", "{}")]
    finally:
        source.spool.close()


def test_teams_destination_is_required():
    with pytest.raises(ValueError, match="teams_destination_required"):
        BridgeRepository(None, {"intake": {"operations": ["teams.reply"]}}, CUTOFF)
