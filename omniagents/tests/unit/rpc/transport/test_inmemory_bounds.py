import asyncio

import pytest

from omniagents.rpc.transport import TransportClosedError, TransportOverloadedError
from omniagents.rpc.transport.inmemory import InMemoryTransport


@pytest.mark.asyncio
async def test_inmemory_transport_rejects_queue_overload():
    client, server = InMemoryTransport.create_pair(max_messages=1, max_bytes=100)
    await client.connect()
    await server.connect()

    await client.send_json({"value": 1})

    with pytest.raises(TransportOverloadedError):
        await client.send_json({"value": 2})

    assert await server.receive_json() == {"value": 1}


@pytest.mark.asyncio
async def test_inmemory_disconnect_wakes_receiver_when_queue_is_full():
    client, server = InMemoryTransport.create_pair(max_messages=1, max_bytes=100)
    await client.connect()
    await server.connect()
    await client.send_json({"value": 1})

    await client.disconnect()

    assert await server.receive_json() == {"value": 1}
    with pytest.raises(TransportClosedError):
        await asyncio.wait_for(server.receive_json(), timeout=0.1)


@pytest.mark.asyncio
async def test_inmemory_transport_rejects_oversize_payload():
    client, server = InMemoryTransport.create_pair(max_messages=2, max_bytes=8)
    await client.connect()
    await server.connect()

    with pytest.raises(TransportOverloadedError) as exc_info:
        await client.send_json({"value": "too large"})

    assert not exc_info.value.retryable
    assert exc_info.value.unit == "bytes"
