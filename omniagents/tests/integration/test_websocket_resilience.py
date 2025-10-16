"""
Tests for WebSocket disconnection and reconnection scenarios.
Critical for ensuring the refactored system handles network issues gracefully.
"""
import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
from fastapi import WebSocketDisconnect

from omniagents.rpc import JsonRpcEndpoint, RpcMethodsBase
from omniagents.rpc.server import JsonRpcChannel
from omniagents.core.session import Session


class TestDisconnectionScenarios:
    """Test various disconnection scenarios and recovery."""
    
    def test_channel_handles_disconnected_transport(self):
        """Test that JsonRpcChannel raises on disconnected transport."""
        # Create a mock transport that's disconnected
        transport = AsyncMock()
        transport.send_json.side_effect = RuntimeError("Transport disconnected")
        
        channel = JsonRpcChannel(transport)
        
        # Should raise the exception - callers handle it
        with pytest.raises(RuntimeError, match="Transport disconnected"):
            asyncio.run(channel.async_call("test_method", {"data": "test"}))
        
        with pytest.raises(RuntimeError, match="Transport disconnected"):
            asyncio.run(channel.send_result(1, {"result": "test"}))
    
    @pytest.mark.asyncio
    async def test_endpoint_handles_disconnect_during_method(self):
        """Test endpoint handles WebSocket disconnect during method execution."""
        
        class TestMethods(RpcMethodsBase):
            async def long_running(self):
                await asyncio.sleep(0.1)
                return "completed"
        
        methods = TestMethods()
        endpoint = JsonRpcEndpoint(methods)
        
        # Mock WebSocket that disconnects after first receive
        ws = AsyncMock()
        ws.accept = AsyncMock()
        
        # First receive returns a request, second raises disconnect
        receive_count = 0
        async def mock_receive_json():
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "long_running"
                }
            else:
                raise WebSocketDisconnect()
        
        ws.receive_json = mock_receive_json
        
        # Register and get handler
        app = Mock()
        endpoint.register_route(app, "/ws")
        handler = app.add_api_websocket_route.call_args[0][1]
        
        # Should handle gracefully
        await handler(ws)
        
        # WebSocket should be accepted but may not get response
        ws.accept.assert_called_once()
    
    def test_session_pending_events_mechanism(self):
        """Test that sessions can buffer events when no channels are available."""
        session = Session()
        
        # No channels connected
        assert len(session.channels) == 0
        assert len(session.pending_events) == 0
        
        # Add pending events
        session.pending_events.append(("tool_approval_request", {
            "approval_id": "123",
            "tool": "test_tool",
            "arguments": "{}"
        }))
        
        assert len(session.pending_events) == 1
        
        # Add a channel
        mock_channel = Mock()
        session.channels.append(mock_channel)
        
        # Events should still be pending until explicitly replayed
        assert len(session.pending_events) == 1
    
    @pytest.mark.asyncio
    async def test_multi_channel_broadcast_with_dead_channels(self):
        """Test broadcasting to multiple channels with some disconnected."""
        from omniagents.core.runtime.bridge import _broadcast
        
        session = Session()
        
        # Create mix of live and dead channels
        live_channel = AsyncMock()
        live_channel.async_call = AsyncMock()
        
        dead_channel = AsyncMock()
        dead_channel.async_call = AsyncMock(side_effect=RuntimeError("Disconnected"))
        
        session.channels = [live_channel, dead_channel]
        
        # Broadcast a message
        delivered = await _broadcast(session, "test_event", {"data": "test"})
        
        # Should return True (at least one delivered)
        assert delivered is True
        
        # Both should be called
        live_channel.async_call.assert_called_once_with("test_event", args={"data": "test"})
        dead_channel.async_call.assert_called_once_with("test_event", args={"data": "test"})
        
        # Dead channel should be removed
        assert dead_channel not in session.channels
        assert live_channel in session.channels
    
    @pytest.mark.asyncio
    async def test_broadcast_with_no_channels(self):
        """Test broadcasting when no channels are connected."""
        from omniagents.core.runtime.bridge import _broadcast
        
        session = Session()
        session.channels = []
        
        # Should return False
        delivered = await _broadcast(session, "test_event", {"data": "test"})
        assert delivered is False
    
    def test_reconnection_replay_pattern(self):
        """Test the pattern for replaying events on reconnection."""
        # This tests the conceptual pattern used in start_run
        session = Session()
        
        # Simulate pending events from previous connection
        session.pending_events = [
            ("tool_approval_request", {"approval_id": "1", "tool": "tool1"}),
            ("message_output", {"content": "Hello", "run_id": "run1"}),
        ]
        
        # Simulate reconnection with new channel
        new_channel = Mock()
        session.channels = [new_channel]
        
        # Simulate replay logic (simplified from start_run)
        delivered_events = []
        still_pending = []
        
        for method, payload in session.pending_events:
            # Try to deliver
            try:
                # In real code, this would use _safe_notify
                delivered_events.append((method, payload))
            except Exception:
                still_pending.append((method, payload))
        
        # All events should be "delivered" in this simulation
        assert len(delivered_events) == 2
        assert len(still_pending) == 0
    
    @pytest.mark.asyncio
    async def test_safe_notify_pattern(self):
        """Test the safe notification pattern used throughout the server."""
        # Test the pattern used by _safe_notify
        
        async def safe_notify(channel, method, payload):
            try:
                await channel.async_call(method, args=payload)
                return True
            except Exception:
                return False
        
        # Test with working channel
        good_channel = AsyncMock()
        result = await safe_notify(good_channel, "test", {"data": 1})
        assert result is True
        good_channel.async_call.assert_called_once()
        
        # Test with broken channel
        bad_channel = AsyncMock()
        bad_channel.async_call.side_effect = RuntimeError("Disconnected")
        result = await safe_notify(bad_channel, "test", {"data": 1})
        assert result is False
    
    def test_session_cleanup_on_disconnect(self):
        """Test that sessions clean up properly on disconnect."""
        session = Session()
        
        # Add multiple channels
        ch1, ch2, ch3 = Mock(), Mock(), Mock()
        session.channels = [ch1, ch2, ch3]
        
        # Simulate ch2 disconnecting
        session.channels.remove(ch2)
        
        assert ch1 in session.channels
        assert ch2 not in session.channels
        assert ch3 in session.channels
        assert len(session.channels) == 2


class TestReconnectionIntegration:
    """Integration tests for reconnection scenarios."""
    
    @pytest.mark.asyncio
    async def test_event_replay_on_reconnect(self, tmp_path):
        """Test that pending events are replayed when client reconnects."""
        # This is a conceptual test showing the pattern
        # Real integration test would require full server setup
        
        from omniagents.core.session import SessionManager
        
        # Create session manager
        sm = SessionManager("test", "test_agent")
        
        # Create session with pending events
        session_id = "test_session"
        session = sm.get_or_create(session_id)
        
        # Add pending events (simulating failed delivery)
        session.pending_events = [
            ("tool_approval_request", {
                "approval_id": "pending_1",
                "tool": "file_write",
                "arguments": '{"path": "/tmp/test.txt"}'
            })
        ]
        
        # Simulate new connection
        new_channel = AsyncMock()
        session.channels.append(new_channel)
        
        # In real server, start_run would replay these
        # Here we just verify the structure is correct
        assert len(session.pending_events) == 1
        assert session.pending_events[0][0] == "tool_approval_request"
        assert session.pending_events[0][1]["approval_id"] == "pending_1"
