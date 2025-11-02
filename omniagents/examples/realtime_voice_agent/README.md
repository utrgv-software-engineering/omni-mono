# Realtime Voice Agent Example

This example demonstrates how to build a voice-enabled AI agent using OmniAgents' realtime protocol. The agent can have natural voice conversations, call tools, and provide real-time responses.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
pip install openai-agents

# 2. Set API key
export OPENAI_API_KEY="sk-your-key-here"

# 3. Check audio (optional)
python check_audio.py

# 4. Start server
python server.py

# 5. In another terminal, start voice client
python voice_client.py
```

Now speak into your microphone to talk with the agent!

## Features

- **Voice Interaction**: Speak naturally to the agent and receive spoken responses
- **Text Support**: Can also accept text input alongside voice
- **Tool Calling**: Agent can call weather tools during conversation
- **Audio Transcription**: Automatic transcription of user speech
- **Low Latency**: Streaming audio for responsive conversations

## Prerequisites

1. **Python 3.11+**: Required for OmniAgents
2. **OpenAI API Key**: With access to `gpt-4o-realtime-preview` model
3. **OpenAI Agents SDK**: Install with `pip install openai-agents` (imports as `agents`)
4. **PyAudio** (for voice client): Audio I/O library
5. **Microphone and Speakers**: For voice interaction

## Setup

### 1. Install System Dependencies (for Voice Client)

**macOS:**
```bash
brew install portaudio
```

**Ubuntu/Debian:**
```bash
sudo apt-get install portaudio19-dev python3-pyaudio
```

**Windows:**
PyAudio usually works out of the box. If you encounter issues, download the wheel from [here](https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio).

### 2. Install Python Dependencies

```bash
# Install omniagents dependencies
pip install -r ../../requirements.txt

# Install example-specific dependencies
pip install -r requirements.txt

# Install OpenAI agents SDK (package: openai-agents, imports as: agents)
pip install openai-agents
```

### 3. Verify Audio Setup (Optional)

Check that your audio devices are working:

```bash
python check_audio.py
```

This will:
- Verify PyAudio installation
- List all audio devices
- Test microphone recording
- Test speaker playback

### 4. Set Your API Key

Create a `.env` file in the examples directory or export the environment variable:

```bash
export OPENAI_API_KEY="sk-your-key-here"
```

## Running the Server

### Option 1: Using the CLI (Server Mode)

```bash
python -m omniagents.cli run \
    -c examples/realtime_voice_agent/agent.yaml \
    --mode server \
    --host 0.0.0.0 \
    --port 8000
```

The realtime endpoint will be available at:
```
ws://localhost:8000/ws/realtime
```

### Option 2: Using Python Directly

Create a `server.py` file:

```python
import os
import uvicorn
from fastapi import FastAPI
from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.rpc import JsonRpcEndpoint
from omniagents.rpc.realtime_service import RealtimeService

# Load agent spec
spec = load_agent_spec_from_yaml("agent.yaml")

# Build settings
settings = {
    "openai_api_key": os.getenv("OPENAI_API_KEY")
}

# Create service and endpoint
service = RealtimeService(spec, settings)
endpoint = JsonRpcEndpoint(service)

# Create FastAPI app
app = FastAPI(title="Realtime Voice Agent")
endpoint.register_route(app, "/ws/realtime")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Run with:
```bash
python server.py
```

## Using the Voice Client

### Full Voice Client (Recommended)

The voice client provides full duplex audio communication with microphone and speakers:

```bash
python voice_client.py
```

**Features:**
- Real-time audio capture from microphone
- Agent audio playback through speakers
- Live transcript display
- Tool execution notifications
- Graceful interruption (Ctrl+C)

**Output:**
```
============================================================
Realtime Voice Agent Client
============================================================

Initializing audio...

Available audio devices:
  Input: MacBook Pro Microphone
  Output: MacBook Pro Speakers

✓ Speaker initialized
✓ Microphone initialized

Connecting to ws://localhost:8000/ws/realtime...
✓ Connected to server

Starting voice session...
✓ Session started: abc-123-def

============================================================
Voice session active!
============================================================

Speak into your microphone to talk to the agent.
Press Ctrl+C to end the session.

============================================================

USER: What's the weather in San Francisco?

[Calling tool: get_current_weather]
[Tool completed: get_current_weather]

ASSISTANT: The current weather in San Francisco is 65 degrees Fahrenheit, or 18 degrees Celsius. It's partly cloudy with 72% humidity and winds at 10 miles per hour from the west.
```

**Troubleshooting Audio:**

If you see audio device errors:
```bash
# List available audio devices
python -c "import pyaudio; p = pyaudio.PyAudio(); [print(f'{i}: {p.get_device_info_by_index(i)[\"name\"]}') for i in range(p.get_device_count())]"
```

### Text-Only Test Client

For testing without audio dependencies, use the included test client:

```bash
python test_client.py
```

This client sends text queries and displays responses without requiring audio hardware.

**Example:**
```python
import asyncio
from omniagents.rpc import JsonRpcClient

async def simple_test():
    # Connect to server
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
                print(f"Event: {event['type']}")

                if event["type"] == "realtime_transcript":
                    role = event["role"]
                    text = event["transcript"]
                    print(f"  {role.upper()}: {text}")

                elif event["type"] == "realtime_tool_called":
                    print(f"  Tool: {event['tool_name']}")

    # Start event handler
    event_task = asyncio.create_task(handle_events())

    # Send text input (since we enabled text modality)
    await client.call("send_text", {
        "session_id": session_id,
        "text": "What's the weather in San Francisco?"
    })

    # Wait a bit for response
    await asyncio.sleep(10)

    # Stop session
    await client.call("stop_session", {"session_id": session_id})
    event_task.cancel()
    await client.disconnect()

    print("Test complete!")

if __name__ == "__main__":
    asyncio.run(simple_test())
```

