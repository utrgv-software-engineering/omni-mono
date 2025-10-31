import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocket

from omniagents.rpc.transport import TransportClosedError, TransportError
from omniagents.rpc.transport.inmemory import InMemoryTransport
from omniagents.rpc.transport.websocket import (
    ClientWebSocketTransport,
    ServerWebSocketTransport,
)


TIMEOUT = 5.0


@pytest.mark.asyncio
async def test_client_send_failure_marks_disconnected():
    async with asyncio.timeout(TIMEOUT):
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.send.side_effect = RuntimeError("boom")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws

            transport = ClientWebSocketTransport("ws://localhost:8765")
            await transport.connect()

            with pytest.raises(TransportError):
                await transport.send_json({"payload": 1})

            assert not transport.is_connected()


@pytest.mark.asyncio
async def test_client_receive_failure_marks_disconnected():
    async with asyncio.timeout(TIMEOUT):
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.recv.side_effect = RuntimeError("recv error")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws

            transport = ClientWebSocketTransport("ws://localhost:8765")
            await transport.connect()

            with pytest.raises(TransportError):
                await transport.receive_json()

            assert not transport.is_connected()


@pytest.mark.asyncio
async def test_server_send_failure_marks_disconnected():
    async with asyncio.timeout(TIMEOUT):
        mock_ws = AsyncMock(spec=WebSocket)
        mock_ws.send_json.side_effect = RuntimeError("send failure")

        transport = ServerWebSocketTransport(mock_ws)
        await transport.connect()

        with pytest.raises(TransportError):
            await transport.send_json({"message": "ping"})

        assert not transport.is_connected()


@pytest.mark.asyncio
async def test_server_receive_failure_marks_disconnected():
    async with asyncio.timeout(TIMEOUT):
        mock_ws = AsyncMock(spec=WebSocket)
        mock_ws.receive_json.side_effect = RuntimeError("receive failure")

        transport = ServerWebSocketTransport(mock_ws)
        await transport.connect()

        with pytest.raises(TransportError):
            await transport.receive_json()

        assert not transport.is_connected()


@pytest.mark.asyncio
async def test_inmemory_send_after_disconnect_raises():
    async with asyncio.timeout(TIMEOUT):
        client, server = InMemoryTransport.create_pair()

        await client.connect()
        await server.connect()

        await client.disconnect()

        with pytest.raises(TransportClosedError):
            await server.receive_json()

        assert not server.is_connected()

        with pytest.raises(TransportClosedError):
            await server.send_json({"late": True})


@pytest.mark.asyncio
async def test_inmemory_receive_failure_wrapped():
    async with asyncio.timeout(TIMEOUT):
        client, server = InMemoryTransport.create_pair()

        await client.connect()
        await server.connect()

        client._receive_queue.get = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(TransportError):
            await client.receive_json()
