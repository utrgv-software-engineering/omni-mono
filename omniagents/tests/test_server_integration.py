"""
Integration tests for server RPC methods that match actual implementation.
"""
import asyncio
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from omniagents.server import app as server_app
from omniagents.core.specs import AgentSpec
from agents import Agent


# Mock agent for testing
async def mock_build_agent(settings, mcp, app):
    return Agent(name='test_agent', tools=[], instructions='Test instructions', model='gpt-3.5-turbo')


# Minimal mock stream_agent
async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20):
    """Mock agent streaming - minimal implementation."""
    # Add user message to history
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message
    append_message(sess.id, user_msg)
    # The server will send run_end


class TestServerIntegration:
    """Test server RPC methods with minimal mocking."""
    
    @pytest.fixture
    def test_app(self, monkeypatch):
        """Create a test app with mocked agent."""
        spec = AgentSpec(
            name='TestAgent',
            header_title='Test Agent',
            page_title='Test Agent',
            welcome_text='Welcome to Test Agent',
            client_storage_prefix='test.',
            get_agent_instructions=lambda: 'Test instructions',
            build_agent=mock_build_agent,
        )
        
        # Mock the stream_agent function
        from omniagents.core import runner_bridge
        monkeypatch.setattr(runner_bridge, 'stream_agent', mock_stream_agent)
        
        # Build and return the app
        app = server_app.build_app(spec=spec)
        return app
    
    @pytest.fixture
    def client(self, test_app):
        """Create a test client."""
        return TestClient(test_app)
    
    def test_basic_rpc_flow(self, client):
        """Test basic RPC flow: start_run, get_history, list_sessions, archive."""
        with client.websocket_connect('/ws') as ws:
            # 1. Start a run
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "Hello"}
            })
            
            resp = ws.receive_json()
            assert "result" in resp
            assert "session_id" in resp["result"]
            assert "run_id" in resp["result"]
            
            session_id = resp["result"]["session_id"]
            
            # Should receive run_end event
            event = ws.receive_json()
            assert event["method"] == "run_end"
            
            # 2. Get session history
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "get_session_history",
                "params": {"session_id": session_id}
            })
            
            hist_resp = ws.receive_json()
            history = hist_resp["result"]
            assert len(history) >= 1
            assert history[0]["role"] == "user"
            assert history[0]["content"] == "Hello"
            
            # 3. List sessions
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "list_sessions",
                "params": {}
            })
            
            list_resp = ws.receive_json()
            sessions = list_resp["result"]
            
            # Find our session
            our_session = None
            for s in sessions:
                if s["id"] == session_id:
                    our_session = s
                    break
            
            assert our_session is not None
            assert our_session["archived"] is False
            
            # 4. Archive the session
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "archive_session",
                "params": {"session_id": session_id}
            })
            
            archive_resp = ws.receive_json()
            assert archive_resp["result"] is True
            
            # 5. Verify it's archived
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 5,
                "method": "list_sessions",
                "params": {}
            })
            
            list_resp2 = ws.receive_json()
            sessions2 = list_resp2["result"]
            
            for s in sessions2:
                if s["id"] == session_id:
                    assert s["archived"] is True
                    break
    
    def test_multiple_runs_same_session(self, client):
        """Test multiple runs in the same session."""
        with client.websocket_connect('/ws') as ws:
            # First run
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "First"}
            })
            
            resp1 = ws.receive_json()
            session_id = resp1["result"]["session_id"]
            run_id1 = resp1["result"]["run_id"]
            
            # Wait for completion
            ws.receive_json()  # run_end
            
            # Second run with same session
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "start_run",
                "params": {
                    "prompt": "Second",
                    "session_id": session_id
                }
            })
            
            resp2 = ws.receive_json()
            assert resp2["result"]["session_id"] == session_id
            run_id2 = resp2["result"]["run_id"]
            assert run_id1 != run_id2
            
            # Wait for completion
            ws.receive_json()  # run_end
            
            # Check history has both messages
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "get_session_history",
                "params": {"session_id": session_id}
            })
            
            hist_resp = ws.receive_json()
            history = hist_resp["result"]
            assert len(history) >= 2
            assert any(msg["content"] == "First" for msg in history)
            assert any(msg["content"] == "Second" for msg in history)
    
    def test_tool_approval_response(self, client):
        """Test tool approval response method exists."""
        with client.websocket_connect('/ws') as ws:
            # Try to send a tool approval response (even if no request)
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tool_approval_response",
                "params": {
                    "approval_id": "test_id",
                    "approved": True,
                    "always_approve": False,
                    "error_message": None
                }
            })
            
            resp = ws.receive_json()
            # Should return True (even if approval_id not found)
            assert resp["result"] is True
    
    def test_invalid_session_id(self, client):
        """Test handling of invalid session ID."""
        with client.websocket_connect('/ws') as ws:
            # Get history for non-existent session
            ws.send_json({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "get_session_history",
                "params": {"session_id": "non_existent"}
            })
            
            resp = ws.receive_json()
            # Should return empty history
            assert resp["result"] == []