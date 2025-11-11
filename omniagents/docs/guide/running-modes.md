# Running Modes

OmniAgents supports multiple interface modes to suit different use cases.

## CLI Mode

Text-based chat in the terminal:

```bash
omniagents run -P agent.yml --mode cli
```

**Features:**
- Simple text interface
- Session persistence in SQLite
- Resume with `--session-id <id>`

**Use Cases:**
- Remote servers
- Automation scripts
- Quick testing

## TUI Mode (Bubble Tea)

Rich terminal UI built with Go:

```bash
omniagents run -P agent.yml --mode tui
```

**Features:**
- Interactive TUI with keyboard navigation
- Session picker with `--resume` flag
- Real-time message streaming
- Native performance

**Use Cases:**
- Local development
- Terminal enthusiasts
- SSH sessions

## GUI Mode (Flet)

Cross-platform desktop interface:

```bash
omniagents run -P agent.yml --mode gui
```

**Features:**
- Visual interface with tabs and panels
- Settings management
- Local storage persistence
- Browser-based rendering

**Use Cases:**
- Desktop applications
- End-user interfaces
- Visual tool approval

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

## Ink Mode (React)

Terminal UI built with React:

```bash
omniagents run -P agent.yml --mode ink
```

**Features:**
- React-based terminal rendering
- Modern component architecture

**Use Cases:**
- Node.js environments
- React developers

## Choosing a Mode

| Mode | Use Case | Pros | Cons |
|------|----------|------|------|
| CLI | Automation, scripts | Simple, lightweight | Basic interface |
| TUI | Local development | Rich UI, fast | Terminal only |
| GUI | End users | Visual, friendly | Heavier |
| Server | Web apps, remote | Flexible, remote | Requires client |

## Session Management

All modes support session persistence. See [Session Management](sessions.md) for details.

## Next Steps

- [Backends Overview](../backends/overview.md) - Learn about backend architecture
- [Server Backend](../backends/server.md) - Build remote applications
