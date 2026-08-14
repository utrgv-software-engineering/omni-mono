# Realtime Agent Protocol

## Overview

The Realtime Agent Protocol is a JSON-RPC 2.0 based protocol for voice-enabled AI agent interactions over WebSocket connections. This protocol enables low-latency, bidirectional voice communication with AI agents using OpenAI's realtime models.

## Protocol Features

- **Voice Interaction**: Real-time audio input/output with automatic speech recognition and text-to-speech
- **Streaming Events**: Live updates for audio, transcripts, tools, handoffs, and guardrails
- **Session Management**: Create, manage, and terminate voice sessions
- **Audio Storage**: Automatic storage of audio chunks with metadata in database
- **Modality Support**: Audio-only, text-only, or combined audio+text interactions
- **Tool Execution**: Full support for tool calls during voice conversations. This includes client tools: when a realtime session is started with a GUI session id, client tools registered on that GUI session via `variables.client_tools` are bridged in — `safe: true` tools only, since realtime has no approval flow — and their invocations arrive as `client_request {tool.call}` on the GUI `/ws` connection(s), not on `/ws/realtime`.
- **Agent Handoffs**: Seamless transitions between specialized voice agents
- **Guardrails**: Input and output content validation during realtime interactions
- **Interruption**: User can interrupt agent responses mid-speech

## Architecture

The realtime protocol operates separately from the standard text agent protocol:

- **Endpoint**: `/ws/realtime` (separate from `/ws` for text agents)
- **Not Compatible**: Realtime sessions are distinct from text agent sessions
- **Audio Format**: Base64-encoded audio transmitted as JSON strings
- **Metadata Storage**: Audio metadata and transcripts stored in database; audio files on disk
- **Stateful**: Each session maintains conversation state and audio history

## Transport

The protocol uses JSON-RPC 2.0 over WebSocket:
- **Client → Server**: RPC calls for session control and audio/text input
- **Server → Client**: Notifications for streaming events (audio, transcripts, tools, etc.)

### Connection

Connect to the realtime endpoint:
```
ws://server:8000/ws/realtime
```

With optional authentication:
```
ws://server:8000/ws/realtime?token=your-auth-token
```

## RPC Methods

### capabilities()

Probe whether realtime voice is available **without** opening a session. Use
this for boot-time capability detection. Calling `start_session` purely to
probe is a footgun: it creates a trace row and opens an upstream Realtime
WebSocket even when the user never speaks.

**Returns:**
```json
{
  "enabled": true,
  "kind": "realtime",
  "agent_name": "omni"
}
```

`kind` is `"realtime"` for the OpenAI Realtime backend or `"pipeline"` for the
classic STT→LLM→TTS pipeline. If voice is not configured, the `capabilities`
method is simply not registered — clients should treat the resulting
`MethodNotFound` (or transport-level failure) as `enabled: false`.

### start_session(session_id?: string)

Start a new realtime voice session.

**Parameters:**
- `session_id` (string, optional): Custom session ID (generated if not provided)

**Returns:**
```json
{
  "session_id": "session-uuid",
  "run_id": "run-uuid"
}
```

**Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "start_session",
  "params": {
    "session_id": "my-session-123"
  }
}
```

**Notes:**
- Session is immediately connected and ready for audio/text input
- Event streaming begins automatically
- Session configuration (voice, modalities, audio format) comes from agent spec

### send_audio(session_id: string, audio_base64: string)

Send audio input to the agent.

**Parameters:**
- `session_id` (string): Active session ID
- `audio_base64` (string): Base64-encoded audio data in configured format

**Returns:**
```json
true
```

**Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "method": "send_audio",
  "params": {
    "session_id": "session-123",
    "audio_base64": "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA..."
  }
}
```

**Notes:**
- Audio format must match session configuration (e.g., pcm16, g711_ulaw)
- Send audio chunks as they are captured from microphone
- Agent processes audio with automatic turn detection

### send_text(session_id: string, text: string)

Send text input to the agent (text modality must be enabled).

**Parameters:**
- `session_id` (string): Active session ID
- `text` (string): Text message to send

**Returns:**
```json
true
```

**Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "3",
  "method": "send_text",
  "params": {
    "session_id": "session-123",
    "text": "What's the weather like today?"
  }
}
```

**Notes:**
- Only works if session modalities include "text"
- Agent will respond with audio and/or text depending on configuration
- Triggers immediate response generation

### interrupt(session_id: string)

Interrupt the current agent response.

**Parameters:**
- `session_id` (string): Active session ID

**Returns:**
```json
true
```

**Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "4",
  "method": "interrupt",
  "params": {
    "session_id": "session-123"
  }
}
```

**Notes:**
- Cancels any ongoing audio response
- Agent stops speaking immediately
- Safe to call even if agent is not currently responding

### stop_session(session_id: string)

Stop and clean up a realtime session.

**Parameters:**
- `session_id` (string): Session ID to stop

**Returns:**
```json
true
```

**Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "5",
  "method": "stop_session",
  "params": {
    "session_id": "session-123"
  }
}
```

**Notes:**
- Closes realtime connection and stops event streaming
- Session history and audio files are preserved
- Cannot restart a stopped session; create a new one instead

### get_session_info(session_id: string)

Get information about an active session.

**Parameters:**
- `session_id` (string): Session ID to query

**Returns:**
```json
{
  "session_id": "session-123",
  "run_id": "run-456",
  "active": true
}
```

**Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "6",
  "method": "get_session_info",
  "params": {
    "session_id": "session-123"
  }
}
```

### list_sessions()

List all active realtime sessions.

**Returns:**
```json
{
  "sessions": [
    {
      "session_id": "session-123",
      "run_id": "run-456",
      "active": true
    },
    {
      "session_id": "session-789",
      "run_id": "run-012",
      "active": true
    }
  ]
}
```

**Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "7",
  "method": "list_sessions",
  "params": {}
}
```

## Event Notifications

The server streams events during realtime sessions. All events are delivered via `realtime_event` method.

### realtime_event notification wrapper

All realtime events are wrapped in a notification:
```json
{
  "jsonrpc": "2.0",
  "method": "realtime_event",
  "params": {
    "type": "realtime_audio",
    ...
  }
}
```

### Event Types

#### realtime_turn_started

Agent has started processing a new turn.

```json
{
  "type": "realtime_turn_started",
  "run_id": "run-123",
  "timestamp": 1234567890.123
}
```

#### realtime_turn_ended

Agent has finished processing a turn.

```json
{
  "type": "realtime_turn_ended",
  "run_id": "run-123",
  "timestamp": 1234567890.456
}
```

#### realtime_audio

Agent audio output chunk.

```json
{
  "type": "realtime_audio",
  "run_id": "run-123",
  "item_id": "item_abc123",
  "content_index": 0,
  "audio_base64": "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAA...",
  "delta_ms": 320,
  "timestamp": 1234567890.789
}
```

**Notes:**
- Audio is base64-encoded in configured output format
- `delta_ms` indicates duration of this audio chunk
- Audio chunks are automatically stored to disk with metadata in database
- `item_id` and `content_index` identify the audio segment

#### realtime_transcript

Transcription of user input or agent output.

```json
{
  "type": "realtime_transcript",
  "run_id": "run-123",
  "item_id": "item_abc123",
  "content_index": 0,
  "role": "assistant",
  "transcript": "Based on the current weather data, it's sunny and 72 degrees.",
  "timestamp": 1234567890.123
}
```

**Notes:**
- `role` is either "user" (input) or "assistant" (output)
- Transcripts are stored in conversation history
- May arrive before, during, or after corresponding audio

#### realtime_tool_called

Agent is calling a tool.

```json
{
  "type": "realtime_tool_called",
  "run_id": "run-123",
  "call_id": "call_xyz789",
  "tool_name": "get_weather",
  "arguments": "{\"location\": \"San Francisco\"}",
  "timestamp": 1234567890.234
}
```

#### realtime_tool_completed

Tool execution completed successfully.

```json
{
  "type": "realtime_tool_completed",
  "run_id": "run-123",
  "call_id": "call_xyz789",
  "tool_name": "get_weather",
  "result": "{\"temperature\": 72, \"conditions\": \"sunny\"}",
  "timestamp": 1234567890.345
}
```

#### realtime_tool_failed

Tool execution failed.

```json
{
  "type": "realtime_tool_failed",
  "run_id": "run-123",
  "call_id": "call_xyz789",
  "tool_name": "get_weather",
  "error": "API rate limit exceeded",
  "timestamp": 1234567890.456
}
```

#### realtime_handoff_agent_start

New agent is starting (during handoff).

```json
{
  "type": "realtime_handoff_agent_start",
  "run_id": "run-123",
  "agent_name": "WeatherSpecialist",
  "timestamp": 1234567890.567
}
```

#### realtime_handoff

Agent is transferring conversation to another agent.

```json
{
  "type": "realtime_handoff",
  "run_id": "run-123",
  "from_agent": "GeneralAssistant",
  "to_agent": "WeatherSpecialist",
  "context": "User wants detailed weather forecast",
  "timestamp": 1234567890.678
}
```

#### realtime_handoff_agent_end

Previous agent has ended (after handoff).

```json
{
  "type": "realtime_handoff_agent_end",
  "run_id": "run-123",
  "agent_name": "GeneralAssistant",
  "timestamp": 1234567890.789
}
```

#### realtime_guardrail_tripped

Content guardrail was triggered.

```json
{
  "type": "realtime_guardrail_tripped",
  "run_id": "run-123",
  "guardrail_type": "input",
  "guardrail_name": "content_filter",
  "message": "Inappropriate content detected",
  "blocked": true,
  "timestamp": 1234567890.890
}
```

**Notes:**
- `guardrail_type` is either "input" or "output"
- `blocked: true` means content was rejected
- `blocked: false` means guardrail triggered but allowed content with warning

#### realtime_error

Error occurred during session.

```json
{
  "type": "realtime_error",
  "run_id": "run-123",
  "error": "Connection to OpenAI API failed",
  "timestamp": 1234567890.999
}
```

## Audio Storage

The realtime protocol automatically stores audio data:

### Storage Locations

- **Audio Files**: `{base_path}/{project_slug}/{agent_slug}/audio/{session_id}/`
- **Metadata**: SQLite database table `audio_metadata`

### File Naming

Audio chunks are stored as:
```
{session_id}_{item_id}_{content_index}.{format}
```

Example:
```
session-123_item_abc_0.pcm16
```

### Database Schema

Audio metadata table:
```sql
CREATE TABLE audio_metadata (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    content_index INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    audio_format TEXT NOT NULL,
    duration_ms INTEGER,
    project_slug TEXT,
    agent_slug TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Audio Formats

Supported audio formats (configured in agent spec):

- **pcm16**: 16-bit PCM, 24kHz sample rate
- **g711_ulaw**: G.711 μ-law, 8kHz sample rate
- **g711_alaw**: G.711 A-law, 8kHz sample rate

**Note**: Input and output formats can be configured separately.

## Session Configuration

Session behavior is configured via `RealtimeSettings` in the agent spec:

```python
RealtimeSettings(
    model_name="gpt-4o-realtime-preview",
    voice="alloy",  # alloy, echo, fable, onyx, nova, shimmer
    temperature=0.8,
    max_output_tokens=4096,
    modalities=["audio", "text"],
    input_audio_format="pcm16",
    output_audio_format="pcm16",
    turn_detection={
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 500
    },
    input_audio_transcription={
        "model": "whisper-1"
    }
)
```

## Authentication

Same token-based authentication as standard protocol:
```
ws://server:8000/ws/realtime?token=your-auth-token
```

Invalid tokens result in immediate connection close with code 4403.

## Error Handling

### JSON-RPC Errors

Standard JSON-RPC 2.0 error responses:
```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "error": {
    "code": -32603,
    "message": "Session not found",
    "data": "session-123"
  }
}
```

### Common Errors

- **Session not found** (-32603): Invalid or stopped session ID
- **Session already exists** (-32603): Attempting to start duplicate session
- **Not connected** (-32603): Sending audio/text before session connected
- **Invalid audio format** (-32602): Audio data cannot be decoded

### Event Errors

Errors during streaming are delivered via `realtime_error` events, not JSON-RPC errors.

## Implementation Guidelines

### For Servers

1. Create separate `/ws/realtime` endpoint from standard `/ws`
2. Implement all RPC methods listed above
3. Stream events via `realtime_event` notifications
4. Store audio chunks to disk and metadata to database
5. Handle session lifecycle (start, stop, cleanup)
6. Support configured audio formats and modalities

### For Clients

1. Connect to `/ws/realtime` endpoint
2. Handle all event notification types
3. Encode/decode audio in correct format
4. Buffer audio for smooth playback
5. Implement UI for interruption
6. Display transcripts alongside audio

## Example Server Implementation

```python
from omniagents.rpc import JsonRpcEndpoint
from omniagents.rpc.realtime_service import RealtimeService
from omniagents.core.agents.specs import AgentSpec, RealtimeSettings
from fastapi import FastAPI

# Create agent spec with realtime mode
spec = AgentSpec(
    name="VoiceAssistant",
    realtime_mode=True,
    realtime_settings=RealtimeSettings(
        model_name="gpt-4o-realtime-preview",
        voice="alloy",
        modalities=["audio"],
        input_audio_format="pcm16",
        output_audio_format="pcm16"
    ),
    # ... other spec fields
)

# Build settings
settings = {
    "openai_api_key": "sk-..."
}

# Create realtime service
realtime_service = RealtimeService(spec, settings)
realtime_endpoint = JsonRpcEndpoint(realtime_service)

# Register with FastAPI
app = FastAPI()
realtime_endpoint.register_route(app, "/ws/realtime")
```

## Example Client Implementation

```python
import asyncio
import base64
from omniagents.rpc import JsonRpcClient

async def voice_chat():
    # Connect to realtime endpoint
    client = JsonRpcClient("ws://localhost:8000/ws/realtime")
    await client.connect()

    # Start session
    result = await client.call("start_session")
    session_id = result["session_id"]

    # Set up event handler
    async def handle_events():
        async for notification in client.notifications():
            if notification["method"] == "realtime_event":
                event = notification["params"]

                if event["type"] == "realtime_audio":
                    # Decode and play audio
                    audio_bytes = base64.b64decode(event["audio_base64"])
                    await play_audio(audio_bytes)

                elif event["type"] == "realtime_transcript":
                    # Display transcript
                    print(f"{event['role']}: {event['transcript']}")

                elif event["type"] == "realtime_tool_called":
                    print(f"Tool: {event['tool_name']}")

    # Start event handler
    event_task = asyncio.create_task(handle_events())

    # Send audio from microphone
    async for audio_chunk in capture_microphone():
        audio_base64 = base64.b64encode(audio_chunk).decode()
        await client.call("send_audio", {
            "session_id": session_id,
            "audio_base64": audio_base64
        })

    # Or send text
    await client.call("send_text", {
        "session_id": session_id,
        "text": "What's the weather?"
    })

    # Interrupt agent
    await client.call("interrupt", {"session_id": session_id})

    # Stop session when done
    await client.call("stop_session", {"session_id": session_id})
    event_task.cancel()
```

## Comparison with Standard Protocol

| Feature | Standard Protocol | Realtime Protocol |
|---------|------------------|-------------------|
| Endpoint | `/ws` | `/ws/realtime` |
| Modality | Text only | Audio, text, or both |
| Session Type | Conversation messages | Voice session |
| Audio Support | None | Full bidirectional |
| Latency | Higher (batch) | Lower (streaming) |
| Tools | Supported | Supported |
| Handoffs | Supported | Supported |
| Guardrails | Supported | Supported |
| Storage | Messages in DB | Audio files + metadata |
| Interruption | N/A | Supported |

## Best Practices

### Audio Handling

- **Chunk Size**: Send audio in 100-200ms chunks for optimal latency
- **Buffering**: Buffer 500-1000ms of audio for smooth playback
- **Format**: Use pcm16 for best quality, g711 for lower bandwidth

### Session Management

- **One Session Per User**: Don't share sessions across users
- **Cleanup**: Always call `stop_session` when done
- **Timeout**: Implement client-side timeout for inactive sessions

### Error Recovery

- **Reconnect**: Implement exponential backoff for reconnection
- **State**: Track session state client-side (active, stopped, error)
- **Graceful Degradation**: Fall back to text if audio fails

### Performance

- **WebSocket**: Keep connection alive with ping/pong
- **Compression**: Consider enabling WebSocket compression
- **CDN**: Serve audio files via CDN for playback if needed

This protocol enables seamless voice interactions with AI agents while maintaining full support for tools, handoffs, and guardrails.
