# Realtime SDK to RPC Event Mapping

This document describes how OpenAI `agents` SDK realtime events are mapped to our JSON-RPC protocol events.

## Important API Constraints

### Modalities Restriction ⚠️

**CRITICAL**: OpenAI's realtime API only supports ONE modality at a time:
- ✅ `modalities: ["text"]` - Text-only mode
- ✅ `modalities: ["audio"]` - Audio-only mode
- ❌ `modalities: ["audio", "text"]` - **NOT SUPPORTED** (will error)

**Error if you try both:**
```
RealtimeError(message="Invalid modalities: ['audio', 'text'].
Supported combinations are: ['text'] and ['audio'].", ...)
```

**Choose based on your use case:**
- Use `["text"]` for testing with text input/output
- Use `["audio"]` for full voice interaction with microphone/speaker

## SDK Event Types (Session-Level)

The SDK emits these event types when iterating over a `RealtimeSession`:

| SDK Event Type | RPC Event Type | Notes |
|---|---|---|
| `agent_start` | `realtime_agent_start` | Maps model `turn_started` → session `agent_start` |
| `agent_end` | `realtime_agent_end` | Maps model `turn_ended` → session `agent_end` |
| `audio` | `realtime_audio` | Contains `audio.data` (bytes) |
| `audio_end` | `realtime_audio_end` | Signals end of audio generation |
| `audio_interrupted` | `realtime_audio_interrupted` | User interrupted audio playback |
| `tool_start` | `realtime_tool_start` | Tool invocation beginning |
| `tool_end` | `realtime_tool_end` | Tool execution completed |
| `error` | `realtime_error` | Error during session |
| `guardrail_tripped` | `realtime_guardrail_tripped` | Output guardrail triggered |
| `handoff` | `realtime_handoff` | Agent handoff |
| `history_updated` | `realtime_history_updated` | Full history snapshot |
| `history_added` | `realtime_history_added` | New history item |
| `raw_model_event` | `realtime_raw_model_event` | Forwards all model-layer events |
| `input_audio_timeout_triggered` | (not mapped) | Can be added if needed |

## Events NOT Emitted as Session Events

These are **model-layer events only**, accessible via `raw_model_event`:

- `transcript_delta` - Incremental transcript updates
- `turn_started` - Model started processing (mapped to `agent_start`)
- `turn_ended` - Model finished processing (mapped to `agent_end`)
- `audio_done` - Model completed audio (mapped to `audio_end`)
- `function_call` - Handled internally, emits `tool_start`/`tool_end`
- `item_updated` - Handled internally, triggers `history_updated`/`history_added`
- `connection_status` - Internal connection state

## Key SDK Structure Details

### Audio Events

```python
# SDK RealtimeAudio structure
event.type == "audio"
event.audio  # RealtimeModelAudioEvent
event.audio.data  # bytes (NOT event.audio.audio!)
event.item_id  # str
event.content_index  # int
```

### Agent Events

```python
# SDK RealtimeAgentStartEvent structure
event.type == "agent_start"
event.agent  # RealtimeAgent object
event.agent.name  # str

# SDK RealtimeAgentEndEvent structure
event.type == "agent_end"
event.agent  # RealtimeAgent object
event.agent.name  # str
```

### Guardrail Events

```python
# SDK RealtimeGuardrailTripped structure (IMPORTANT!)
event.type == "guardrail_tripped"
event.guardrail_results  # list[OutputGuardrailResult]
event.message  # str - description of what was blocked

# Each OutputGuardrailResult has:
result.guardrail  # OutputGuardrail with .name attribute
result.agent_output  # Any - the blocked content
result.agent  # Agent object
result.output  # GuardrailFunctionOutput
```

### Tool Events

```python
# SDK RealtimeToolStart structure
event.type == "tool_start"
event.tool  # Tool object
event.tool.name  # str
event.agent  # RealtimeAgent object

# SDK RealtimeToolEnd structure
event.type == "tool_end"
event.tool  # Tool object
event.tool.name  # str
event.output  # Any - tool result (at event level, not in tool!)
event.agent  # RealtimeAgent object
```

### Raw Model Events

```python
# SDK RealtimeRawModelEvent structure
event.type == "raw_model_event"
event.data  # RealtimeModelEvent (any model event)

# Example: transcript_delta in raw_model_event
event.data.type == "transcript_delta"
event.data.item_id  # str
event.data.delta  # str
event.data.response_id  # str
```

## RPC Event Structures

### realtime_audio

```json
{
  "type": "realtime_audio",
  "run_id": "run_123",
  "audio_base64": "dGVzdA==",
  "item_id": "item_456",
  "content_index": 0,
  "delta_ms": null
}
```

### realtime_agent_start / realtime_agent_end

```json
{
  "type": "realtime_agent_start",
  "run_id": "run_123",
  "agent_name": "MyAgent"
}
```

### realtime_guardrail_tripped

```json
{
  "type": "realtime_guardrail_tripped",
  "run_id": "run_123",
  "guardrail_results": [
    {
      "guardrail_name": "content_filter",
      "agent_output": "blocked content here"
    }
  ],
  "message": "Output was blocked by guardrail"
}
```

### realtime_raw_model_event

```json
{
  "type": "realtime_raw_model_event",
  "run_id": "run_123",
  "model_event": {
    "type": "transcript_delta",
    "item_id": "item_456",
    "delta": "Hello ",
    "response_id": "resp_789"
  }
}
```

## Common Pitfalls

### ❌ Wrong: Expecting transcript_delta as session event
```python
# This will NEVER match
if event.type == "transcript_delta":
    # Dead code - never emitted as session event
```

### ✅ Correct: Access transcript_delta via raw_model_event
```python
if event.type == "raw_model_event":
    if event.data.type == "transcript_delta":
        delta = event.data.delta
```

### ❌ Wrong: Old guardrail structure
```python
# Old structure (doesn't exist in SDK)
event.guardrail_name  # ❌
event.item_id  # ❌
event.reason  # ❌
```

### ✅ Correct: Real SDK guardrail structure
```python
# Real SDK structure
event.guardrail_results  # list
event.message  # str
event.guardrail_results[0].guardrail.name  # str
```

### ❌ Wrong: Audio data location
```python
audio_data = event.audio.audio  # ❌ No such field
```

### ✅ Correct: Audio data location
```python
audio_data = event.audio.data  # ✅ bytes
```

## Testing Against Real SDK

See `tests/integration/test_realtime_sdk_integration.py` for examples of testing with actual SDK classes instead of mocks.

Key principle: **Mock the API layer, not the SDK layer**. Use real `RealtimeAgent`, `RealtimeAudio`, etc. objects in tests to catch API changes.
