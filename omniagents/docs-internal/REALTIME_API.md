# Realtime Agent API Reference

This document provides a comprehensive reference for the Python API for building and deploying realtime voice agents.

## Table of Contents

- [Configuration](#configuration)
- [Server-Side Classes](#server-side-classes)
- [Event Types](#event-types)
- [Audio Storage](#audio-storage)
- [Complete Example](#complete-example)

## Configuration

### RealtimeSettings

Configure realtime agent behavior via the `RealtimeSettings` dataclass.

```python
from omniagents.core.agents.specs import RealtimeSettings

settings = RealtimeSettings(
    model_name="gpt-4o-realtime-preview",
    voice="alloy",
    temperature=0.8,
    max_output_tokens=4096,
    modalities=["audio", "text"],
    input_audio_format="pcm16",
    output_audio_format="pcm16",
    turn_detection=None,
    input_audio_transcription=None
)
```

#### Parameters

- **model_name** (str): OpenAI realtime model ID
  - Default: `"gpt-4o-realtime-preview"`

- **voice** (str): Voice for text-to-speech
  - Options: `"alloy"`, `"echo"`, `"fable"`, `"onyx"`, `"nova"`, `"shimmer"`
  - Default: `"alloy"`

- **temperature** (float): Sampling temperature (0.0 - 1.0)
  - Default: `0.8`

- **max_output_tokens** (int): Maximum tokens per response
  - Default: `4096`

- **modalities** (Optional[List[str]]): Communication modalities
  - Options: `["audio"]`, `["text"]`, `["audio", "text"]`
  - Default: `None` (uses model default)

- **input_audio_format** (Optional[str]): Input audio encoding
  - Options: `"pcm16"`, `"g711_ulaw"`, `"g711_alaw"`
  - Default: `None` (uses model default)

- **output_audio_format** (Optional[str]): Output audio encoding
  - Options: `"pcm16"`, `"g711_ulaw"`, `"g711_alaw"`
  - Default: `None` (uses model default)

- **turn_detection** (Optional[Dict[str, Any]]): Voice activity detection config
  - Default: `None` (uses model default)
  - Example:
    ```python
    {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 500
    }
    ```

- **input_audio_transcription** (Optional[Dict[str, Any]]): Transcription config
  - Default: `None` (no transcription)
  - Example:
    ```python
    {
        "model": "whisper-1"
    }
    ```

### AgentSpec with Realtime Mode

Enable realtime mode in your agent spec:

```python
from omniagents.core.agents.specs import AgentSpec, RealtimeSettings

spec = AgentSpec(
    name="VoiceAssistant",
    header_title="Voice Assistant",
    page_title="Voice Chat",
    welcome_text="Start speaking to chat with me!",
    client_storage_prefix="voice_assistant",
    get_agent_instructions=lambda: "You are a helpful voice assistant.",
    build_agent=None,  # Not used for realtime
    model_name="gpt-4o-realtime-preview",
    realtime_mode=True,  # Enable realtime mode
    realtime_settings=RealtimeSettings(
        voice="alloy",
        modalities=["audio"],
        input_audio_format="pcm16",
        output_audio_format="pcm16"
    ),
    # Tools (optional)
    available_tools=[...],
    # Handoffs (optional)
    handoff_configs=[...],
    # Guardrails (optional)
    input_guardrails=[...],
    output_guardrails=[...]
)
```

## Server-Side Classes

### RealtimeSession

Manages a single realtime agent session.

#### Constructor

```python
from omniagents.rpc.realtime_session import RealtimeSession

session = RealtimeSession(
    spec=agent_spec,
    settings={"openai_api_key": "sk-..."},
    session_id="optional-session-id",
    audio_storage=None  # Optional custom AudioStorageBackend
)
```

**Parameters:**
- `spec` (AgentSpec): Agent spec with `realtime_mode=True`
- `settings` (Optional[Dict[str, Any]]): API keys and settings
- `session_id` (Optional[str]): Session ID (generated if not provided)
- `audio_storage` (Optional[AudioStorageBackend]): Custom audio storage

#### Methods

##### start()

Start the realtime session.

```python
run_id = await session.start()
```

**Returns:** `str` - The run ID for this session

**Raises:**
- `RuntimeError` - If session already active
- `ImportError` - If OpenAI agents SDK not installed
- `ValueError` - If API key not provided

##### connect()

Connect to the realtime API.

```python
realtime_session = await session.connect()
```

**Returns:** OpenAI `RealtimeSession` object

**Raises:**
- `RuntimeError` - If session not started

**Note:** Must call `start()` before `connect()`.

##### stream_events()

Stream realtime events from the agent.

```python
async for event in session.stream_events():
    print(f"Event: {event.type}")
    if event.type == "realtime_audio":
        audio_data = base64.b64decode(event.audio_base64)
```

**Yields:** `RealtimeEventBase` - RPC event objects

**Raises:**
- `RuntimeError` - If session not connected

##### send_audio(audio_base64: str)

Send audio input to the agent.

```python
import base64

audio_bytes = record_from_microphone()
audio_base64 = base64.b64encode(audio_bytes).decode()
await session.send_audio(audio_base64)
```

**Parameters:**
- `audio_base64` (str): Base64-encoded audio data

**Raises:**
- `RuntimeError` - If session not connected

##### send_text(text: str)

Send text input to the agent.

```python
await session.send_text("What's the weather today?")
```

**Parameters:**
- `text` (str): Text message to send

**Raises:**
- `RuntimeError` - If session not connected

**Note:** Only works if modalities include "text".

##### interrupt()

Interrupt the current agent response.

```python
await session.interrupt()
```

**Raises:**
- `RuntimeError` - If session not connected

##### stop()

Stop the session and clean up resources.

```python
await session.stop()
```

**Note:** Safe to call multiple times.

##### managed() (Context Manager)

Automatic lifecycle management:

```python
async with session.managed():
    await session.connect()
    async for event in session.stream_events():
        # Process events
        pass
# Session automatically stopped on exit
```

#### Properties

- `session_id` (str): Unique session identifier
- `spec` (AgentSpec): Agent specification
- `settings` (Dict[str, Any]): Settings dictionary
- `audio_storage` (AudioStorageBackend): Audio storage instance

### RealtimeService

JSON-RPC service for managing multiple realtime sessions.

#### Constructor

```python
from omniagents.rpc.realtime_service import RealtimeService

service = RealtimeService(
    spec=agent_spec,
    settings={"openai_api_key": "sk-..."}
)
```

**Parameters:**
- `spec` (AgentSpec): Agent spec with `realtime_mode=True`
- `settings` (Optional[Dict[str, Any]]): API keys and settings

**Raises:**
- `ValueError` - If spec does not have `realtime_mode=True`

#### Methods

##### start_session(session_id: Optional[str])

Start a new realtime session.

```python
result = await service.start_session("my-session-123")
# result: {"session_id": "my-session-123", "run_id": "run-uuid"}
```

**Parameters:**
- `session_id` (Optional[str]): Custom session ID

**Returns:** `Dict[str, Any]` with `session_id` and `run_id`

**Raises:**
- `RuntimeError` - If session already exists

##### send_audio(session_id: str, audio_base64: str)

Send audio to a session.

```python
await service.send_audio("session-123", audio_base64)
```

**Parameters:**
- `session_id` (str): Target session ID
- `audio_base64` (str): Base64-encoded audio

**Returns:** `bool` - True on success

**Raises:**
- `ValueError` - If session not found

##### send_text(session_id: str, text: str)

Send text to a session.

```python
await service.send_text("session-123", "Hello!")
```

**Parameters:**
- `session_id` (str): Target session ID
- `text` (str): Text message

**Returns:** `bool` - True on success

**Raises:**
- `ValueError` - If session not found

##### interrupt(session_id: str)

Interrupt agent response.

```python
await service.interrupt("session-123")
```

**Parameters:**
- `session_id` (str): Target session ID

**Returns:** `bool` - True on success

**Raises:**
- `ValueError` - If session not found

##### stop_session(session_id: str)

Stop a session.

```python
await service.stop_session("session-123")
```

**Parameters:**
- `session_id` (str): Session to stop

**Returns:** `bool` - True on success

**Raises:**
- `ValueError` - If session not found

##### get_session_info(session_id: str)

Get session information.

```python
info = await service.get_session_info("session-123")
# info: {"session_id": "...", "run_id": "...", "active": true}
```

**Parameters:**
- `session_id` (str): Target session ID

**Returns:** `Dict[str, Any]` with session details

**Raises:**
- `ValueError` - If session not found

##### list_sessions()

List all active sessions.

```python
result = await service.list_sessions()
# result: {"sessions": [{"session_id": "...", ...}, ...]}
```

**Returns:** `Dict[str, Any]` with list of sessions

##### cleanup()

Clean up all active sessions.

```python
await service.cleanup()
```

**Note:** Called automatically on service shutdown.

## Event Types

All events inherit from `RealtimeEventBase` and include a `type` field for discrimination.

### RealtimeTurnStartedEvent

```python
@dataclass
class RealtimeTurnStartedEvent(RealtimeEventBase):
    type: str = field(default="realtime_turn_started", init=False)
    run_id: str = ""
    timestamp: float = 0.0
```

### RealtimeTurnEndedEvent

```python
@dataclass
class RealtimeTurnEndedEvent(RealtimeEventBase):
    type: str = field(default="realtime_turn_ended", init=False)
    run_id: str = ""
    timestamp: float = 0.0
```

### RealtimeAudioEvent

```python
@dataclass
class RealtimeAudioEvent(RealtimeEventBase):
    type: str = field(default="realtime_audio", init=False)
    run_id: str = ""
    item_id: str = ""
    content_index: int = 0
    audio_base64: str = ""
    delta_ms: int = 0
    timestamp: float = 0.0
```

**Note:** Audio is automatically stored to disk when this event is streamed.

### RealtimeTranscriptEvent

```python
@dataclass
class RealtimeTranscriptEvent(RealtimeEventBase):
    type: str = field(default="realtime_transcript", init=False)
    run_id: str = ""
    item_id: str = ""
    content_index: int = 0
    role: str = ""  # "user" or "assistant"
    transcript: str = ""
    timestamp: float = 0.0
```

### RealtimeToolCalledEvent

```python
@dataclass
class RealtimeToolCalledEvent(RealtimeEventBase):
    type: str = field(default="realtime_tool_called", init=False)
    run_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    arguments: str = ""  # JSON string
    timestamp: float = 0.0
```

### RealtimeToolCompletedEvent

```python
@dataclass
class RealtimeToolCompletedEvent(RealtimeEventBase):
    type: str = field(default="realtime_tool_completed", init=False)
    run_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    result: str = ""  # JSON string
    timestamp: float = 0.0
```

### RealtimeToolFailedEvent

```python
@dataclass
class RealtimeToolFailedEvent(RealtimeEventBase):
    type: str = field(default="realtime_tool_failed", init=False)
    run_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    error: str = ""
    timestamp: float = 0.0
```

### RealtimeHandoffAgentStartEvent

```python
@dataclass
class RealtimeHandoffAgentStartEvent(RealtimeEventBase):
    type: str = field(default="realtime_handoff_agent_start", init=False)
    run_id: str = ""
    agent_name: str = ""
    timestamp: float = 0.0
```

### RealtimeHandoffEvent

```python
@dataclass
class RealtimeHandoffEvent(RealtimeEventBase):
    type: str = field(default="realtime_handoff", init=False)
    run_id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    context: str = ""
    timestamp: float = 0.0
```

### RealtimeHandoffAgentEndEvent

```python
@dataclass
class RealtimeHandoffAgentEndEvent(RealtimeEventBase):
    type: str = field(default="realtime_handoff_agent_end", init=False)
    run_id: str = ""
    agent_name: str = ""
    timestamp: float = 0.0
```

### RealtimeGuardrailTrippedEvent

```python
@dataclass
class RealtimeGuardrailTrippedEvent(RealtimeEventBase):
    type: str = field(default="realtime_guardrail_tripped", init=False)
    run_id: str = ""
    guardrail_type: str = ""  # "input" or "output"
    guardrail_name: str = ""
    message: str = ""
    blocked: bool = False
    timestamp: float = 0.0
```

### RealtimeErrorEvent

```python
@dataclass
class RealtimeErrorEvent(RealtimeEventBase):
    type: str = field(default="realtime_error", init=False)
    run_id: str = ""
    error: str = ""
    timestamp: float = 0.0
```

## Audio Storage

### LocalAudioStorage

Handles audio file storage and retrieval.

```python
from omniagents.core.session.audio_storage import LocalAudioStorage

storage = LocalAudioStorage(base_path="/path/to/audio")
```

#### Methods

##### save_audio_chunk()

```python
relative_path = await storage.save_audio_chunk(
    session_id="session-123",
    item_id="item_abc",
    content_index=0,
    audio_data=audio_bytes,
    audio_format="pcm16"
)
# Returns: "session-123/session-123_item_abc_0.pcm16"
```

##### get_audio_path()

```python
abs_path = await storage.get_audio_path(
    session_id="session-123",
    item_id="item_abc",
    content_index=0,
    audio_format="pcm16"
)
# Returns absolute path to audio file
```

### get_audio_storage_path()

Helper to get storage path for agent:

```python
from omniagents.core.paths import get_audio_storage_path

path = get_audio_storage_path(
    project_slug="my_project",
    agent_slug="voice_assistant"
)
# Returns: Path("{base}/my_project/voice_assistant/audio")
```

## Complete Example

### Server with FastAPI

```python
import os
from fastapi import FastAPI
from omniagents.core.agents.specs import AgentSpec, RealtimeSettings
from omniagents.rpc import JsonRpcEndpoint
from omniagents.rpc.realtime_service import RealtimeService

# Define agent spec
spec = AgentSpec(
    name="VoiceWeatherBot",
    header_title="Weather Voice Bot",
    page_title="Voice Weather",
    welcome_text="Ask me about the weather!",
    client_storage_prefix="weather_voice",
    get_agent_instructions=lambda: (
        "You are a helpful weather assistant. "
        "Provide weather information in a friendly, conversational tone."
    ),
    build_agent=None,
    model_name="gpt-4o-realtime-preview",
    realtime_mode=True,
    realtime_settings=RealtimeSettings(
        voice="alloy",
        temperature=0.8,
        modalities=["audio", "text"],
        input_audio_format="pcm16",
        output_audio_format="pcm16",
        turn_detection={
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 500
        },
        input_audio_transcription={
            "model": "whisper-1"
        }
    ),
    available_tools=[get_weather_tool],  # Your tools
    project_slug="weather_project",
    agent_slug="voice_weather"
)

# Build settings
settings = {
    "openai_api_key": os.getenv("OPENAI_API_KEY")
}

# Create service
realtime_service = RealtimeService(spec, settings)
realtime_endpoint = JsonRpcEndpoint(realtime_service)

# Create FastAPI app
app = FastAPI()
realtime_endpoint.register_route(app, "/ws/realtime")

# Run with: uvicorn myapp:app --host 0.0.0.0 --port 8000
```

### Client Example

```python
import asyncio
import base64
from omniagents.rpc import JsonRpcClient

async def voice_chat():
    # Connect
    client = JsonRpcClient("ws://localhost:8000/ws/realtime")
    await client.connect()

    # Start session
    result = await client.call("start_session")
    session_id = result["session_id"]
    print(f"Session started: {session_id}")

    # Handle events
    async def handle_events():
        async for notification in client.notifications():
            if notification["method"] == "realtime_event":
                event = notification["params"]
                event_type = event["type"]

                if event_type == "realtime_audio":
                    # Play audio
                    audio_bytes = base64.b64decode(event["audio_base64"])
                    await play_audio(audio_bytes)

                elif event_type == "realtime_transcript":
                    # Show transcript
                    role = event["role"]
                    text = event["transcript"]
                    print(f"{role.upper()}: {text}")

                elif event_type == "realtime_tool_called":
                    print(f"Calling tool: {event['tool_name']}")

                elif event_type == "realtime_error":
                    print(f"ERROR: {event['error']}")

    # Start event handler in background
    event_task = asyncio.create_task(handle_events())

    try:
        # Send audio from microphone
        async for audio_chunk in capture_microphone():
            audio_base64 = base64.b64encode(audio_chunk).decode()
            await client.call("send_audio", {
                "session_id": session_id,
                "audio_base64": audio_base64
            })

    except KeyboardInterrupt:
        print("\nInterrupting...")
        await client.call("interrupt", {"session_id": session_id})

    finally:
        # Stop session
        await client.call("stop_session", {"session_id": session_id})
        event_task.cancel()
        await client.disconnect()

# Run
asyncio.run(voice_chat())
```

### Session Management Example

```python
from omniagents.rpc.realtime_session import RealtimeSession

async def managed_session_example():
    session = RealtimeSession(
        spec=agent_spec,
        settings={"openai_api_key": "sk-..."}
    )

    # Use context manager for automatic cleanup
    async with session.managed():
        await session.connect()

        # Send initial text
        await session.send_text("Hello, what's the weather?")

        # Stream events
        event_count = 0
        async for event in session.stream_events():
            print(f"Event {event_count}: {event.type}")
            event_count += 1

            # Stop after 100 events
            if event_count >= 100:
                break

    # Session automatically stopped
    print("Session ended")

asyncio.run(managed_session_example())
```

## See Also

- [Realtime Protocol Documentation](../omniagents/rpc/realtime_protocol.md)
- [Standard Agent Protocol](../omniagents/rpc/protocol.md)
- [Agent Specifications](OMNIAGENTS.md)
