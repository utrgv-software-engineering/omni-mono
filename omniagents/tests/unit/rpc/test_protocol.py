"""
Simplified unit tests for JSON-RPC core components.
Focuses on testing without full async context to avoid hanging issues.
"""

import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch
import pytest
from fastapi import WebSocketDisconnect

from omniagents.rpc import RpcMethodsBase, JsonRpcEndpoint, JsonRpcClient
from omniagents.rpc.client import RpcError
from omniagents.rpc.lifecycle import RpcConnectionClosedError
from omniagents.rpc.server import JsonRpcChannel
from omniagents.rpc.transport import TransportClosedError
from omniagents.rpc.transport.inmemory import InMemoryTransport
from omniagents.rpc.transport.websocket import ServerWebSocketTransport

TIMEOUT = 5.0


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


class RecorderMethods(RpcMethodsBase):
    def __init__(self):
        super().__init__()
        self.sync_calls = []
        self.async_calls = []

    def notify_sync(self, **params):
        self.sync_calls.append(params)

    async def notify_async(self, **params):
        self.async_calls.append(params)


class ConcurrentRpcMethods(RpcMethodsBase):
    def __init__(self):
        super().__init__()
        self.release_slow = asyncio.Event()

    async def slow(self):
        await self.release_slow.wait()
        return "slow"

    async def fast(self):
        self.release_slow.set()
        return "fast"


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
        transport.is_connected = Mock(return_value=True)
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

    @pytest.mark.asyncio
    async def test_call_requires_connected_transport(self):
        transport = AsyncMock()
        transport.is_connected = Mock(return_value=False)
        client = JsonRpcClient(transport, None)

        with pytest.raises(RpcConnectionClosedError):
            await asyncio.wait_for(client.call("missing"), timeout=TIMEOUT)

        transport.send_json.assert_not_called()


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
        responses = [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "echo",
                    "params": {"message": "hello"},
                }
            ),
            WebSocketDisconnect(),
        ]

        async def mock_receive_text():
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        ws.receive_text = AsyncMock(side_effect=mock_receive_text)
        ws.send_json = AsyncMock()
        ws.accept = AsyncMock()

        # Register and get the handler
        app = Mock()
        endpoint.register_route(app, "/ws")
        handler = app.add_api_websocket_route.call_args[0][1]

        # Execute the handler
        await handler(ws)

        # Check WebSocket interactions
        ws.accept.assert_awaited_once()
        assert len(ws.send_json.await_args_list) == 1

        # Check the response
        response = ws.send_json.call_args[0][0]
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert response["result"] == "Echo: hello"
        assert methods.channel is None

    @pytest.mark.asyncio
    async def test_endpoint_multiplexes_requests_on_one_connection(self):
        methods = ConcurrentRpcMethods()
        endpoint = JsonRpcEndpoint(methods)
        responses_complete = asyncio.Event()
        sent = []

        ws = AsyncMock()
        requests = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slow"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "fast"}),
        ]

        async def receive_text():
            if requests:
                return requests.pop(0)
            await responses_complete.wait()
            raise WebSocketDisconnect()

        async def send_json(payload):
            sent.append(payload)
            if len(sent) == 2:
                responses_complete.set()

        ws.receive_text = AsyncMock(side_effect=receive_text)
        ws.send_json = AsyncMock(side_effect=send_json)
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        app = Mock()
        endpoint.register_route(app, "/ws")
        handler = app.add_api_websocket_route.call_args[0][1]

        await asyncio.wait_for(handler(ws), timeout=TIMEOUT)

        assert {payload["id"]: payload["result"] for payload in sent} == {
            1: "slow",
            2: "fast",
        }
        assert methods.channel is None

    @pytest.mark.asyncio
    async def test_endpoint_returns_error_on_method_failure(self):
        methods = MockRpcMethods()
        endpoint = JsonRpcEndpoint(methods)

        ws = AsyncMock()
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "error_method"}),
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

        ws.accept.assert_awaited_once()
        assert len(ws.send_json.await_args_list) == 1
        payload = ws.send_json.await_args_list[0][0][0]
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
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps(["bad", "payload"]),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "echo",
                        "params": {"message": "after"},
                    }
                ),
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

        ws.accept.assert_awaited_once()
        assert len(ws.send_json.await_args_list) == 2

        error_payload = ws.send_json.await_args_list[0][0][0]
        assert error_payload["error"]["code"] == -32600
        assert error_payload["id"] is None

        result_payload = ws.send_json.await_args_list[1][0][0]
        assert result_payload["id"] == 3
        assert result_payload["result"] == "Echo: after"
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


