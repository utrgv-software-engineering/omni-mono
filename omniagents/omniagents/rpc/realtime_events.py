"""Realtime event types for JSON-RPC protocol.

This module defines event types for communicating realtime agent events
between server and client over the RPC protocol.
"""

from dataclasses import dataclass, asdict, field
from typing import Any, Optional, Dict, List
import base64


@dataclass
class RealtimeEventBase:
    """Base class for all realtime RPC events."""

    run_id: str
    type: str = field(default="", init=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class RealtimeAudioEvent(RealtimeEventBase):
    """Audio chunk event from realtime agent."""

    audio_base64: str
    item_id: str
    content_index: int
    delta_ms: Optional[int] = None

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_audio"


@dataclass
class RealtimeAudioEndEvent(RealtimeEventBase):
    """Audio generation complete event."""

    item_id: str
    content_index: int

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_audio_end"


@dataclass
class RealtimeAudioInterruptedEvent(RealtimeEventBase):
    """Audio playback interrupted event."""

    item_id: str
    content_index: int

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_audio_interrupted"


@dataclass
class RealtimeTranscriptDeltaEvent(RealtimeEventBase):
    """Incremental transcript update event."""

    item_id: str
    content_index: int
    delta: str

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_transcript_delta"


@dataclass
class RealtimeTurnStartedEvent(RealtimeEventBase):
    """Agent turn started event."""

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_turn_started"


@dataclass
class RealtimeTurnEndedEvent(RealtimeEventBase):
    """Agent turn ended event."""

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_turn_ended"


@dataclass
class RealtimeToolStartEvent(RealtimeEventBase):
    """Tool invocation started event."""

    tool_name: str
    tool_call_id: str
    arguments: str

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_tool_start"


@dataclass
class RealtimeToolEndEvent(RealtimeEventBase):
    """Tool execution completed event."""

    tool_name: str
    tool_call_id: str
    output: str

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_tool_end"


@dataclass
class RealtimeErrorEvent(RealtimeEventBase):
    """Error occurred during realtime session."""

    error_message: str
    error_type: Optional[str] = None

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_error"


@dataclass
class RealtimeGuardrailTrippedEvent(RealtimeEventBase):
    """Guardrail triggered interruption event."""

    guardrail_results: List[Dict[str, Any]]
    message: str

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_guardrail_tripped"


@dataclass
class RealtimeHandoffEvent(RealtimeEventBase):
    """Agent handoff event."""

    from_agent: str
    to_agent: str

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_handoff"


@dataclass
class RealtimeAgentStartEvent(RealtimeEventBase):
    """Agent started event."""

    agent_name: str

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_agent_start"


@dataclass
class RealtimeAgentEndEvent(RealtimeEventBase):
    """Agent ended event."""

    agent_name: str

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_agent_end"


@dataclass
class RealtimeHistoryUpdatedEvent(RealtimeEventBase):
    """Full history snapshot event."""

    history_items: List[Dict[str, Any]]

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_history_updated"


@dataclass
class RealtimeHistoryAddedEvent(RealtimeEventBase):
    """New item added to history."""

    item: Dict[str, Any]

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_history_added"


@dataclass
class RealtimeRawModelEvent(RealtimeEventBase):
    """Raw model event (forwards all model-layer events)."""

    model_event: Dict[str, Any]

    def __post_init__(self):
        """Ensure type is set correctly."""
        self.type = "realtime_raw_model_event"


def map_realtime_session_event_to_rpc(
    event: Any, run_id: str
) -> Optional[RealtimeEventBase]:
    """Map OpenAI SDK RealtimeSessionEvent to RPC event.

    Args:
        event: RealtimeSessionEvent from OpenAI SDK
        run_id: The current run ID

    Returns:
        RPC event object or None if event type is not mapped
    """
    event_type = getattr(event, "type", None)

    if event_type == "audio":
        # Extract audio data from RealtimeAudio.audio (RealtimeModelAudioEvent)
        audio_obj = getattr(event, "audio", None)
        if audio_obj is None:
            return None

        # Get the raw audio bytes from audio.data
        audio_data = getattr(audio_obj, "data", b"")

        # Encode bytes to base64
        if isinstance(audio_data, bytes):
            audio_base64 = base64.b64encode(audio_data).decode("utf-8")
        else:
            # Already a string, assume base64
            audio_base64 = audio_data

        return RealtimeAudioEvent(
            run_id=run_id,
            audio_base64=audio_base64,
            item_id=getattr(event, "item_id", ""),
            content_index=getattr(event, "content_index", 0),
            delta_ms=None,  # Not provided in SDK events
        )

    elif event_type == "audio_end":
        return RealtimeAudioEndEvent(
            run_id=run_id,
            item_id=getattr(event, "item_id", ""),
            content_index=getattr(event, "content_index", 0),
        )

    elif event_type == "audio_interrupted":
        return RealtimeAudioInterruptedEvent(
            run_id=run_id,
            item_id=getattr(event, "item_id", ""),
            content_index=getattr(event, "content_index", 0),
        )

    # NOTE: transcript_delta is NOT emitted as a session event
    # It's only a model event, accessible via raw_model_event
    # elif event_type == "transcript_delta":

    elif event_type == "agent_start":
        # RealtimeAgentStartEvent has agent
        agent_obj = getattr(event, "agent", None)
        return RealtimeAgentStartEvent(
            run_id=run_id,
            agent_name=(
                getattr(agent_obj, "name", "unknown") if agent_obj else "unknown"
            ),
        )

    elif event_type == "agent_end":
        # RealtimeAgentEndEvent has agent
        agent_obj = getattr(event, "agent", None)
        return RealtimeAgentEndEvent(
            run_id=run_id,
            agent_name=(
                getattr(agent_obj, "name", "unknown") if agent_obj else "unknown"
            ),
        )

    elif event_type == "tool_start":
        # RealtimeToolStart has tool (Tool object) and agent
        tool_obj = getattr(event, "tool", None)
        return RealtimeToolStartEvent(
            run_id=run_id,
            tool_name=getattr(tool_obj, "name", "unknown") if tool_obj else "unknown",
            tool_call_id="",  # Not provided in this event
            arguments="",  # Not provided in this event
        )

    elif event_type == "tool_end":
        # RealtimeToolEnd has tool, output, agent
        tool_obj = getattr(event, "tool", None)
        return RealtimeToolEndEvent(
            run_id=run_id,
            tool_name=getattr(tool_obj, "name", "unknown") if tool_obj else "unknown",
            tool_call_id="",  # Not provided in this event
            output=str(getattr(event, "output", "")),
        )

    elif event_type == "error":
        error_obj = getattr(event, "error", None)
        return RealtimeErrorEvent(
            run_id=run_id,
            error_message=str(error_obj) if error_obj else "Unknown error",
            error_type=getattr(error_obj, "type", None) if error_obj else None,
        )

    elif event_type == "guardrail_tripped":
        # SDK RealtimeGuardrailTripped has guardrail_results (list) and message
        guardrail_results = getattr(event, "guardrail_results", [])
        message = getattr(event, "message", "")

        # Convert guardrail_results to dicts
        results_dicts = []
        for result in guardrail_results:
            guardrail_obj = getattr(result, "guardrail", None)
            result_dict = {
                "guardrail_name": (
                    getattr(guardrail_obj, "name", "unknown")
                    if guardrail_obj
                    else "unknown"
                ),
                "agent_output": str(getattr(result, "agent_output", "")),
            }
            results_dicts.append(result_dict)

        return RealtimeGuardrailTrippedEvent(
            run_id=run_id,
            guardrail_results=results_dicts,
            message=message,
        )

    elif event_type == "handoff":
        # RealtimeHandoffEvent has from_agent and to_agent (RealtimeAgent objects)
        from_agent_obj = getattr(event, "from_agent", None)
        to_agent_obj = getattr(event, "to_agent", None)
        return RealtimeHandoffEvent(
            run_id=run_id,
            from_agent=(
                getattr(from_agent_obj, "name", "unknown")
                if from_agent_obj
                else "unknown"
            ),
            to_agent=(
                getattr(to_agent_obj, "name", "unknown") if to_agent_obj else "unknown"
            ),
        )

    # Note: "agent_start" and "agent_end" are already handled above
    # as they map to RealtimeTurnStartedEvent and RealtimeTurnEndedEvent

    elif event_type == "history_updated":
        # Convert history items to dictionaries
        history = getattr(event, "history", [])
        history_items = []
        for item in history:
            # Extract relevant fields from RealtimeItem
            item_dict = {
                "id": getattr(item, "id", None),
                "type": getattr(item, "type", None),
                "role": getattr(item, "role", None),
                "status": getattr(item, "status", None),
            }
            history_items.append(item_dict)

        return RealtimeHistoryUpdatedEvent(run_id=run_id, history_items=history_items)

    elif event_type == "history_added":
        item_obj = getattr(event, "item", None)
        if item_obj:
            item_dict = {
                "id": getattr(item_obj, "id", None),
                "type": getattr(item_obj, "type", None),
                "role": getattr(item_obj, "role", None),
                "status": getattr(item_obj, "status", None),
            }
            return RealtimeHistoryAddedEvent(run_id=run_id, item=item_dict)

    elif event_type == "raw_model_event":
        # RealtimeRawModelEvent contains a data field with the model event
        data = getattr(event, "data", None)
        if data:
            # Convert model event to dict
            model_event_dict = {
                "type": getattr(data, "type", "unknown"),
            }

            # Add type-specific fields
            if hasattr(data, "item_id"):
                model_event_dict["item_id"] = data.item_id
            if hasattr(data, "content_index"):
                model_event_dict["content_index"] = data.content_index
            if hasattr(data, "delta"):
                model_event_dict["delta"] = data.delta
            if hasattr(data, "transcript"):
                model_event_dict["transcript"] = data.transcript
            if hasattr(data, "response_id"):
                model_event_dict["response_id"] = data.response_id
            if hasattr(data, "name"):
                model_event_dict["name"] = data.name
            if hasattr(data, "call_id"):
                model_event_dict["call_id"] = data.call_id
            if hasattr(data, "arguments"):
                model_event_dict["arguments"] = data.arguments
            if hasattr(data, "error"):
                model_event_dict["error"] = str(data.error)
            if hasattr(data, "status"):
                model_event_dict["status"] = data.status

            return RealtimeRawModelEvent(run_id=run_id, model_event=model_event_dict)

    elif event_type == "input_audio_timeout_triggered":
        # SDK emits this as a session event
        return None  # We can add support for this later if needed

    # Unknown event type - return None
    return None
