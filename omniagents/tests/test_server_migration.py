"""
Test to verify server migration is working correctly.
"""

import pytest
from unittest.mock import Mock, patch

from omniagents.core.agent_service import AgentService
from omniagents.core.runner_bridge import stream_agent


def test_runner_not_imported():
    """Verify Runner is not imported in server modules."""
    # Check agent_service
    import omniagents.core.agent_service as agent_service_module
    assert not hasattr(agent_service_module, 'Runner')
    
    # Check runner_bridge is now in core
    import omniagents.core.runner_bridge as bridge_module
    assert not hasattr(bridge_module, 'Runner')
    
    # Check source doesn't contain Runner.run_streamed
    import inspect
    bridge_source = inspect.getsource(bridge_module)
    assert 'Runner.run_streamed' not in bridge_source


def test_agent_service_builds_agent_correctly():
    """Verify AgentService builds agents using spec's build_agent method."""
    # The import is inside the method, so check the source
    import inspect
    source = inspect.getsource(AgentService.start_run)
    # Should use spec's build_agent method, not create_unified_agent
    assert 'spec.build_agent' in source or '_default_build_agent' in source
    # Should NOT use create_unified_agent (causes circular dependency)
    assert 'create_unified_agent' not in source


def test_stream_agent_uses_runner():
    """Verify stream_agent uses Runner.run_streamed (the correct pattern)."""
    import inspect
    source = inspect.getsource(stream_agent)
    # Should use Runner.run_streamed with the agent as parameter
    assert '_Runner.run_streamed' in source or 'Runner.run_streamed' in source


@pytest.mark.asyncio  
async def test_agent_with_runner():
    """Test that server works with agents through Runner."""
    from omniagents.core.session import Session
    from unittest.mock import patch
    
    # Create mock agent (OpenAI SDK style)
    mock_agent = Mock()
    mock_agent.name = "TestAgent"
    
    # Create session
    session = Session()
    session.history = []
    
    # Mock channel
    mock_channel = Mock()
    mock_channel.async_call = Mock()
    
    # Mock Runner.run_streamed
    mock_result = Mock()
    
    # Mock stream_events to return empty generator
    async def mock_stream_events():
        return
        yield  # Make it a generator
    
    mock_result.stream_events = mock_stream_events
    
    # Mock dependencies
    with patch('omniagents.core.runner_bridge.append_message'):
        with patch('omniagents.core.runner_bridge._Runner') as mock_runner_class:
            mock_runner_class.run_streamed.return_value = mock_result
            
            # Run stream_agent
            await stream_agent(mock_agent, "Test", session, mock_channel)
            
            # Verify Runner.run_streamed was called with the agent
            mock_runner_class.run_streamed.assert_called_once()
            call_args = mock_runner_class.run_streamed.call_args[0]
            assert call_args[0] == mock_agent  # First arg is the agent
            assert call_args[1] == [{"role": "user", "content": "Test"}]  # Second arg is messages


if __name__ == "__main__":
    pytest.main([__file__, "-v"])