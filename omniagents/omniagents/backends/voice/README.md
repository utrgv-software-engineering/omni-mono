# Voice Backend

Push-to-talk voice interaction backend for OmniAgents using OpenAI's Realtime API.

## Features

- **Push-to-Talk Interface**: Hold SPACEBAR to speak, release to send
- **Real-time Audio Streaming**: Low-latency audio input/output (100ms input, 200ms output chunks)
- **Voice Activity Detection**: Server-side VAD automatically detects when you stop speaking
- **Live Transcripts**: See both user and agent transcripts in real-time
- **Tool Execution**: Visual feedback when agent calls tools
- **Session Management**: Resume previous conversations with `--session-id`

## Requirements

- Python 3.11+
- OpenAI API key with access to `gpt-realtime`
- PyAudio (requires system audio libraries)
- Working microphone and speakers

## Installation

### Install Voice Backend Dependencies

```bash
pip install omniagents[voice]
```

### System Audio Libraries

**macOS:**
```bash
brew install portaudio
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt-get install portaudio19-dev
```

**Windows:**
PyAudio Windows wheels are available on PyPI.

## Agent Configuration

Your agent must be configured for realtime mode:

```yaml
# agent.yaml
name: "My Voice Agent"
realtime_mode: true

realtime_settings:
  model_name: "gpt-realtime"
  voice: "alloy"  # Options: alloy, echo, fable, onyx, nova, shimmer
  temperature: 0.8
  max_output_tokens: 4096

  # MUST include 'audio' for voice backend
  modalities:
    - audio

  # Audio format (recommended settings)
  input_audio_format: "pcm16"   # 16-bit PCM at 24kHz
  output_audio_format: "pcm16"

  # Voice activity detection
  turn_detection:
    type: "server_vad"          # Server-side voice activity detection
    threshold: 0.3              # Lower = more sensitive (0-1)
    prefix_padding_ms: 100      # Audio to include before speech detected
    silence_duration_ms: 200    # Silence duration to trigger response

# Tools (optional)
tools:
  - get_current_weather
  - execute_bash
  - read_file
  - web_search
```

## Usage

### Basic Usage

```bash
# Set your OpenAI API key
export OPENAI_API_KEY="sk-your-key-here"

# Run with voice backend
omniagents run -c agent.yaml --mode voice

# Or using Python module
python -m omniagents.cli run -c agent.yaml --mode voice
```

### Advanced Options

```bash
# Enable debug logging
omniagents run -c agent.yaml --mode voice --debug

# Resume previous session
omniagents run -c agent.yaml --mode voice --session-id <session-id>

# With authentication token
omniagents run -c agent.yaml --mode voice --auth-token "your-token"
```

## How It Works

### Architecture

```
Microphone → Voice Client → WebSocket → FastAPI Server → OpenAI Realtime API
                ↓                              ↓
          Speakers (Audio)              Tool Execution
```

1. **Server Process**: Spawns FastAPI server in background thread with `/ws/realtime` endpoint
2. **Voice Client**: Connects to server via WebSocket using JSON-RPC 2.0 protocol
3. **Push-to-Talk**: SPACEBAR key triggers audio capture and streaming
4. **VAD**: Server detects silence and triggers agent response
5. **Audio Playback**: Agent audio streamed back and played through speakers

### Push-to-Talk Flow

1. **Hold SPACEBAR** → Microphone captures audio in 100ms chunks
2. **Release SPACEBAR** → Client sends silence to trigger server VAD
3. **Agent Processing** → Whisper transcribes, agent responds with audio
4. **Playback** → Agent audio plays through speakers

### Events Displayed

The voice client displays the following events in real-time:

- **Transcripts** (`realtime_transcript`) - User and agent speech-to-text
- **Tool Calls** (`realtime_tool_start`) - When agent starts calling a tool with arguments (🔧 icon)
- **Tool Results** (`realtime_tool_end`) - When tool completes with output preview (✓ icon)
- **Errors** (`realtime_error`) - Any errors during the session

