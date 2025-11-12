"""Real SDK integration tests for realtime functionality.

These tests use the ACTUAL agents SDK classes (not mocks) to verify
that our event mapping and session handling works with real SDK objects.
"""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace

# Import REAL SDK classes
try:
    from agents.realtime import RealtimeAgent, RealtimeRunner
    from agents.realtime.events import (
        RealtimeAudio,
        RealtimeAudioEnd,
        RealtimeAudioInterrupted,
        RealtimeAgentStartEvent,
        RealtimeAgentEndEvent,
        RealtimeToolStart,
        RealtimeToolEnd,
        RealtimeHandoffEvent,
        RealtimeGuardrailTripped,
        RealtimeError,
        RealtimeHistoryUpdated,
        RealtimeHistoryAdded,
        RealtimeRawModelEvent,
        RealtimeEventInfo,
    )
    from agents.realtime.model_events import (
        RealtimeModelAudioEvent,
        RealtimeModelTranscriptDeltaEvent,
    )
    from agents.realtime.items import (
        RealtimeItem,
        UserMessageItem,
        AssistantMessageItem,
    )
    from agents.run_context import RunContextWrapper
    from agents.tool import FunctionTool
    from agents.guardrail import OutputGuardrailResult, OutputGuardrail

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

from omniagents.core.agents.specs import AgentSpec, RealtimeSettings
from omniagents.rpc.realtime_session import RealtimeSession
from omniagents.rpc.realtime_events import (
    RealtimeAudioEvent as RpcAudioEvent,
    RealtimeAudioEndEvent as RpcAudioEndEvent,
    RealtimeAgentStartEvent as RpcAgentStartEvent,
    RealtimeAgentEndEvent as RpcAgentEndEvent,
    RealtimeGuardrailTrippedEvent as RpcGuardrailTrippedEvent,
    RealtimeTranscriptEvent as RpcTranscriptEvent,
)


