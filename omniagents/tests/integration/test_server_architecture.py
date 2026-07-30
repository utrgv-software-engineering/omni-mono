"""
Test to verify server migration is working correctly.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from omniagents.core.agents.service import AgentService
from omniagents.core.runtime.bridge import stream_agent


def test_runner_not_imported():
    """Verify Runner is imported locally, not at module level in server modules."""
    # Check agent_service
    import omniagents.core.agents.service as agent_service_module

    assert not hasattr(agent_service_module, "Runner")

    # Check runner_bridge is now in core
    import omniagents.core.runtime.bridge as bridge_module

    assert not hasattr(bridge_module, "Runner")

    # Check that Runner is imported locally within functions (which is acceptable)
    import inspect

    bridge_source = inspect.getsource(bridge_module)
    # Should use local import pattern to avoid circular dependencies
    assert "from agents import Runner as _Runner" in bridge_source
    # The usage of Runner.run_streamed is fine with local import
    assert "_Runner.run_streamed" in bridge_source


def test_agent_service_builds_agent_correctly():
    """Verify AgentService builds agents using spec's build_agent method."""
    # The import is inside the method, so check the source
    import inspect

    source = inspect.getsource(AgentService.start_run)
    # Should use internal builder helper rather than direct create_unified_agent
    assert "_build_agent_for_session" in source
    # Should NOT use create_unified_agent (causes circular dependency)
    assert "create_unified_agent" not in source


def test_stream_agent_uses_runner():
    """Verify stream_agent uses Runner.run_streamed (the correct pattern)."""
    import inspect

    source = inspect.getsource(stream_agent)
    # Should use Runner.run_streamed with the agent as parameter
    assert "_Runner.run_streamed" in source or "Runner.run_streamed" in source


@pytest.mark.asyncio
async def test_agent_with_runner():
    """Test that server works with agents through Runner."""
    from omniagents.core.session import Session
    from unittest.mock import patch

    # Create mock agent (OpenAI SDK style)
    mock_agent = Mock()
    mock_agent.name = "TestAgent"

    # Create session
    session = Session("test_project", "test_agent")
    session.history = []

    # Mock channel
    mock_channel = Mock()
    mock_channel.async_call = AsyncMock()

    # Mock Runner.run_streamed
    mock_result = Mock()
    # The bridge inspects SDK 0.16+ tool-approval interruptions.
    mock_result.interruptions = []

    # Mock stream_events to return empty generator
    async def mock_stream_events():
        return
        yield  # Make it a generator

    mock_result.stream_events = mock_stream_events

    # Mock dependencies
    with patch(
        "agents.Runner.run_streamed", return_value=mock_result
    ) as mock_run_streamed:

        # Run stream_agent
        await stream_agent(mock_agent, "Test", session, mock_channel)

        # Verify Runner.run_streamed was called with the agent
        mock_run_streamed.assert_called_once()
        call_args = mock_run_streamed.call_args[0]
        assert call_args[0] == mock_agent  # First arg is the agent
        assert call_args[1] == [
            {"role": "user", "content": "Test"}
        ]  # Second arg is messages


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
