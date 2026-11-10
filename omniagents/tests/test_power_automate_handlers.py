import json

import pytest

from omniagents.integrations.power_automate import (
    PowerAutomateConfig,
    PowerAutomateSource,
)
from omniagents.integrations.power_automate_handlers import JsonHandler
from omniagents.integrations.power_automate_transport import Delivery


def invoice():
    return Delivery(
        "invoice-42",
        "finance",
        {
            "type": "invoice.received",
            "subject": "invoice-42",
            "invoice": {"amount": 12},
        },
    )


def test_nonmessage_payload_preserved_without_conversation_envelope():
    delivery = invoice()
    event = JsonHandler({"event": "invoice"}).prepare(delivery)
    assert event.payload == delivery.payload
    assert event.provider == "power-automate"
    assert event.event == "invoice"
    assert event.thread_key is None
    assert event.filters == {"sources": ["finance"]}
    assert event.delivery_id == JsonHandler({}).prepare(delivery).delivery_id
    assert (
        event.delivery_id
        != JsonHandler({})
        .prepare(Delivery(delivery.id, "other", delivery.payload))
        .delivery_id
    )


def test_missing_required_business_data_rejected():
    with pytest.raises(ValueError):
        JsonHandler({"required_fields": ["missing"]}).prepare(invoice())


def test_arbitrary_nested_data_preserved():
    payload = {
        "anything": [{"root": 42, "replies": list(range(200))}],
        "file": "opaque-data",
    }
    event = JsonHandler({}).prepare(Delivery("any", "finance", payload))
    assert event.payload == payload


@pytest.mark.asyncio
async def test_generic_source_routes_without_any_teams_configuration(tmp_path):
    config = PowerAutomateConfig.parse(
        {
            "sources": {
                "finance": {
                    "handler": "json",
                    "event": "invoice",
                    "required_fields": ["invoice"],
                }
            }
        }
    )
    received = []

    async def accept(event):
        received.append(event)

    source = PowerAutomateSource(config, accept, tmp_path / "replies.db")
    try:
        await source.accept_delivery(invoice())
        assert received[0].payload["invoice"] == {"amount": 12}
        assert not source.spool.pending()
        with pytest.raises(ValueError):
            await source.accept_delivery(Delivery("one", "unconfigured", {}))
    finally:
        await source.stop()