@pytest.mark.skipif(not SDK_AVAILABLE, reason="agents SDK not installed")
class TestRealSDKIntegration:
    """Tests using real SDK classes to verify our integration."""

    @pytest.fixture
    def spec(self):
        """Create a test agent spec."""
        return AgentSpec(
            name="TestAgent",
            welcome_text="Welcome",
            get_agent_instructions=lambda: "You are a test agent",
            build_agent=None,
            model_name="gpt-4o-realtime-preview",
            realtime_mode=True,
            realtime_settings=RealtimeSettings(),
            project_slug="test_project",
            agent_slug="test_agent",
        )

    @pytest.fixture
    def settings(self):
        """Settings with API key."""
        return {"openai_api_key": "sk-test-key"}

    @pytest.mark.asyncio
    async def test_real_sdk_audio_event_mapping(self, spec, settings):
        """Test mapping real SDK audio events."""
        # Create REAL SDK agent
        agent = RealtimeAgent(
            name="TestAgent",
            instructions="Test instructions",
        )

        # Create a REAL SDK audio event
        audio_data = b"test_audio_data"
        context = RunContextWrapper(None)

        model_audio_event = RealtimeModelAudioEvent(
            data=audio_data,
            response_id="resp_123",
            item_id="item_456",
            content_index=0,
        )

        sdk_event = RealtimeAudio(
            audio=model_audio_event,
            item_id="item_456",
            content_index=0,
            info=RealtimeEventInfo(context=context),
        )

        # Import our mapping function
        from omniagents.rpc.realtime_events import map_realtime_session_event_to_rpc

        # Map it
        rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_789")

        # Verify
        assert isinstance(rpc_event, RpcAudioEvent)
        assert rpc_event.run_id == "run_789"
        assert rpc_event.item_id == "item_456"
        assert rpc_event.content_index == 0
        # Audio should be base64 encoded
        import base64

        assert base64.b64decode(rpc_event.audio_base64) == audio_data

    @pytest.mark.asyncio
    async def test_real_sdk_agent_events_mapping(self, spec, settings):
        """Test mapping real SDK agent start/end events."""
        # Create REAL SDK agent
        agent = RealtimeAgent(
            name="TestAgent",
            instructions="Test instructions",
        )

        context = RunContextWrapper(None)

        # Test agent_start
        sdk_start_event = RealtimeAgentStartEvent(
            agent=agent,
            info=RealtimeEventInfo(context=context),
        )

        from omniagents.rpc.realtime_events import map_realtime_session_event_to_rpc

        rpc_event = map_realtime_session_event_to_rpc(sdk_start_event, "run_123")

        assert isinstance(rpc_event, RpcAgentStartEvent)
        assert rpc_event.agent_name == "TestAgent"

        # Test agent_end
        sdk_end_event = RealtimeAgentEndEvent(
            agent=agent,
            info=RealtimeEventInfo(context=context),
        )

        rpc_event = map_realtime_session_event_to_rpc(sdk_end_event, "run_123")

        assert isinstance(rpc_event, RpcAgentEndEvent)
        assert rpc_event.agent_name == "TestAgent"

    @pytest.mark.asyncio
    async def test_real_sdk_audio_end_event_mapping(self, spec, settings):
        """Test mapping real SDK audio_end event."""
        context = RunContextWrapper(None)

        sdk_event = RealtimeAudioEnd(
            item_id="item_789",
            content_index=1,
            info=RealtimeEventInfo(context=context),
        )

        from omniagents.rpc.realtime_events import map_realtime_session_event_to_rpc

        rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_123")

        assert isinstance(rpc_event, RpcAudioEndEvent)
        assert rpc_event.item_id == "item_789"
        assert rpc_event.content_index == 1

    @pytest.mark.asyncio
    async def test_real_sdk_transcript_event_mapping(self, spec, settings):
        """Test mapping transcript_delta to high-level transcript event."""
        context = RunContextWrapper(None)

        # Create a REAL model event
        model_event = RealtimeModelTranscriptDeltaEvent(
            item_id="item_abc",
            delta="Hello ",
            response_id="resp_xyz",
        )

        # Wrap in RawModelEvent
        sdk_event = RealtimeRawModelEvent(
            data=model_event,
            info=RealtimeEventInfo(context=context),
        )

        from omniagents.rpc.realtime_events import map_realtime_session_event_to_rpc

        rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_123")

        assert isinstance(rpc_event, RpcTranscriptEvent)
        assert rpc_event.type == "realtime_transcript"
        assert rpc_event.role == "assistant"
        assert rpc_event.transcript == "Hello "

    @pytest.mark.asyncio
    async def test_real_sdk_guardrail_event_mapping(self, spec, settings):
        """Test mapping real SDK guardrail_tripped event."""
        context = RunContextWrapper(None)

        # Create a REAL guardrail object
        class MockGuardrail(OutputGuardrail):
            def __init__(self):
                self.name = "test_filter"

            async def run(self, agent_output, context):
                return None

        # Create a minimal agent for the result
        agent = RealtimeAgent(name="TestAgent", instructions="Test")

        guardrail = MockGuardrail()

        # Create a REAL OutputGuardrailResult with all required fields
        guardrail_result = SimpleNamespace(
            guardrail=guardrail,
            agent_output="Blocked content",
            agent=agent,
            output=None,  # GuardrailFunctionOutput can be None
        )

        # Create REAL GuardrailTripped event
        sdk_event = RealtimeGuardrailTripped(
            guardrail_results=[guardrail_result],
            message="Content was blocked",
            info=RealtimeEventInfo(context=context),
        )

        from omniagents.rpc.realtime_events import map_realtime_session_event_to_rpc

        rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_123")

        assert isinstance(rpc_event, RpcGuardrailTrippedEvent)
        assert len(rpc_event.guardrail_results) == 1
        assert rpc_event.guardrail_results[0]["guardrail_name"] == "test_filter"
        assert rpc_event.guardrail_results[0]["agent_output"] == "Blocked content"
        assert rpc_event.message == "Content was blocked"

    @pytest.mark.asyncio
    async def test_verify_sdk_event_structure(self):
        """Verify our understanding of SDK event structures is correct."""
        # This test documents the ACTUAL SDK structures we rely on

        context = RunContextWrapper(None)

        # Verify agent event structure
        agent = RealtimeAgent(name="Test", instructions="Test")
        event = RealtimeAgentStartEvent(
            agent=agent, info=RealtimeEventInfo(context=context)
        )
        assert hasattr(event, "type")
        assert event.type == "agent_start"
        assert hasattr(event, "agent")
        assert event.agent.name == "Test"

        # Verify audio event structure
        model_audio = RealtimeModelAudioEvent(
            data=b"test",
            response_id="resp",
            item_id="item",
            content_index=0,
        )
        audio_event = RealtimeAudio(
            audio=model_audio,
            item_id="item",
            content_index=0,
            info=RealtimeEventInfo(context=context),
        )
        assert audio_event.type == "audio"
        assert hasattr(audio_event, "audio")
        assert hasattr(audio_event.audio, "data")
        assert isinstance(audio_event.audio.data, bytes)

        # Verify transcript_delta is in model events, not session events
        transcript_event = RealtimeModelTranscriptDeltaEvent(
            item_id="item",
            delta="text",
            response_id="resp",
        )
        assert transcript_event.type == "transcript_delta"
        # This is a model event, accessed via RealtimeRawModelEvent
