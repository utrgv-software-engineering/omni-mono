"""Integration tests for realtime agent functionality.

These tests verify end-to-end realtime session management, event streaming,
tool execution, handoffs, and guardrails in a real server environment.
"""

import asyncio
import socket
import base64
import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI

from omniagents.core.agents.specs import AgentSpec, RealtimeSettings
from omniagents.rpc import JsonRpcEndpoint, JsonRpcClient
from omniagents.rpc.realtime_service import RealtimeService


def _get_free_port():
    """Get an available port for testing."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _run_server(app, port):
    """Run a test server."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task


@pytest.fixture
def realtime_spec():
    """Create a test realtime agent spec."""
    return AgentSpec(
        name="TestRealtimeAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Test instructions",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(
            model_name="gpt-4o-realtime-preview",
            voice="alloy",
            modalities=["audio", "text"],
            input_audio_format="pcm16",
            output_audio_format="pcm16",
        ),
        project_slug="test_project",
        agent_slug="test_agent",
    )


@pytest.fixture
def settings_with_key():
    """Settings with API key."""
    return {"openai_api_key": "sk-test-key"}


@pytest_asyncio.fixture
async def test_server(realtime_spec, settings_with_key):
    """Create a test server with realtime service."""
    port = _get_free_port()

    # Mock the OpenAI SDK
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()

        async def mock_events():
            """Empty event generator."""
            return
            yield  # Make it a generator

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_session.send_audio = AsyncMock()
        mock_session.send_message = AsyncMock()
        mock_session.interrupt = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        # Create service and endpoint
        service = RealtimeService(realtime_spec, settings_with_key)
        endpoint = JsonRpcEndpoint(service)

        # Create app
        app = FastAPI()
        endpoint.register_route(app, "/ws/realtime")

        # Start server
        server, task = await _run_server(app, port)

        yield port, service

        # Cleanup
        await service.cleanup()
        server.should_exit = True
        await task


# --- Session Lifecycle Tests ---


@pytest.mark.asyncio
async def test_session_lifecycle(test_server):
    """Test complete session lifecycle: start, interact, stop."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            """Generate test events."""
            yield SimpleNamespace(type="turn_started")
            yield SimpleNamespace(type="turn_ended")

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        # Connect client
        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start session
            result = await client.call("start_session")
            assert "session_id" in result
            assert "run_id" in result
            session_id = result["session_id"]

            # Get session info
            info = await client.call("get_session_info", session_id=session_id)
            assert info["session_id"] == session_id
            assert info["active"] is True

            # List sessions
            sessions = await client.call("list_sessions")
            assert len(sessions["sessions"]) == 1
            assert sessions["sessions"][0]["session_id"] == session_id

            # Stop session
            success = await client.call("stop_session", session_id=session_id)
            assert success is True

            # Verify session is gone
            sessions = await client.call("list_sessions")
            assert len(sessions["sessions"]) == 0


@pytest.mark.asyncio
async def test_multiple_sessions(test_server):
    """Test managing multiple concurrent sessions."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start multiple sessions
            session_ids = []
            for i in range(3):
                result = await client.call("start_session")
                session_ids.append(result["session_id"])

            # Verify all sessions exist
            sessions = await client.call("list_sessions")
            assert len(sessions["sessions"]) == 3

            # Stop all sessions
            for session_id in session_ids:
                await client.call("stop_session", session_id=session_id)

            # Verify all stopped
            sessions = await client.call("list_sessions")
            assert len(sessions["sessions"]) == 0


# --- Event Streaming Tests ---


@pytest.mark.asyncio
async def test_event_streaming(test_server):
    """Test that session starts event streaming task."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            """Generate test events."""
            yield SimpleNamespace(type="turn_started")
            await asyncio.sleep(0.1)  # Simulate some delay
            yield SimpleNamespace(type="turn_ended")

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start session (triggers event streaming task)
            result = await client.call("start_session")
            session_id = result["session_id"]

            # Verify event streaming task was created
            assert session_id in service._event_tasks
            task = service._event_tasks[session_id]
            assert not task.done()  # Task should still be running

            # Give events time to stream
            await asyncio.sleep(0.2)

            # Cleanup
            await client.call("stop_session", session_id=session_id)

            # Verify task was cancelled
            assert session_id not in service._event_tasks


# --- Audio Input Tests ---


@pytest.mark.asyncio
async def test_send_audio(test_server):
    """Test sending audio input to agent."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.send_audio = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start session
            result = await client.call("start_session")
            session_id = result["session_id"]

            # Send audio
            fake_audio = b"fake_audio_data_12345"
            audio_base64 = base64.b64encode(fake_audio).decode()

            success = await client.call(
                "send_audio", session_id=session_id, audio_base64=audio_base64
            )

            assert success is True

            # Verify audio was sent to session
            mock_session.send_audio.assert_called_once_with(fake_audio, commit=False)

            # Cleanup
            await client.call("stop_session", session_id=session_id)


@pytest.mark.asyncio
async def test_send_text(test_server):
    """Test sending text input to agent."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.send_message = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start session
            result = await client.call("start_session")
            session_id = result["session_id"]

            # Send text
            success = await client.call(
                "send_text", session_id=session_id, text="Hello agent"
            )

            assert success is True

            # Verify text was sent
            mock_session.send_message.assert_called_once_with("Hello agent")

            # Cleanup
            await client.call("stop_session", session_id=session_id)


@pytest.mark.asyncio
async def test_interrupt(test_server):
    """Test interrupting agent response."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.interrupt = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start session
            result = await client.call("start_session")
            session_id = result["session_id"]

            # Interrupt
            success = await client.call("interrupt", session_id=session_id)

            assert success is True

            # Verify interrupt was called
            mock_session.interrupt.assert_called_once()

            # Cleanup
            await client.call("stop_session", session_id=session_id)


# --- Error Handling Tests ---


@pytest.mark.asyncio
async def test_session_not_found(test_server):
    """Test error when operating on non-existent session."""
    port, service = test_server

    async with JsonRpcClient.from_url(f"ws://127.0.0.1:{port}/ws/realtime") as client:
        # Try to get info for non-existent session
        with pytest.raises(Exception) as exc_info:
            await client.call("get_session_info", session_id="nonexistent")

        assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_duplicate_session(test_server):
    """Test error when starting duplicate session."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start first session with specific ID
            await client.call("start_session", session_id="test-session")

            # Try to start duplicate
            with pytest.raises(Exception) as exc_info:
                await client.call("start_session", session_id="test-session")

            assert "already exists" in str(exc_info.value).lower()

            # Cleanup
            await client.call("stop_session", session_id="test-session")


# --- Cleanup Tests ---


@pytest.mark.asyncio
async def test_service_cleanup(test_server):
    """Test that service cleanup stops all sessions."""
    port, service = test_server

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        async with JsonRpcClient.from_url(
            f"ws://127.0.0.1:{port}/ws/realtime"
        ) as client:
            # Start multiple sessions
            session_ids = []
            for i in range(3):
                result = await client.call("start_session")
                session_ids.append(result["session_id"])

            # Verify sessions exist
            sessions = await client.call("list_sessions")
            assert len(sessions["sessions"]) == 3

            # Cleanup service
            await service.cleanup()

            # Verify all sessions stopped
            sessions = await client.call("list_sessions")
            assert len(sessions["sessions"]) == 0
