# Session Management

OmniAgents persists conversation history in SQLite, allowing you to resume conversations and maintain context across runs.

## Session Storage

Sessions are stored in `sessions.db` (SQLite database) at the repository root by default.

### Database Location

Default: `./sessions.db`

Configure via environment variable:

```bash
export OMNIAGENTS_DB_PATH=/path/to/sessions.db
```

## Session Lifecycle

1. **Creation**: A new session is created when you start an agent
2. **Storage**: Messages and tool calls are saved to the database
3. **Resume**: Sessions can be resumed by ID

## Resuming Sessions

### CLI Mode

Use the `--session-id` flag:

```bash
omniagents run -P agent.yml --mode cli --session-id abc123
```

### TUI Mode

Use the `--resume` flag to open a session picker:

```bash
omniagents run -P agent.yml --mode tui --resume
```

The session picker displays:
- Session ID
- Creation date
- Last message preview
- Message count

### GUI Mode

The GUI maintains session state automatically during runtime.

### Server Mode

Clients specify session IDs when connecting to the WebSocket endpoint:

```python
from omniagents.rpc import RemoteAgentProxy

proxy = RemoteAgentProxy(
    url="ws://localhost:8000/ws",
    session_id="abc123"
)
```

## Session Data

Each session stores:

- **Session ID**: Unique identifier
- **Agent Configuration**: Agent name, instructions, tools
- **Message History**: User messages, agent responses
- **Tool Calls**: Tool invocations and results
- **Metadata**: Timestamps, status, etc.

## Managing Sessions

### List Sessions

```python
from omniagents.core.session import SessionManager

manager = SessionManager("sessions.db")
sessions = manager.list_sessions()

for session in sessions:
    print(f"{session.id}: {session.created_at}")
```

### Delete Sessions

```python
manager.delete_session("session-id")
```

## Best Practices

1. **Unique IDs**: Use meaningful session IDs for important conversations
2. **Cleanup**: Periodically delete old sessions
3. **Backup**: Back up `sessions.db` for critical data
4. **Security**: Protect session database access
5. **Privacy**: Be aware of sensitive data in conversation history

## Session Context

Sessions maintain conversation context, allowing agents to:

- Reference previous messages
- Build on earlier conversations
- Maintain state across runs

## Example: Long-Running Analysis

```bash
# Start analysis
omniagents run -P analyst.yml --mode cli --session-id analysis-2025-11-11

# User: "Analyze sales data for Q1"
# Agent performs analysis...

# Later, resume the session
omniagents run -P analyst.yml --mode cli --session-id analysis-2025-11-11

# User: "Now compare with Q2"
# Agent has context from Q1 analysis
```

## Database Schema

The session database includes tables for:

- `sessions`: Session metadata
- `messages`: Conversation messages
- `tool_calls`: Tool execution records

## Next Steps

- [Running Modes](running-modes.md) - Learn about different execution modes
- [Server Backend](../backends/server.md) - Remote session management