See `voice_client.py` and `test_client.py` for complete working examples.

## Agent Configuration

### Voice Options

The agent supports multiple voices:
- `alloy`: Neutral, balanced voice
- `echo`: Clear, expressive voice
- `fable`: Warm, storytelling voice
- `onyx`: Deep, authoritative voice
- `nova`: Energetic, upbeat voice
- `shimmer`: Soft, gentle voice

Change in `agent.yaml`:
```yaml
realtime_settings:
  voice: "nova"  # Change to your preferred voice
```

### Audio Formats

Supported formats:
- `pcm16`: 16-bit PCM at 24kHz (best quality, higher bandwidth)
- `g711_ulaw`: G.711 μ-law at 8kHz (lower bandwidth)
- `g711_alaw`: G.711 A-law at 8kHz (lower bandwidth)

### Modalities

Control communication modes:
```yaml
modalities:
  - audio        # Voice only
  - audio        # Text only
  - text         # Both voice and text
```

### Turn Detection

Configure when the agent stops listening:
```yaml
turn_detection:
  type: "server_vad"          # Voice activity detection
  threshold: 0.5              # Sensitivity (0.0-1.0)
  prefix_padding_ms: 300      # Pre-speech audio
  silence_duration_ms: 500    # Silence to end turn
```

Lower `silence_duration_ms` for quicker responses but may cut off speaker.

## Available Tools

The example includes three weather tools:

### get_current_weather(location)

Get current weather conditions for a location.

```
User: "What's the weather in San Francisco?"
Agent: *calls get_current_weather("San Francisco")*
Agent: "The current weather in San Francisco is 65 degrees..."
```

### get_weather_forecast(location, days)

Get multi-day weather forecast.

```
User: "Give me a 5-day forecast for New York"
Agent: *calls get_weather_forecast("New York", 5)*
Agent: "Here's the 5-day forecast for New York..."
```

### get_weather_alerts(location)

Check for weather alerts or warnings.

```
User: "Are there any weather alerts in London?"
Agent: *calls get_weather_alerts("London")*
Agent: "There is a flood watch in effect until midnight..."
```

## Audio Storage

Audio files are automatically stored:

**Location**: `{base}/examples/voice_weather/audio/{session_id}/`

**Format**: `{session_id}_{item_id}_{content_index}.pcm16`

**Metadata**: Stored in SQLite database at `sessions.db`

## Event Types

The agent emits various events during conversation:

- `realtime_turn_started`: Agent begins processing
- `realtime_turn_ended`: Agent finishes processing
- `realtime_audio`: Agent audio output chunk
- `realtime_transcript`: Speech transcription
- `realtime_tool_called`: Agent calling a tool
- `realtime_tool_completed`: Tool execution result
- `realtime_tool_failed`: Tool execution error
- `realtime_error`: Session error

## Troubleshooting

### "OpenAI API key not provided"

Make sure `OPENAI_API_KEY` is set in your environment or `.env` file.

### "OpenAI agents SDK not installed"

Install with:
```bash
pip install openai-agents
```

Note: The package name is `openai-agents` but it imports as `agents`.

### "Session not found"

The session may have timed out or been stopped. Start a new session.

### Audio Quality Issues

- Use `pcm16` format for best quality
- Ensure microphone sample rate matches (24kHz for pcm16)
- Check network latency

### Agent Cuts Off While Speaking

Increase `silence_duration_ms` in turn detection:
```yaml
turn_detection:
  silence_duration_ms: 1000  # Longer silence before ending turn
```

## Advanced Features

### Adding Handoffs

Create multiple specialized agents and configure handoffs in `agent.yaml`:

```yaml
handoffs:
  - to_agent: "detailed_forecast_agent"
    description: "Transfer to agent that provides detailed forecasts"
    conditions:
      - "user asks for extended forecast"
      - "user wants hourly breakdown"
```

### Adding Guardrails

Protect against inappropriate content:

```yaml
input_guardrails:
  - name: "content_filter"
    description: "Block inappropriate language"

output_guardrails:
  - name: "weather_only"
    description: "Ensure responses are weather-related"
```

See `examples/guardrails_yaml` for guardrail examples.

### Session Management

List active sessions:
```python
result = await client.call("list_sessions")
print(result["sessions"])
```

Get session info:
```python
info = await client.call("get_session_info", {"session_id": session_id})
print(info)
```

## Documentation

- [Realtime Protocol](../../omniagents/rpc/realtime_protocol.md)
- [Python API Reference](../../docs/REALTIME_API.md)
- [Standard Protocol](../../omniagents/rpc/protocol.md)

## Next Steps

1. Replace mock weather tools with real API calls (OpenWeatherMap, Weather.gov, etc.)
2. Add more specialized tools (radar, air quality, UV index, etc.)
3. Implement agent handoffs for specialized queries
4. Add guardrails for content filtering
5. Build a web-based UI with WebRTC for browser access
6. Deploy to production with authentication and rate limiting

## License

See repository root for license information.
