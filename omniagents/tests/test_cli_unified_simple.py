"""
Simple unit tests for CLI unified agent integration.
Tests the integration points without importing the full CLI runner.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from typing import List, Any

from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.rpc.agents.local import LocalAgent
from omniagents.core.agents.factory import create_unified_agent


class TestCLIUnifiedIntegration:
    """Test CLI integration with unified agent architecture."""
    
    @pytest.fixture
    def mock_spec(self):
        """Create a mock AgentSpec."""
        spec = Mock(spec=AgentSpec)
        spec.name = "TestAgent"
        spec.model_name = "gpt-4"
        spec.available_tools = []
        spec.get_agent_instructions = Mock(return_value="Test instructions")
        spec.use_safe_agent = False
        spec.safe_agent_options = SafeAgentOptions()
        spec.remote_ws_url = None
        spec.remote_auth_token_key = None
        spec.settings_fields = []
        spec.max_turns = 10
        spec.build_agent = None  # Add build_agent attribute
        return spec
    
    @pytest.mark.asyncio
    async def test_local_agent_with_mcp_initialization(self, mock_spec):
        """Test LocalAgentRpcAdapter properly handles MCP servers."""
        settings = {"openai_api_key": "test-key"}
        approval_cb = Mock()
        mcp_servers = [Mock(name="TestMCPServer")]
        
        adapter = LocalAgent(mock_spec, settings, approval_cb, mcp_servers)
        
        # Verify MCP servers are stored
        assert adapter.mcp_servers == mcp_servers
        
        # Initialize and verify custom builder uses MCP
        with patch('omniagents.core.agents.builder._default_build_agent', new_callable=AsyncMock) as mock_build:
            mock_agent = Mock()
            mock_build.return_value = mock_agent
            
            await adapter.initialize()
            
            # Get the custom builder function
            custom_builder = adapter.spec.build_agent
            assert custom_builder is not None
            
            # Call it with stub arguments
            result = await custom_builder({}, None, Mock())
            
            # Verify MCP server was passed to builder
            mock_build.assert_called_once()
            call_args = mock_build.call_args
            assert call_args[0][1] == mcp_servers[0]  # Second arg is MCP server
            
        await adapter.cleanup()
    
    @pytest.mark.asyncio
    async def test_factory_creates_local_agent_with_mcp(self, mock_spec):
        """Test factory properly passes MCP servers to local agent."""
        settings = {"openai_api_key": "test-key"}
        mcp_servers = [Mock(name="TestMCP")]
        
        agent = await create_unified_agent(mock_spec, settings, None, mcp_servers)
        
        assert isinstance(agent, LocalAgent)
        assert agent.mcp_servers == mcp_servers
    
    @pytest.mark.asyncio
    async def test_local_agent_run_streamed(self, mock_spec):
        """Test LocalAgentRpcAdapter run_streamed method."""
        settings = {"openai_api_key": "test-key"}
        adapter = LocalAgent(mock_spec, settings)
        
        messages = [{"role": "user", "content": "Hello"}]
        result = adapter.run_streamed(messages)
        
        # Verify result has stream_events method
        assert hasattr(result, 'stream_events')
        assert callable(result.stream_events)
    
    @pytest.mark.asyncio
    async def test_safe_agent_approval_callback(self, mock_spec):
        """Test approval callback is properly passed through."""
        mock_spec.use_safe_agent = True
        settings = {"openai_api_key": "test-key"}
        approval_cb = Mock()
        
        adapter = LocalAgent(mock_spec, settings, approval_cb)
        
        assert adapter.approval_callback == approval_cb
        
        # Initialize and verify approval callback is used
        with patch('omniagents.core.agents.builder._default_build_agent', new_callable=AsyncMock) as mock_build:
            await adapter.initialize()
            
            # Get the custom builder
            custom_builder = adapter.spec.build_agent
            
            # Call it
            await custom_builder({}, None, Mock())
            
            # Verify approval callback was passed
            call_args = mock_build.call_args
            assert call_args[0][3] == approval_cb  # Fourth arg is approval callback
            
        await adapter.cleanup()
    
    def test_model_settings_compatibility(self, mock_spec):
        """Test agent has model_settings for compatibility."""
        adapter = LocalAgent(mock_spec, {})
        
        # Verify model_settings exists (required by CLI)
        assert hasattr(adapter, 'model_settings')
        assert isinstance(adapter.model_settings, dict)