class TestJsonRpcClientInMemory:
    @pytest.mark.asyncio
    async def test_notifications_dispatch(self):
        methods = RecorderMethods()
        client_transport, server_transport = InMemoryTransport.create_pair()
        client = JsonRpcClient(client_transport, methods, gui_handshake=False)
        await asyncio.wait_for(server_transport.connect(), timeout=TIMEOUT)

        async with asyncio.timeout(TIMEOUT):
            async with client:
                await asyncio.wait_for(
                    server_transport.send_json(
                        {
                            "jsonrpc": "2.0",
                            "method": "notify_sync",
                            "params": {"value": 1},
                        }
                    ),
                    timeout=TIMEOUT,
                )
                await asyncio.wait_for(
                    server_transport.send_json(
                        {
                            "jsonrpc": "2.0",
                            "method": "notify_async",
                            "params": {"value": 2},
                        }
                    ),
                    timeout=TIMEOUT,
                )
                await asyncio.sleep(0.01)
                assert methods.sync_calls == [{"value": 1}]
                assert methods.async_calls == [{"value": 2}]

        await asyncio.wait_for(server_transport.disconnect(), timeout=TIMEOUT)

    @pytest.mark.asyncio
    async def test_success_and_error_responses(self):
        methods = RecorderMethods()
        client_transport, server_transport = InMemoryTransport.create_pair()
        client = JsonRpcClient(client_transport, methods, gui_handshake=False)
        await asyncio.wait_for(server_transport.connect(), timeout=TIMEOUT)

        async with asyncio.timeout(TIMEOUT):
            async with client:
                success_task = asyncio.create_task(client.call("ping", value=1))
                request = await asyncio.wait_for(
                    server_transport.receive_json(), timeout=TIMEOUT
                )
                assert request["method"] == "ping"
                await asyncio.wait_for(
                    server_transport.send_json(
                        {"jsonrpc": "2.0", "id": request["id"], "result": {"value": 1}}
                    ),
                    timeout=TIMEOUT,
                )
                assert await asyncio.wait_for(success_task, timeout=TIMEOUT) == {
                    "value": 1
                }

                error_task = asyncio.create_task(client.call("fail"))
                error_request = await asyncio.wait_for(
                    server_transport.receive_json(), timeout=TIMEOUT
                )
                await asyncio.wait_for(
                    server_transport.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": error_request["id"],
                            "error": {
                                "code": -32001,
                                "message": "failed",
                                "data": {"detail": True},
                            },
                        }
                    ),
                    timeout=TIMEOUT,
                )
                with pytest.raises(RpcError) as exc:
                    await asyncio.wait_for(error_task, timeout=TIMEOUT)
                assert exc.value.code == -32001
                assert exc.value.message == "failed"
                assert exc.value.data == {"detail": True}

        await asyncio.wait_for(server_transport.disconnect(), timeout=TIMEOUT)

    @pytest.mark.asyncio
    async def test_ignores_mismatched_responses(self):
        methods = RecorderMethods()
        client_transport, server_transport = InMemoryTransport.create_pair()
        client = JsonRpcClient(client_transport, methods, gui_handshake=False)
        await asyncio.wait_for(server_transport.connect(), timeout=TIMEOUT)

        async with asyncio.timeout(TIMEOUT):
            async with client:
                task = asyncio.create_task(client.call("compute"))
                request = await asyncio.wait_for(
                    server_transport.receive_json(), timeout=TIMEOUT
                )
                await asyncio.wait_for(
                    server_transport.send_json(
                        {"jsonrpc": "2.0", "id": "unmatched", "result": "ignored"}
                    ),
                    timeout=TIMEOUT,
                )
                await asyncio.sleep(0.01)
                assert not task.done()
                await asyncio.wait_for(
                    server_transport.send_json(
                        {"jsonrpc": "2.0", "id": request["id"], "result": "ok"}
                    ),
                    timeout=TIMEOUT,
                )
                assert await asyncio.wait_for(task, timeout=TIMEOUT) == "ok"

        await asyncio.wait_for(server_transport.disconnect(), timeout=TIMEOUT)

    @pytest.mark.asyncio
    async def test_handles_non_dict_payload(self):
        methods = RecorderMethods()
        client_transport, server_transport = InMemoryTransport.create_pair()
        client = JsonRpcClient(client_transport, methods, gui_handshake=False)
        await asyncio.wait_for(server_transport.connect(), timeout=TIMEOUT)

        async with asyncio.timeout(TIMEOUT):
            async with client:
                task = asyncio.create_task(client.call("noop"))
                request = await asyncio.wait_for(
                    server_transport.receive_json(), timeout=TIMEOUT
                )
                await asyncio.wait_for(
                    server_transport.send_json(["unexpected", "payload"]),
                    timeout=TIMEOUT,
                )
                await asyncio.sleep(0.01)
                await asyncio.wait_for(
                    server_transport.send_json(
                        {"jsonrpc": "2.0", "id": request["id"], "result": "done"}
                    ),
                    timeout=TIMEOUT,
                )
                assert await asyncio.wait_for(task, timeout=TIMEOUT) == "done"

        await asyncio.wait_for(server_transport.disconnect(), timeout=TIMEOUT)

    @pytest.mark.asyncio
    async def test_recv_loop_stops_on_transport_closed(self):
        methods = RecorderMethods()
        client_transport, server_transport = InMemoryTransport.create_pair()
        client = JsonRpcClient(client_transport, methods, gui_handshake=False)
        await asyncio.wait_for(server_transport.connect(), timeout=TIMEOUT)

        async with asyncio.timeout(TIMEOUT):
            async with client:
                await asyncio.wait_for(server_transport.disconnect(), timeout=TIMEOUT)
                await asyncio.sleep(0.01)
                assert client._recv_task is not None
                assert client._recv_task.done()
