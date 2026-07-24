"""
Unit tests for transport implementations.
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, AsyncMock, patch
from fastapi import WebSocket

from omniagents.rpc.transport import (
    Transport,
    BaseTransport,
    TransportError,
    TransportClosedError,
)
from omniagents.rpc.transport.websocket import (
    ClientWebSocketTransport,
    ServerWebSocketTransport,
)
from omniagents.rpc.transport.inmemory import InMemoryTransport

TIMEOUT = 5.0


class TestBaseTransport:
    """Test base transport functionality."""

    def test_initial_state(self):
        """Test initial connection state."""

        # Create a concrete implementation for testing
        class TestTransport(BaseTransport):
            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def send_json(self, data):
                pass

            async def receive_json(self):
                pass

        transport = TestTransport()
        assert not transport.is_connected()

    def test_ensure_connected_raises_when_not_connected(self):
        """Test that _ensure_connected raises when not connected."""

        class TestTransport(BaseTransport):
            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def send_json(self, data):
                pass

            async def receive_json(self):
                pass

        transport = TestTransport()
        with pytest.raises(TransportClosedError, match="Transport is not connected"):
            transport._ensure_connected()


class TestClientWebSocketTransport:
    """Test client WebSocket transport."""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful connection."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock()
            mock_ws.closed = False

            with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
                mock_connect.return_value = mock_ws

                transport = ClientWebSocketTransport("ws://localhost:8000")
                await transport.connect()

                assert transport.is_connected()
                mock_connect.assert_called_once_with("ws://localhost:8000")

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        """Test connection failure."""
        async with asyncio.timeout(TIMEOUT):
            with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
                mock_connect.side_effect = Exception("Connection failed")

                transport = ClientWebSocketTransport("ws://localhost:8000")
                with pytest.raises(TransportError, match="Failed to connect"):
                    await transport.connect()

                assert not transport.is_connected()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test disconnection."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock()
            mock_ws.open = True

            with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
                mock_connect.return_value = mock_ws

                transport = ClientWebSocketTransport("ws://localhost:8000")
                await transport.connect()
                await transport.disconnect()

                assert not transport.is_connected()
                mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_json(self):
        """Test sending JSON data."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock()
            mock_ws.open = True

            with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
                mock_connect.return_value = mock_ws

                transport = ClientWebSocketTransport("ws://localhost:8000")
                await transport.connect()

                data = {"method": "test", "params": {"value": 123}}
                await transport.send_json(data)

                mock_ws.send.assert_called_once_with(json.dumps(data))

    @pytest.mark.asyncio
    async def test_send_json_when_not_connected(self):
        """Test sending when not connected."""
        async with asyncio.timeout(TIMEOUT):
            transport = ClientWebSocketTransport("ws://localhost:8000")

            with pytest.raises(TransportClosedError):
                await transport.send_json({"test": "data"})

    @pytest.mark.asyncio
    async def test_receive_json(self):
        """Test receiving JSON data."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock()
            mock_ws.open = True
            mock_ws.recv.return_value = '{"result": "success"}'

            with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
                mock_connect.return_value = mock_ws

                transport = ClientWebSocketTransport("ws://localhost:8000")
                await transport.connect()

                result = await transport.receive_json()
                assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_receive_invalid_json(self):
        """Test receiving invalid JSON."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock()
            mock_ws.open = True
            mock_ws.recv.return_value = "invalid json"

            with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
                mock_connect.return_value = mock_ws

                transport = ClientWebSocketTransport("ws://localhost:8000")
                await transport.connect()

                with pytest.raises(TransportError, match="Invalid JSON received"):
                    await transport.receive_json()


