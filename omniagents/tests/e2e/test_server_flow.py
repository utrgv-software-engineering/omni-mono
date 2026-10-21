"""
Tests for server mode with unified agent architecture.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any, List

from omniagents.execution import AgentHostResources
from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.core.agents.service import AgentService
from omniagents.core.session import Session


class DummyAgent:
    """Minimal agent stub used by service tests."""

    def __init__(self, name: str = "TestAgent"):
        self.name = name
        self.tools: List[Any] = []
        self.instructions = "Test instructions"
        self.model = "gpt-4"
        self.model_settings = None
        self.mcp_servers: List[Any] = []
        self.mcp_config: Dict[str, Any] = {}
        self.handoffs: List[Any] = []
        self.handoff_description = None
        self.input_guardrails: List[Any] = []
        self.output_guardrails: List[Any] = []
        self.output_type = None
        self.prompt = None

    async def get_all_tools(self) -> List[Any]:
        return []

    def reset_run_state(self) -> None:
        pass


async def _drain_run_tasks() -> None:
    tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith("run_")]
    for task in tasks:
        try:
            await task
        except Exception:
            pass


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

        async def _builder(settings, mcp_server, stub_app):
            return DummyAgent()

        spec.build_agent = AsyncMock(side_effect=_builder)
        spec.project_slug = "test_project"
        spec.agent_slug = "test_agent"
        spec.tracing_config = {"provider": "none"}
        spec.mcp_servers = []
        spec.available_tools = []
        spec.settings_fields = []
        spec.safe_agent_options = SafeAgentOptions()
        spec.input_guardrails = []
        spec.output_guardrails = []
        spec.output_type = None
        spec.output_type_strict = True
        spec.reset_tool_choice = True
        spec.tool_use_behavior = "run_llm_again"
        spec.model_settings = None
        spec.prompt = None
        spec.handoff_configs = []
        spec.handoff_description = None
        spec.mcp_config = {}
        spec.variables = None
        spec.context = None
        spec.build_context = None
        spec.resolve_default_model_config = None
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
        sess = Session("test_project", "test_agent")
        sess.id = "test-session-123"
        sess.history = []
        sess.channels = []
        sess.pending_events = []
        return sess

    @pytest.mark.asyncio
    async def test_agent_creation_with_unified_factory(
        self, mock_spec, mock_channel, mock_session
    ):
        """Service should invoke spec.build_agent with expected arguments."""
        service = AgentService(
            mock_spec,
            AgentHostResources.create("test-e2e-server_flow-host"),
        )
        service.channel = mock_channel

        mock_session_manager = Mock()
        mock_session_manager.get_or_create.return_value = mock_session
        mock_session_manager.bind_task = Mock()
        service._session_manager = mock_session_manager

        with patch(
            "omniagents.core.agents.service.stream_agent",
            new_callable=AsyncMock,
            return_value={},
        ) as mock_stream:
            await service.start_run("Hello", {"mode": "none"}, session_id="test-123")
            await _drain_run_tasks()

        mock_spec.build_agent.assert_awaited_once()
        settings_arg, mcp_arg, stub_app = mock_spec.build_agent.await_args.args
        assert settings_arg == {}
        assert mcp_arg is None
        assert hasattr(stub_app, "_request_tool_approval")
        mock_session_manager.bind_task.assert_called_once()
        mock_stream.assert_awaited()

    @pytest.mark.asyncio
    async def test_safe_agent_configures_needs_approval(
        self, mock_spec, mock_channel, mock_session
    ):
        """When use_safe_agent is true, the service wires per-tool
        needs_approval predicates via configure_tool_approval."""
        mock_spec.use_safe_agent = True

        service = AgentService(
            mock_spec,
            AgentHostResources.create("test-e2e-server_flow-host"),
        )
        service.channel = mock_channel

        service._session_manager = Mock()
        service._session_manager.get_or_create.return_value = mock_session
        service._session_manager.bind_task = Mock()

        configure_calls: list[dict] = []

        def fake_configure(agent, **kwargs):
            configure_calls.append(kwargs)

        with (
            patch(
                "omniagents.core.agents.service.configure_tool_approval",
                fake_configure,
            ),
            patch(
                "omniagents.core.agents.service.stream_agent",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            await service.start_run("Hello", {"mode": "none"}, session_id="test-123")
            await _drain_run_tasks()

        mock_spec.build_agent.assert_awaited()
        assert configure_calls, "configure_tool_approval should be invoked"
        # Predicate must be scoped to the current session.
        assert configure_calls[0]["session"] is mock_session

    @pytest.mark.asyncio
    async def test_stream_agent_with_unified_agent(self, mock_session):
        """Test stream_agent works with unified agent's run_streamed method."""
        from omniagents.core.runtime.bridge import stream_agent

        # Create mock unified agent
        mock_agent = Mock()
        mock_agent.output_type = None
        mock_agent.get_all_tools = AsyncMock(return_value=[])
        mock_agent.handoffs = []
        mock_agent.input_guardrails = []
        mock_agent.output_guardrails = []
        mock_stream_result = Mock()
        # The bridge inspects SDK 0.16+ tool-approval interruptions.
        mock_stream_result.interruptions = []

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

        # Mock RPC channel
        mock_channel = Mock()
        mock_channel.async_call = AsyncMock()
        mock_session.channels.append(mock_channel)

        # Mock dependencies
        with patch("omniagents.core.runtime.bridge.ItemHelpers") as mock_helpers:
            mock_helpers.text_message_output.return_value = "Hello!"

            with patch(
                "agents.Runner.run_streamed", return_value=mock_stream_result
            ) as mock_run:
                # Run stream_agent
                await stream_agent(
                    mock_agent, "Test prompt", mock_session, mock_channel
                )

                mock_run.assert_called_once()
                args, kwargs = mock_run.call_args
                assert args[0] == mock_agent
                assert args[1] == [{"role": "user", "content": "Test prompt"}]

                assert mock_channel.async_call.called
                calls = mock_channel.async_call.call_args_list
                assert any(call[0][0] == "message_output" for call in calls)

    @pytest.mark.asyncio
    async def test_agent_builder_called_each_run(
        self, mock_spec, mock_channel, mock_session
    ):
        """Service should build a fresh agent for each run."""
        service = AgentService(
            mock_spec,
            AgentHostResources.create("test-e2e-server_flow-host"),
        )
        service.channel = mock_channel

        service._session_manager = Mock()
        service._session_manager.get_or_create.return_value = mock_session
        service._session_manager.bind_task = Mock()

        with patch(
            "omniagents.core.agents.service.stream_agent",
            new_callable=AsyncMock,
            return_value={},
        ):
            await service.start_run("First", {"mode": "none"}, session_id="test-123")
            await _drain_run_tasks()
            await service.start_run("Second", {"mode": "none"}, session_id="test-123")
            await _drain_run_tasks()

        assert mock_spec.build_agent.await_count == 2

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
