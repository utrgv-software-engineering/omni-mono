"""
Simplified unit tests for JSON-RPC core components.
Focuses on testing without full async context to avoid hanging issues.
"""

import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import pytest
from fastapi import WebSocketDisconnect

from omniagents.rpc import RpcMethodsBase, JsonRpcEndpoint, JsonRpcClient
from omniagents.rpc.server import JsonRpcChannel
from omniagents.rpc.transport import TransportClosedError
from omniagents.rpc.transport.websocket import ServerWebSocketTransport


class TestJsonRpcChannel:
    """Test the JsonRpcChannel notification sender."""

    def test_init(self):
        transport = Mock()
        channel = JsonRpcChannel(transport)
        assert channel.transport is transport

    def test_websocket_backward_compatibility(self):
        """Test backward compatibility property."""
        ws = Mock()
        transport = Mock()
        transport.websocket = ws
        channel = JsonRpcChannel(transport)
        assert channel.websocket is ws

    @pytest.mark.asyncio
    async def test_async_call_sends_notification(self):
        transport = AsyncMock()
        channel = JsonRpcChannel(transport)

        await channel.async_call("test_method", {"param": "value"})

        transport.send_json.assert_called_once()
        call_args = transport.send_json.call_args[0][0]
        assert call_args["jsonrpc"] == "2.0"
        assert call_args["method"] == "test_method"
        assert call_args["params"] == {"param": "value"}
        assert "id" not in call_args

    @pytest.mark.asyncio
    async def test_async_call_propagates_transport_error(self):
        transport = AsyncMock()
        transport.send_json.side_effect = TransportClosedError("closed")
        channel = JsonRpcChannel(transport)

        with pytest.raises(TransportClosedError):
            await channel.async_call("failing_method")

        transport.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_result(self):
        transport = AsyncMock()
        channel = JsonRpcChannel(transport)

        await channel.send_result(123, {"status": "ok"})

        transport.send_json.assert_called_once()
        call_args = transport.send_json.call_args[0][0]
        assert call_args["jsonrpc"] == "2.0"
        assert call_args["id"] == 123
        assert call_args["result"] == {"status": "ok"}


class MockRpcMethods(RpcMethodsBase):
    """Mock RPC methods for testing."""

    async def echo(self, message: str) -> str:
        return f"Echo: {message}"

    async def add(self, a: int, b: int) -> int:
        return a + b

    async def error_method(self) -> None:
        raise ValueError("Test error")


class TestJsonRpcEndpoint:
    """Test the JsonRpcEndpoint server implementation."""

    def test_init(self):
        methods = MockRpcMethods()
        endpoint = JsonRpcEndpoint(methods)
        assert endpoint.methods is methods

    def test_register_route(self):
        methods = MockRpcMethods()
        endpoint = JsonRpcEndpoint(methods)
        app = Mock()

        endpoint.register_route(app, "/ws")

        app.add_api_websocket_route.assert_called_once()
        assert app.add_api_websocket_route.call_args[0][0] == "/ws"
        assert callable(app.add_api_websocket_route.call_args[0][1])


class TestJsonRpcClient:
    """Test JsonRpcClient basic functionality."""

    def test_init(self):
        transport = Mock()
        client = JsonRpcClient(transport, None)
        assert client._transport is transport
        assert client._id == 0
        assert len(client._pending) == 0

    def test_from_url(self):
        """Test backward compatibility factory method."""
        client = JsonRpcClient.from_url("ws://test", None)
        assert hasattr(client._transport, "url")
        assert client._transport.url == "ws://test"

    def test_proxy_attribute(self):
        transport = Mock()
        client = JsonRpcClient(transport, None)
        assert hasattr(client, "other")
        assert hasattr(client.other, "any_method")

    @pytest.mark.asyncio
    async def test_call_creates_request(self):
        """Test that calling a method creates proper JSON-RPC request."""
        transport = AsyncMock()
        transport.is_connected.return_value = True
        client = JsonRpcClient(transport, None)

        # Start the call but don't await it
        call_task = asyncio.create_task(client.call("test_method", param="value"))

        # Give it a moment to send the request
        await asyncio.sleep(0.01)

        # Check that send_json was called
        transport.send_json.assert_called_once()
        sent_data = transport.send_json.call_args[0][0]

        assert sent_data["jsonrpc"] == "2.0"
        assert sent_data["method"] == "test_method"
        assert sent_data["params"] == {"param": "value"}
        assert "id" in sent_data
        assert sent_data["id"] == "1"

        # Cancel the task to clean up
        call_task.cancel()
        try:
            await call_task
        except asyncio.CancelledError:
            pass


