"""Tests for realtime event types and mapping."""

import base64
from types import SimpleNamespace

import pytest

from omniagents.rpc.realtime_events import (
    RealtimeAudioEvent,
    RealtimeAudioEndEvent,
    RealtimeAudioInterruptedEvent,
    RealtimeTranscriptDeltaEvent,
    RealtimeTurnStartedEvent,
    RealtimeTurnEndedEvent,
    RealtimeToolStartEvent,
    RealtimeToolEndEvent,
    RealtimeErrorEvent,
    RealtimeGuardrailTrippedEvent,
    RealtimeHandoffEvent,
    RealtimeAgentStartEvent,
    RealtimeAgentEndEvent,
    RealtimeHistoryUpdatedEvent,
    RealtimeHistoryAddedEvent,
    RealtimeRawModelEvent,
    map_realtime_session_event_to_rpc,
)


# --- Event Type Creation Tests ---


def test_realtime_audio_event_creation():
    """Test creating RealtimeAudioEvent."""
    event = RealtimeAudioEvent(
        run_id="run_123",
        audio_base64="dGVzdCBhdWRpbw==",
        item_id="item_456",
        content_index=0,
        delta_ms=100,
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_audio"
    assert event.audio_base64 == "dGVzdCBhdWRpbw=="
    assert event.item_id == "item_456"
    assert event.content_index == 0
    assert event.delta_ms == 100


def test_realtime_audio_end_event_creation():
    """Test creating RealtimeAudioEndEvent."""
    event = RealtimeAudioEndEvent(run_id="run_123", item_id="item_456", content_index=0)

    assert event.run_id == "run_123"
    assert event.type == "realtime_audio_end"
    assert event.item_id == "item_456"
    assert event.content_index == 0


def test_realtime_audio_interrupted_event_creation():
    """Test creating RealtimeAudioInterruptedEvent."""
    event = RealtimeAudioInterruptedEvent(
        run_id="run_123", item_id="item_456", content_index=0
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_audio_interrupted"
    assert event.item_id == "item_456"
    assert event.content_index == 0


def test_realtime_transcript_delta_event_creation():
    """Test creating RealtimeTranscriptDeltaEvent."""
    event = RealtimeTranscriptDeltaEvent(
        run_id="run_123", item_id="item_456", content_index=0, delta="Hello"
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_transcript_delta"
    assert event.delta == "Hello"


def test_realtime_turn_started_event_creation():
    """Test creating RealtimeTurnStartedEvent."""
    event = RealtimeTurnStartedEvent(run_id="run_123")

    assert event.run_id == "run_123"
    assert event.type == "realtime_turn_started"


def test_realtime_turn_ended_event_creation():
    """Test creating RealtimeTurnEndedEvent."""
    event = RealtimeTurnEndedEvent(run_id="run_123")

    assert event.run_id == "run_123"
    assert event.type == "realtime_turn_ended"


def test_realtime_tool_start_event_creation():
    """Test creating RealtimeToolStartEvent."""
    event = RealtimeToolStartEvent(
        run_id="run_123",
        tool_name="get_weather",
        tool_call_id="call_789",
        arguments='{"location": "NYC"}',
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_tool_start"
    assert event.tool_name == "get_weather"
    assert event.tool_call_id == "call_789"
    assert event.arguments == '{"location": "NYC"}'


def test_realtime_tool_end_event_creation():
    """Test creating RealtimeToolEndEvent."""
    event = RealtimeToolEndEvent(
        run_id="run_123",
        tool_name="get_weather",
        tool_call_id="call_789",
        output="Sunny, 75°F",
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_tool_end"
    assert event.tool_name == "get_weather"
    assert event.tool_call_id == "call_789"
    assert event.output == "Sunny, 75°F"


def test_realtime_error_event_creation():
    """Test creating RealtimeErrorEvent."""
    event = RealtimeErrorEvent(
        run_id="run_123",
        error_message="Connection failed",
        error_type="network_error",
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_error"
    assert event.error_message == "Connection failed"
    assert event.error_type == "network_error"


def test_realtime_guardrail_tripped_event_creation():
    """Test creating RealtimeGuardrailTrippedEvent with real SDK structure."""
    event = RealtimeGuardrailTrippedEvent(
        run_id="run_123",
        guardrail_results=[
            {
                "guardrail_name": "content_filter",
                "agent_output": "Inappropriate content",
            }
        ],
        message="Output was blocked",
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_guardrail_tripped"
    assert len(event.guardrail_results) == 1
    assert event.guardrail_results[0]["guardrail_name"] == "content_filter"
    assert event.message == "Output was blocked"


def test_realtime_handoff_event_creation():
    """Test creating RealtimeHandoffEvent."""
    event = RealtimeHandoffEvent(
        run_id="run_123", from_agent="support_agent", to_agent="sales_agent"
    )

    assert event.run_id == "run_123"
    assert event.type == "realtime_handoff"
    assert event.from_agent == "support_agent"
    assert event.to_agent == "sales_agent"


def test_realtime_agent_start_event_creation():
    """Test creating RealtimeAgentStartEvent."""
    event = RealtimeAgentStartEvent(run_id="run_123", agent_name="support_agent")

    assert event.run_id == "run_123"
    assert event.type == "realtime_agent_start"
    assert event.agent_name == "support_agent"


def test_realtime_agent_end_event_creation():
    """Test creating RealtimeAgentEndEvent."""
    event = RealtimeAgentEndEvent(run_id="run_123", agent_name="support_agent")

    assert event.run_id == "run_123"
    assert event.type == "realtime_agent_end"
    assert event.agent_name == "support_agent"


def test_event_to_dict():
    """Test converting event to dictionary."""
    event = RealtimeAudioEndEvent(run_id="run_123", item_id="item_456", content_index=0)

    event_dict = event.to_dict()

    assert event_dict["run_id"] == "run_123"
    assert event_dict["type"] == "realtime_audio_end"
    assert event_dict["item_id"] == "item_456"
    assert event_dict["content_index"] == 0


# --- Event Mapping Tests ---


def test_map_audio_event():
    """Test mapping audio event from SDK to RPC."""
    audio_data = b"test_audio_data"
    audio_base64 = base64.b64encode(audio_data).decode("utf-8")

    sdk_event = SimpleNamespace(
        type="audio",
        audio=SimpleNamespace(data=audio_data),  # SDK uses 'data' not 'audio'
        item_id="item_123",
        content_index=0,
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeAudioEvent)
    assert rpc_event.run_id == "run_456"
    assert rpc_event.audio_base64 == audio_base64
    assert rpc_event.item_id == "item_123"
    assert rpc_event.content_index == 0
    assert rpc_event.delta_ms is None  # SDK doesn't provide delta_ms


def test_map_audio_event_already_base64():
    """Test mapping audio event when audio is a string (fallback case)."""
    audio_base64 = "dGVzdCBhdWRpbw=="

    sdk_event = SimpleNamespace(
        type="audio",
        audio=SimpleNamespace(data=audio_base64),  # SDK uses 'data' field
        item_id="item_123",
        content_index=0,
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeAudioEvent)
    assert rpc_event.audio_base64 == audio_base64


def test_map_audio_end_event():
    """Test mapping audio_end event."""
    sdk_event = SimpleNamespace(type="audio_end", item_id="item_123", content_index=0)

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeAudioEndEvent)
    assert rpc_event.run_id == "run_456"
    assert rpc_event.item_id == "item_123"


def test_map_audio_interrupted_event():
    """Test mapping audio_interrupted event."""
    sdk_event = SimpleNamespace(
        type="audio_interrupted", item_id="item_123", content_index=1
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeAudioInterruptedEvent)
    assert rpc_event.run_id == "run_456"
    assert rpc_event.item_id == "item_123"
    assert rpc_event.content_index == 1


def test_map_transcript_delta_event():
    """Test that transcript_delta is NOT mapped as session event.

    transcript_delta is only a model event, accessible via raw_model_event.
    """
    sdk_event = SimpleNamespace(
        type="transcript_delta",
        item_id="item_123",
        content_index=0,
        delta="Hello world",
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    # transcript_delta is not emitted as a session event
    assert rpc_event is None


def test_map_turn_started_event():
    """Test that turn_started events are not mapped (SDK doesn't emit these)."""
    sdk_event = SimpleNamespace(type="turn_started")

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    # SDK doesn't emit turn_started, it emits agent_start instead
    assert rpc_event is None


def test_map_turn_ended_event():
    """Test that turn_ended events are not mapped (SDK doesn't emit these)."""
    sdk_event = SimpleNamespace(type="turn_ended")

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    # SDK doesn't emit turn_ended, it emits agent_end instead
    assert rpc_event is None


def test_map_tool_start_event():
    """Test mapping tool_start event."""
    sdk_event = SimpleNamespace(
        type="tool_start",
        tool=SimpleNamespace(name="get_weather"),
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeToolStartEvent)
    assert rpc_event.tool_name == "get_weather"
    # SDK doesn't provide call_id and arguments in tool_start
    assert rpc_event.tool_call_id == ""
    assert rpc_event.arguments == ""


def test_map_tool_end_event():
    """Test mapping tool_end event."""
    sdk_event = SimpleNamespace(
        type="tool_end",
        tool=SimpleNamespace(name="get_weather"),
        output="Sunny, 72°F",  # Output is at event level, not in tool
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeToolEndEvent)
    assert rpc_event.tool_name == "get_weather"
    # SDK doesn't provide call_id in tool_end
    assert rpc_event.tool_call_id == ""
    assert rpc_event.output == "Sunny, 72°F"


def test_map_error_event():
    """Test mapping error event."""

    # Create a proper error object with __str__ method
    class MockError:
        def __init__(self, error_type, message):
            self.type = error_type
            self.message = message

        def __str__(self):
            return self.message

    sdk_event = SimpleNamespace(
        type="error", error=MockError("connection_error", "Failed")
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeErrorEvent)
    assert rpc_event.error_message == "Failed"
    assert rpc_event.error_type == "connection_error"


def test_map_guardrail_tripped_event():
    """Test mapping guardrail_tripped event with real SDK structure."""
    # SDK RealtimeGuardrailTripped has guardrail_results (list) and message
    guardrail_result = SimpleNamespace(
        guardrail=SimpleNamespace(name="content_filter"),
        agent_output="Inappropriate content detected",
    )

    sdk_event = SimpleNamespace(
        type="guardrail_tripped",
        guardrail_results=[guardrail_result],
        message="The agent output was blocked",
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeGuardrailTrippedEvent)
    assert len(rpc_event.guardrail_results) == 1
    assert rpc_event.guardrail_results[0]["guardrail_name"] == "content_filter"
    assert "Inappropriate content" in rpc_event.guardrail_results[0]["agent_output"]
    assert rpc_event.message == "The agent output was blocked"


def test_map_handoff_event():
    """Test mapping handoff event."""
    sdk_event = SimpleNamespace(
        type="handoff",
        from_agent=SimpleNamespace(name="agent_a"),  # SDK passes agent objects
        to_agent=SimpleNamespace(name="agent_b"),
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeHandoffEvent)
    assert rpc_event.from_agent == "agent_a"
    assert rpc_event.to_agent == "agent_b"


def test_map_agent_start_event():
    """Test mapping agent_start event."""
    sdk_event = SimpleNamespace(
        type="agent_start", agent=SimpleNamespace(name="agent_a")
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeAgentStartEvent)
    assert rpc_event.agent_name == "agent_a"


def test_map_agent_end_event():
    """Test mapping agent_end event."""
    sdk_event = SimpleNamespace(type="agent_end", agent=SimpleNamespace(name="agent_a"))

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeAgentEndEvent)
    assert rpc_event.agent_name == "agent_a"


def test_map_history_updated_event():
    """Test mapping history_updated event."""
    item1 = SimpleNamespace(id="1", type="message", role="user", status="completed")
    item2 = SimpleNamespace(
        id="2", type="message", role="assistant", status="completed"
    )

    sdk_event = SimpleNamespace(type="history_updated", history=[item1, item2])

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeHistoryUpdatedEvent)
    assert len(rpc_event.history_items) == 2
    assert rpc_event.history_items[0]["id"] == "1"
    assert rpc_event.history_items[1]["role"] == "assistant"


def test_map_history_added_event():
    """Test mapping history_added event."""
    item = SimpleNamespace(id="1", type="message", role="user", status="completed")

    sdk_event = SimpleNamespace(type="history_added", item=item)

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeHistoryAddedEvent)
    assert rpc_event.item["id"] == "1"
    assert rpc_event.item["role"] == "user"


def test_map_unknown_event_type():
    """Test that unknown event types return None."""
    sdk_event = SimpleNamespace(type="unknown_event_type")

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert rpc_event is None


def test_map_event_with_missing_attributes():
    """Test mapping event with missing optional attributes."""
    sdk_event = SimpleNamespace(type="audio_end")
    # Missing item_id and content_index

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeAudioEndEvent)
    assert rpc_event.item_id == ""
    assert rpc_event.content_index == 0


def test_map_audio_event_with_no_audio_data():
    """Test mapping audio event when audio data is missing."""
    sdk_event = SimpleNamespace(
        type="audio", audio=None, item_id="item_123", content_index=0
    )

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert rpc_event is None


def test_map_raw_model_event():
    """Test mapping raw_model_event."""
    # Create a model event (transcript_delta as example)
    model_event = SimpleNamespace(
        type="transcript_delta",
        item_id="item_123",
        content_index=0,
        delta="Hello",
        response_id="resp_456",
    )

    sdk_event = SimpleNamespace(type="raw_model_event", data=model_event)

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeRawModelEvent)
    assert rpc_event.model_event["type"] == "transcript_delta"
    assert rpc_event.model_event["item_id"] == "item_123"
    assert rpc_event.model_event["delta"] == "Hello"
    assert rpc_event.model_event["response_id"] == "resp_456"


def test_map_raw_model_event_with_error():
    """Test mapping raw_model_event containing an error."""
    model_event = SimpleNamespace(type="error", error=Exception("Connection failed"))

    sdk_event = SimpleNamespace(type="raw_model_event", data=model_event)

    rpc_event = map_realtime_session_event_to_rpc(sdk_event, "run_456")

    assert isinstance(rpc_event, RealtimeRawModelEvent)
    assert rpc_event.model_event["type"] == "error"
    assert "Connection failed" in rpc_event.model_event["error"]
