# Voice Pipeline (OpenAI-Compatible) Example

This example demonstrates OmniAgents voice mode using the **pipeline** backend (`agents.voice.VoicePipeline`) and a custom **openai-compatible** endpoint.

## Requirements

- Install with voice + web extras:

```bash
pip install "omniagents[web,voice]"
```

Your endpoint must support:
- Realtime transcription websocket (for STT): `.../realtime?intent=transcription`
- Audio speech streaming (for TTS): `POST .../audio/speech` (OpenAI-compatible)

## Configure

Set these env vars (or adjust `model_config.py`):

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://your-host/openai/v1
```

## Run

```bash
omniagents run -P agent.yml --mode web
```

## Customize TTS Voice Instructions

This example sets `realtime_settings.tts_instructions` to control the TTS tone/style when using `voice_backend: pipeline`.

Edit `agent.yml`:

```yaml
realtime_settings:
  voice: alloy
  tts_instructions: "Speak clearly and naturally. Keep responses concise."
```

Open the UI and click the mic button to start voice mode.