# Additional integration tests for the full flow
class TestJsonRpcIntegration:
    """Test integrated behavior of endpoint and methods."""

    @pytest.mark.asyncio
    async def test_endpoint_method_execution(self):
        """Test that endpoint properly executes methods."""
        methods = MockRpcMethods()
        endpoint = JsonRpcEndpoint(methods)

        # Create a mock WebSocket
        ws = AsyncMock()
        received_messages = []

        async def mock_receive_json():
            # First call returns a request
            if len(received_messages) == 0:
                received_messages.append(1)
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "echo",
                    "params": {"message": "hello"},
                }
            # Second call raises disconnect
            else:
                from fastapi import WebSocketDisconnect

                raise WebSocketDisconnect()

        ws.receive_json = mock_receive_json
        ws.send_json = AsyncMock()

        # Register and get the handler
        app = Mock()
        endpoint.register_route(app, "/ws")
        handler = app.add_api_websocket_route.call_args[0][1]

        # Execute the handler
        await handler(ws)

        # Check WebSocket interactions
        ws.accept.assert_called_once()
        ws.send_json.assert_called_once()

        # Check the response
        response = ws.send_json.call_args[0][0]
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert response["result"] == "Echo: hello"
        assert methods.channel is None

    @pytest.mark.asyncio
    async def test_endpoint_returns_error_on_method_failure(self):
        methods = MockRpcMethods()
        endpoint = JsonRpcEndpoint(methods)

        ws = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[
                {"jsonrpc": "2.0", "id": 2, "method": "error_method"},
                WebSocketDisconnect(),
            ]
        )
        ws.send_json = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        app = Mock()
        endpoint.register_route(app, "/ws")
        handler = app.add_api_websocket_route.call_args[0][1]

        await handler(ws)

        ws.accept.assert_awaited()
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        assert payload["error"]["code"] == -32603
        assert payload["error"]["data"]["method"] == "error_method"
        assert methods.channel is None

    @pytest.mark.asyncio
    async def test_endpoint_ignores_non_dict_payloads(self):
        methods = MockRpcMethods()
        endpoint = JsonRpcEndpoint(methods)

        ws = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[
                ["bad", "payload"],
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "echo",
                    "params": {"message": "after"},
                },
                WebSocketDisconnect(),
            ]
        )
        ws.send_json = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        app = Mock()
        endpoint.register_route(app, "/ws")
        handler = app.add_api_websocket_route.call_args[0][1]

        await handler(ws)

        ws.accept.assert_awaited()
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["id"] == 3
        assert payload["result"] == "Echo: after"
        assert methods.channel is None

    @pytest.mark.asyncio
    async def test_endpoint_handles_transport_closed_error(self):
        methods = MockRpcMethods()
        endpoint = JsonRpcEndpoint(methods)

        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        app = Mock()
        endpoint.register_route(app, "/ws")
        handler = app.add_api_websocket_route.call_args[0][1]

        async def fake_receive(self):
            self._connected = False
            raise TransportClosedError("closed")

        with patch.object(ServerWebSocketTransport, "receive_json", new=fake_receive):
            await handler(ws)

        ws.send_json.assert_not_called()
        assert methods.channel is None
        ws.accept.assert_awaited()
