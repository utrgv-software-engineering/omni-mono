"""
Integration tests for server RPC methods that match actual implementation.
"""

import asyncio
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from agents import Agent


# Mock agent for testing
async def mock_build_agent(settings, mcp, app):
    return Agent(
        name="test_agent",
        tools=[],
        instructions="Test instructions",
        model="gpt-3.5-turbo",
    )


# Minimal mock stream_agent
async def mock_stream_agent(
    agent, prompt, sess, channel, max_turns=20, **_ignored
):
    """Mock agent streaming - minimal implementation."""
    # Add user message to history
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)
    # The server will send run_end


def _wait_for_result(ws, target_id):
    while True:
        resp = ws.receive_json()
        if resp.get("id") == target_id and ("result" in resp or "error" in resp):
            return resp


def _wait_for_method(ws, method_name):
    while True:
        message = ws.receive_json()
        if message.get("method") == method_name:
            return message


def _wait_for_run_completion(ws, request_id):
    result = None
    run_end = None
    while not (result and run_end):
        message = ws.receive_json()
        if message.get("id") == request_id and (
            "result" in message or "error" in message
        ):
            result = message
        elif message.get("method") == "run_end":
            run_end = message
    return result, run_end


class TestServerIntegration:
    """Test server RPC methods with minimal mocking."""

    @pytest.fixture
    def test_app(self, monkeypatch):
        """Create a test app with mocked agent."""
        spec = AgentSpec(
            name="TestAgent",
            welcome_text="Welcome to Test Agent",
            get_agent_instructions=lambda: "Test instructions",
            build_agent=mock_build_agent,
        )

        async def _describe_session(_service, session, args):
            return {
                "session_id": session.id if session else None,
                "args": args or {},
            }

        def _register(service):
            service.register_server_function(
                "session.describe",
                _describe_session,
                description="Return session metadata",
                params_schema={"type": "object"},
                result_schema={"type": "object"},
            )

        spec.register_server_functions = _register

        # Mock the stream_agent function
        from omniagents.core.runtime import bridge as runner_bridge

        monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
        monkeypatch.setattr(
            "omniagents.core.agents.service.stream_agent", mock_stream_agent
        )

        # Build and return the app
        app = server_app.build_app(spec=spec)
        return app

    @pytest.fixture
    def client(self, test_app):
        """Create a test client."""
        return TestClient(test_app)

    def test_basic_rpc_flow(self, client):
        """Test basic RPC flow: start_run, get_history, list_sessions, archive."""
        with client.websocket_connect("/ws") as ws:
            # 1. Start a run
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "start_run",
                    "params": {"prompt": "Hello"},
                }
            )
            start_resp, _ = _wait_for_run_completion(ws, 1)

            assert "result" in start_resp
            assert "session_id" in start_resp["result"]
            assert "run_id" in start_resp["result"]

            session_id = start_resp["result"]["session_id"]

            # 2. Get session history
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "get_session_history",
                    "params": {"session_id": session_id},
                }
            )

            hist_resp = ws.receive_json()
            history = hist_resp["result"]
            assert len(history) >= 1
            assert history[0]["role"] == "user"
            assert history[0]["content"] == "Hello"

            # 3. List sessions
            ws.send_json(
                {"jsonrpc": "2.0", "id": 3, "method": "list_sessions", "params": {}}
            )

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
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "archive_session",
                    "params": {"session_id": session_id},
                }
            )

            archive_resp = ws.receive_json()
            assert archive_resp["result"] is True

            # 5. Verify it's archived
            ws.send_json(
                {"jsonrpc": "2.0", "id": 5, "method": "list_sessions", "params": {}}
            )

            list_resp2 = ws.receive_json()
            sessions2 = list_resp2["result"]

            for s in sessions2:
                if s["id"] == session_id:
                    assert s["archived"] is True
                    break

    def test_multiple_runs_same_session(self, client):
        """Test multiple runs in the same session."""
        with client.websocket_connect("/ws") as ws:
            # First run
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "start_run",
                    "params": {"prompt": "First"},
                }
            )

            resp1, _ = _wait_for_run_completion(ws, 1)

            session_id = resp1["result"]["session_id"]
            run_id1 = resp1["result"]["run_id"]

            # Second run with same session
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "start_run",
                    "params": {"prompt": "Second", "session_id": session_id},
                }
            )

            resp2, _ = _wait_for_run_completion(ws, 2)

            assert resp2["result"]["session_id"] == session_id
            run_id2 = resp2["result"]["run_id"]
            assert run_id1 != run_id2

            # Check history has both messages
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "get_session_history",
                    "params": {"session_id": session_id},
                }
            )

            hist_resp = ws.receive_json()
            history = hist_resp["result"]
            assert len(history) >= 2
            assert any(msg["content"] == "First" for msg in history)
            assert any(msg["content"] == "Second" for msg in history)

    def test_stop_run_cancels_active_run(self, client, monkeypatch):
        """Verify stop_run cancels the in-flight task and emits run_end."""

        async def blocking_stream_agent(*_args, **_kwargs):
            try:
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise

        monkeypatch.setattr(
            "omniagents.core.agents.service.stream_agent", blocking_stream_agent
        )

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "start_run",
                    "params": {"prompt": "Blocking"},
                }
            )
            start_resp = _wait_for_result(ws, 1)
            run_id = start_resp["result"]["run_id"]

            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "stop_run",
                    "params": {"run_id": run_id},
                }
            )

            stop_resp = None
            run_end = None
            while not (stop_resp and run_end):
                message = ws.receive_json()
                if message.get("method") == "run_end":
                    run_end = message
                elif message.get("id") == 2:
                    stop_resp = message

            assert stop_resp["result"] is True
            assert run_end["params"]["end_reason"] == "cancelled"

    def test_send_user_message_appends_history(self, client, monkeypatch):
        """send_user_message should append to the session history."""

        async def slow_stream_agent(*_args, **_kwargs):
            await asyncio.sleep(0.3)
            return {}

        monkeypatch.setattr(
            "omniagents.core.agents.service.stream_agent", slow_stream_agent
        )

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "start_run",
                    "params": {"prompt": "Initial"},
                }
            )
            start_resp = _wait_for_result(ws, 1)
            run_id = start_resp["result"]["run_id"]
            session_id = start_resp["result"]["session_id"]

            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "send_user_message",
                    "params": {"run_id": run_id, "content": "Follow-up"},
                }
            )
            msg_resp = _wait_for_result(ws, 2)
            assert msg_resp["result"] is None

            _wait_for_method(ws, "run_end")

            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "get_session_history",
                    "params": {"session_id": session_id},
                }
            )
            hist_resp = _wait_for_result(ws, 3)
            history = hist_resp["result"]
            assert any(entry.get("content") == "Follow-up" for entry in history)

    def test_client_functions_records_capabilities(self, client):
        """client_functions handshake should persist declared capabilities."""
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "client_functions",
                    "params": {
                        "version": 2,
                        "functions": [{"name": "ui.custom", "description": "demo"}],
                    },
                }
            )
            resp = _wait_for_result(ws, 1)
            assert resp["result"] is True

        service = client.app.state.agent_service
        assert any(
            entry.get("version") == 2 and entry.get("functions")
            for entry in service._client_capabilities.values()
        )

    def test_get_agent_info_and_server_functions(self, client):
        """Expose agent metadata and registered server functions over RPC."""
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "get_agent_info",
                    "params": {},
                }
            )
            info = _wait_for_result(ws, 1)["result"]
            assert info["name"] == "TestAgent"
            assert "Welcome" in info["welcome_text"]

            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "list_server_functions",
                    "params": {},
                }
            )
            functions = _wait_for_result(ws, 2)["result"]
            assert any(fn["name"] == "session.describe" for fn in functions)

    def test_get_user_history_returns_recent_messages(self, client):
        """get_user_history should surface latest user messages across sessions."""
        with client.websocket_connect("/ws") as ws:
            for idx, prompt in enumerate(["First", "Second"], start=1):
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": idx,
                        "method": "start_run",
                        "params": {"prompt": prompt},
                    }
                )
                _wait_for_run_completion(ws, idx)

            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "get_user_history",
                    "params": {"limit": 5},
                }
            )
            history = _wait_for_result(ws, 99)["result"]
            assert any(entry.get("content") == "Second" for entry in history)

    def test_invalid_session_id(self, client):
        """Test handling of invalid session ID."""
        with client.websocket_connect("/ws") as ws:
            # Get history for non-existent session
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "get_session_history",
                    "params": {"session_id": "non_existent"},
                }
            )

            resp = ws.receive_json()
            # Should return empty history
            assert resp["result"] == []
