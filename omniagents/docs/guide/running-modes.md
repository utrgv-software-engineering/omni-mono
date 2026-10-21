# Running Modes

OmniAgents supports multiple interface modes to suit different use cases.

## Web Mode (Default)

Browser-based interface:

```bash
omniagents run -P agent.yml --mode web
```

**Features:**
- Modern web interface
- Visual tool approval dialogs
- Session management
- Real-time message streaming

**Use Cases:**
- Local development
- End-user interfaces
- Visual feedback and interaction

To dedicate a different agent for realtime voice sessions, add `voice_entrypoint` in `project.yml`:

```yaml
agents:
  entrypoint: support_bot
  voice_entrypoint: support_bot_voice
```
When `--mode web` runs without `--agent`, the voice entrypoint is used.

### Voice Backends

`/ws/realtime` supports two server-side implementations:

- `voice_backend: realtime` (default) uses OpenAI realtime sessions (`agents.realtime.*`).
- `voice_backend: pipeline` uses the voice pipeline (`agents.voice.VoicePipeline`) with STT → agent run → TTS.

Pipeline voice requires extra dependencies:

```bash
pip install "omniagents[voice]"
```

To use an OpenAI-compatible endpoint for both the agent calls and the voice pipeline STT/TTS, provide a `model_config_resolver` that returns `provider: openai-compatible` with `base_url` and `api_key`.

## Ink Mode

Terminal UI built with React:

```bash
omniagents run -P agent.yml --mode ink
```

**Features:**
- React-based terminal rendering
- Modern component architecture
- Interactive terminal UI

**Use Cases:**
- Node.js environments
- Terminal-based workflows
- React developers

## Server Mode

JSON-RPC WebSocket API:

```bash
omniagents run -P agent.yml --mode server --host 0.0.0.0 --port 8000
```

**Features:**
- WebSocket at `/ws`
- JSON-RPC 2.0 protocol
- Stateful sessions
- Remote agent control

**Use Cases:**
- Web applications
- Remote clients
- Integration with other systems

If your `project.yml` defines `agents.voice_entrypoint`, running server mode without `--agent` loads that voice agent so `/ws/realtime` uses its realtime configuration.

See [RPC Protocol](../api/rpc-protocol.md) for protocol details.

## Choosing a Mode

| Mode | Use Case | Pros | Cons |
|------|----------|------|------|
| Web | Local development, end users | Visual, feature-rich, optional realtime voice | Browser required |
| Ink | Terminal workflows | Lightweight, modern UI | Terminal only |
| Server | Web apps, remote | Flexible, remote | Requires client |

## Session Management

All modes support session persistence. See [Session Management](sessions.md) for details.

## Next Steps

- [Backends Overview](../backends/overview.md) - Learn about backend architecture
- [Server Backend](../backends/server.md) - Build remote applications
