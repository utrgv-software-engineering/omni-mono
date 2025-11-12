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

## Voice Mode

Voice-based interaction:

```bash
omniagents run -P agent.yml --mode voice
```

**Features:**
- Voice input and output
- Real-time audio processing
- Natural conversation flow

**Use Cases:**
- Hands-free operation
- Voice assistants
- Accessibility

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

See [RPC Protocol](../api/rpc-protocol.md) for protocol details.

## Choosing a Mode

| Mode | Use Case | Pros | Cons |
|------|----------|------|------|
| Web | Local development, end users | Visual, feature-rich | Browser required |
| Ink | Terminal workflows | Lightweight, modern UI | Terminal only |
| Voice | Hands-free, accessibility | Natural interaction | Requires audio setup |
| Server | Web apps, remote | Flexible, remote | Requires client |

## Session Management

All modes support session persistence. See [Session Management](sessions.md) for details.

## Next Steps

- [Backends Overview](../backends/overview.md) - Learn about backend architecture
- [Server Backend](../backends/server.md) - Build remote applications