class TestServerWebSocketTransport:
    """Test server WebSocket transport."""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful connection acceptance."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock(spec=WebSocket)

            transport = ServerWebSocketTransport(mock_ws)
            await transport.connect()

            assert transport.is_connected()
            mock_ws.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test disconnection."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock(spec=WebSocket)

            transport = ServerWebSocketTransport(mock_ws)
            await transport.connect()
            await transport.disconnect()

            assert not transport.is_connected()
            mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_json(self):
        """Test sending JSON data."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock(spec=WebSocket)

            transport = ServerWebSocketTransport(mock_ws)
            await transport.connect()

            data = {"method": "notification", "params": {"msg": "hello"}}
            await transport.send_json(data)

            mock_ws.send_json.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_receive_json(self):
        """Test receiving JSON data."""
        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock(spec=WebSocket)
            mock_ws.receive_text = AsyncMock(
                return_value=json.dumps({"id": 1, "method": "test"})
            )

            transport = ServerWebSocketTransport(mock_ws)
            await transport.connect()

            result = await transport.receive_json()
            assert result == {"id": 1, "method": "test"}

            mock_ws.receive_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_receive_websocket_disconnect(self):
        """Test handling WebSocket disconnect during receive."""
        from fastapi import WebSocketDisconnect

        async with asyncio.timeout(TIMEOUT):
            mock_ws = AsyncMock(spec=WebSocket)
            mock_ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())

            transport = ServerWebSocketTransport(mock_ws)
            await transport.connect()

            with pytest.raises(TransportClosedError, match="WebSocket disconnected"):
                await transport.receive_json()

            assert not transport.is_connected()


class TestInMemoryTransport:
    """Test in-memory transport."""

    def test_create_pair(self):
        """Test creating a connected pair."""
        client, server = InMemoryTransport.create_pair()

        assert client._pair is server
        assert server._pair is client
        assert client._send_queue is server._receive_queue
        assert server._send_queue is client._receive_queue

    @pytest.mark.asyncio
    async def test_connect_without_pair(self):
        """Test connecting without using create_pair."""
        async with asyncio.timeout(TIMEOUT):
            transport = InMemoryTransport()

            with pytest.raises(
                TransportError, match="must be created with create_pair"
            ):
                await transport.connect()

    @pytest.mark.asyncio
    async def test_bidirectional_communication(self):
        """Test bidirectional communication between paired transports."""
        async with asyncio.timeout(TIMEOUT):
            client, server = InMemoryTransport.create_pair()

            await client.connect()
            await server.connect()

            assert client.is_connected()
            assert server.is_connected()

            await client.send_json({"from": "client", "value": 1})
            result = await server.receive_json()
            assert result == {"from": "client", "value": 1}

            await server.send_json({"from": "server", "value": 2})
            result = await client.receive_json()
            assert result == {"from": "server", "value": 2}

    @pytest.mark.asyncio
    async def test_disconnect_notification(self):
        """Test that disconnect notifies the paired transport."""
        async with asyncio.timeout(TIMEOUT):
            client, server = InMemoryTransport.create_pair()

            await client.connect()
            await server.connect()

            receive_task = asyncio.create_task(server.receive_json())

            await client.disconnect()

            with pytest.raises(
                TransportClosedError, match="Paired transport disconnected"
            ):
                await receive_task

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        """Test sending multiple messages."""
        async with asyncio.timeout(TIMEOUT):
            client, server = InMemoryTransport.create_pair()

            await client.connect()
            await server.connect()

            messages = [
                {"id": 1, "method": "test1"},
                {"id": 2, "method": "test2"},
                {"id": 3, "method": "test3"},
            ]

            for msg in messages:
                await client.send_json(msg)

            for expected in messages:
                result = await server.receive_json()
                assert result == expected

    @pytest.mark.asyncio
    async def test_concurrent_bidirectional(self):
        """Test concurrent bidirectional communication."""
        async with asyncio.timeout(TIMEOUT):
            client, server = InMemoryTransport.create_pair()

            await client.connect()
            await server.connect()

            async def client_sender():
                for i in range(5):
                    await client.send_json({"client": i})
                    await asyncio.sleep(0.01)

            async def server_sender():
                for i in range(5):
                    await server.send_json({"server": i})
                    await asyncio.sleep(0.01)

            async def client_receiver():
                messages = []
                for _ in range(5):
                    msg = await client.receive_json()
                    messages.append(msg)
                return messages

            async def server_receiver():
                messages = []
                for _ in range(5):
                    msg = await server.receive_json()
                    messages.append(msg)
                return messages

            results = await asyncio.gather(
                client_sender(),
                server_sender(),
                client_receiver(),
                server_receiver(),
            )

            client_received = results[2]
            server_received = results[3]

            assert len(client_received) == 5
            assert len(server_received) == 5
            assert all("server" in msg for msg in client_received)
            assert all("client" in msg for msg in server_received)


class TestTransportCreation:
    """Test transport creation without factory."""

    def test_create_client_websocket(self):
        """Test creating client WebSocket transport."""
        transport = ClientWebSocketTransport("ws://localhost:8000")
        assert isinstance(transport, ClientWebSocketTransport)
        assert transport.url == "ws://localhost:8000"

    def test_create_server_websocket(self):
        """Test creating server WebSocket transport."""
        mock_ws = Mock(spec=WebSocket)
        transport = ServerWebSocketTransport(mock_ws)
        assert isinstance(transport, ServerWebSocketTransport)
        assert transport.websocket is mock_ws

    def test_create_memory_pair(self):
        """Test creating memory transport pair."""
        client, server = InMemoryTransport.create_pair()
        assert isinstance(client, InMemoryTransport)
        assert isinstance(server, InMemoryTransport)
        assert client._pair is server
        assert server._pair is client


class TestTransportProtocol:
    """Test that implementations satisfy the Transport protocol."""

    def test_all_transports_implement_protocol(self):
        """Test that all transport classes implement the Transport protocol."""
        # These should all be recognized as Transport implementations
        assert isinstance(ClientWebSocketTransport("ws://test"), Transport)
        assert isinstance(ServerWebSocketTransport(Mock()), Transport)
        assert isinstance(InMemoryTransport(), Transport)
