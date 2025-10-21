"""
Tests for server mode with unified agent architecture.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, List

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.service import AgentService
from omniagents.core.session import Session


class TestServerUnifiedAgent:
    """Test server mode with unified agent architecture."""

    @pytest.fixture
    def mock_spec(self):
        """Create a mock AgentSpec for testing."""
        spec = Mock(spec=AgentSpec)
        spec.name = "TestAgent"
        spec.model_name = "gpt-4"
        spec.use_safe_agent = False
        spec.max_turns = 10
        spec.get_agent_instructions = Mock(return_value="Test instructions")
        return spec

    @pytest.fixture
    def mock_channel(self):
        """Create a mock RPC channel."""
        channel = Mock()
        channel.async_call = AsyncMock()
        return channel

    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        sess = Session()
        sess.id = "test-session-123"
        sess.history = []
        sess.channels = []
        sess.pending_events = []
        return sess

    @pytest.mark.asyncio
    async def test_agent_creation_with_unified_factory(self, mock_spec, mock_channel):
        """Test that AgentService creates agents using unified factory."""
        with patch(
            "omniagents.core.agents.factory.create_unified_agent",
            new_callable=AsyncMock,
        ) as mock_create:
            # Mock the unified agent
            mock_agent = AsyncMock()
            mock_create.return_value = mock_agent

            # Create service
            service = AgentService(mock_spec)
            service.channel = mock_channel

            # Mock session manager
            with patch("omniagents.core.agents.service.sm") as mock_sm:
                mock_session = Mock()
                mock_session.history = []
                mock_session.channels = []
                mock_session.pending_events = []
                mock_sm.get_or_create.return_value = mock_session
                mock_sm.bind_task = Mock()

                # Mock stream_agent to be async
                with patch(
                    "omniagents.core.agents.service.stream_agent",
                    new_callable=AsyncMock,
                ) as mock_stream:
                    # Mock append_message to avoid database errors
                    with patch("omniagents.core.session.append_message"):
                        # Start a run - this will create a task
                        result = await service.start_run("Hello", session_id="test-123")

                        # Find the task that was created
                        tasks = [
                            t
                            for t in asyncio.all_tasks()
                            if t.get_name().startswith("run_")
                        ]
                        assert len(tasks) == 1

                        # Wait for the task to complete
                        await tasks[0]

                        # Verify unified factory was called
                        mock_create.assert_called_once()
                        call_args = mock_create.call_args
                        assert call_args.kwargs["spec"] == mock_spec
                        assert call_args.kwargs["settings"] == {}
                        assert call_args.kwargs["approval_callback"] is None
                        assert call_args.kwargs["mcp_servers"] is None

                        # Verify agent was stored in session
                        assert hasattr(mock_session, "agent")
                        assert mock_session.agent == mock_agent

    @pytest.mark.asyncio
    async def test_safe_agent_approval_callback(self, mock_spec, mock_channel):
        """Test that approval callback is passed for SafeAgent."""
        mock_spec.use_safe_agent = True

        with patch(
            "omniagents.core.agents.factory.create_unified_agent"
        ) as mock_create:
            mock_agent = AsyncMock()
            mock_create.return_value = mock_agent

            service = AgentService(mock_spec)
            service.channel = mock_channel

            with patch("omniagents.core.agents.service.sm") as mock_sm:
                mock_session = Mock()
                mock_session.history = []
                mock_session.channels = []
                mock_session.pending_events = []
                mock_sm.get_or_create.return_value = mock_session

                with patch("omniagents.core.agents.service.stream_agent"):
                    await service.start_run("Hello", session_id="test-123")

                    # Verify approval callback was passed
                    mock_create.assert_called_once()
                    call_args = mock_create.call_args
                    assert call_args.kwargs["approval_callback"] is not None
                    assert callable(call_args.kwargs["approval_callback"])

    @pytest.mark.asyncio
    async def test_stream_agent_with_unified_agent(self, mock_session):
        """Test stream_agent works with unified agent's run_streamed method."""
        from omniagents.core.runtime.bridge import stream_agent

        # Create mock unified agent
        mock_agent = Mock()
        mock_stream_result = Mock()

        # Create mock events
        mock_event1 = Mock()
        mock_event1.type = "run_item_stream_event"
        mock_event1.item = Mock()
        mock_event1.item.type = "message_output_item"
        mock_event1.item.to_input_item = Mock(
            return_value={"role": "assistant", "content": "Hello!"}
        )

        mock_event2 = Mock()
        mock_event2.type = "other_event"  # Should be skipped

        # Setup async generator for stream_events
        async def mock_stream_events():
            yield mock_event1
            yield mock_event2

        mock_stream_result.stream_events = mock_stream_events
        mock_agent.run_streamed = Mock(return_value=mock_stream_result)

        # Mock RPC channel
        mock_channel = Mock()
        mock_channel.async_call = AsyncMock()

        # Mock dependencies
        with patch("omniagents.core.runtime.bridge.append_message"):
            with patch("omniagents.core.runtime.bridge.ItemHelpers") as mock_helpers:
                mock_helpers.text_message_output.return_value = "Hello!"

                # Run stream_agent
                await stream_agent(
                    mock_agent, "Test prompt", mock_session, mock_channel
                )

                # Verify run_streamed was called with correct input
                mock_agent.run_streamed.assert_called_once()
                call_args = mock_agent.run_streamed.call_args[0][0]
                assert call_args == [{"role": "user", "content": "Test prompt"}]

                # Verify events were processed
                assert mock_channel.async_call.called
                # Should have called for message_output event
                calls = mock_channel.async_call.call_args_list
                assert any(call[0][0] == "message_output" for call in calls)

    @pytest.mark.asyncio
    async def test_agent_reuse_across_runs(self, mock_spec, mock_channel, mock_session):
        """Test that agents are reused for subsequent runs in same session."""
        with patch(
            "omniagents.core.agents.factory.create_unified_agent"
        ) as mock_create:
            mock_agent = AsyncMock()
            mock_create.return_value = mock_agent

            service = AgentService(mock_spec)
            service.channel = mock_channel

            with patch("omniagents.core.agents.service.sm") as mock_sm:
                mock_sm.get_or_create.return_value = mock_session

                with patch("omniagents.core.agents.service.stream_agent"):
                    # First run - should create agent
                    await service.start_run("First", session_id="test-123")
                    assert mock_create.call_count == 1
                    assert hasattr(mock_session, "agent")

                    # Second run - should reuse agent
                    await service.start_run("Second", session_id="test-123")
                    assert mock_create.call_count == 1  # Not called again

    def test_no_runner_imports(self):
        """Verify that Runner is imported locally, not at module level."""
        import omniagents.core.runtime.bridge as bridge_module

        # Check that Runner is not in the module's namespace (should be local import only)
        assert not hasattr(bridge_module, "Runner")

        # Check that Runner is imported locally within functions (which is acceptable)
        import inspect

        source = inspect.getsource(bridge_module)
        # Should use local import pattern to avoid circular dependencies
        assert "from agents import Runner as _Runner" in source
        # Should not be imported at module level
        assert not source.startswith("from agents import Runner")