#### Example Tool Call Display

```
🔧 [Calling tool: get_current_weather]
   Arguments: {
     "location": "San Francisco"
   }
✓ [Tool completed: get_current_weather]
  Result: San Francisco: Temperature: 68°F, Conditions: Partly cloudy...
```

Additional events available (with `--debug`):
- `realtime_audio` - Audio chunks being streamed
- `realtime_agent_start` / `realtime_agent_end` - Agent turn boundaries
- `realtime_audio_interrupted` - When user interrupts agent

### Audio Configuration

- **Sample Rate**: 24kHz (pcm16 format)
- **Channels**: 1 (mono)
- **Format**: 16-bit PCM
- **Input Chunk**: 2400 frames = 100ms (low latency)
- **Output Chunk**: 4800 frames = 200ms

## Keyboard Controls

- **SPACEBAR (Hold)**: Record audio
- **SPACEBAR (Release)**: Send audio to agent
- **Ctrl+C**: End session and exit

## Troubleshooting

### PyAudio Installation Issues

**macOS:**
```bash
brew install portaudio
pip install pyaudio
```

**Linux:**
```bash
sudo apt-get install portaudio19-dev python3-dev
pip install pyaudio
```

**Windows:**
Download pre-built wheel from [PyPI](https://pypi.org/project/PyAudio/).

### Audio Device Issues

Check available audio devices:
```bash
python -c "import pyaudio; p = pyaudio.PyAudio(); [print(f'{i}: {p.get_device_info_by_index(i)[\"name\"]}') for i in range(p.get_device_count())]"
```

### No Audio Output

- Verify speakers are set as default output device
- Check system volume is not muted
- Run the voice client with `--debug` to see audio chunk information

### Microphone Not Working

- Grant microphone permissions to terminal/Python
- Verify microphone is set as default input device
- Check `Privacy & Security` settings on macOS

### Connection Errors

- Ensure `OPENAI_API_KEY` is set correctly
- Verify you have access to `gpt-4o-realtime-preview` model
- Check network connectivity
- Run with `--debug` to see detailed error messages

## Example Agents

See `examples/realtime_voice_agent/` for a complete working example with:
- Weather tools (current conditions, forecasts, alerts)
- File operations (read, write, search)
- Bash command execution
- Web search and fetch

## API Reference

### VoiceBackend Class

Located in `omniagents/backends/voice/__init__.py`

```python
class VoiceBackend:
    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the voice backend.

        Args:
            spec: Agent specification (must have realtime_mode=True)
            **kwargs:
                - config_path: Path to agent.yaml
                - auth_token: Optional authentication token
                - debug: Enable debug logging
                - session_id: Resume specific session
        """
```

### Voice Client

Located in `omniagents/backends/voice/client.py`

```python
def run_voice_client(
    server_url: str,
    agent_name: str = "Voice Agent",
    auth_token: Optional[str] = None,
    session_id: Optional[str] = None,
    debug: bool = False,
):
    """Main entry point for voice client."""
```

## Voice Options

Configure different voice personalities in `realtime_settings.voice`:

- **alloy**: Neutral, balanced voice
- **echo**: Clear, expressive voice
- **fable**: Warm, storytelling voice
- **onyx**: Deep, authoritative voice
- **nova**: Energetic, upbeat voice
- **shimmer**: Soft, gentle voice

## Session Storage

Sessions are stored in SQLite database:
- **Location**: `sessions.db` in working directory
- **Audio Files**: Stored in `audio/{session_id}/` directory
- **Format**: PCM16 audio chunks with metadata

## Environment Variables

- `OPENAI_API_KEY`: Your OpenAI API key (required)
- `OMNI_DEBUG`: Enable debug logging (optional)

## Related Documentation

- [OpenAI Realtime API Documentation](https://platform.openai.com/docs/guides/realtime)
- [OmniAgents Server Backend](../server/)
- [Realtime Voice Agent Example](../../../examples/realtime_voice_agent/)

## License

Part of OmniAgents framework.
