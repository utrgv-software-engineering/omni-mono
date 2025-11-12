# Session Management

OmniAgents persists conversation history in SQLite databases, allowing you to resume conversations and maintain context across runs. Each project and agent combination has its own database, keeping conversations organized and isolated.

## Session Storage

### Storage Location

Sessions are stored in a hierarchical directory structure:

```
~/.omniagents/
└── sessions/
    └── <project_slug>/
        └── <agent_slug>/
            ├── sessions.db      # SQLite database
            └── audio/           # Audio files (realtime mode)
```

**Example:**
```
~/.omniagents/sessions/my_project/data_analyst/sessions.db
```

### Environment Variables

#### OMNIAGENTS_HOME
Change the base directory for all OmniAgents data:

```bash
export OMNIAGENTS_HOME="/custom/path"
# Sessions will be in: /custom/path/sessions/
```

#### OMNIAGENTS_HISTORY_DB
Override the session database path completely:

```bash
export OMNIAGENTS_HISTORY_DB="/path/to/custom/sessions.db"
```

This bypasses the default hierarchical structure and stores all sessions in a single database.

#### OMNIAGENTS_AUDIO_STORAGE
Override audio storage location (for realtime voice agents):

```bash
export OMNIAGENTS_AUDIO_STORAGE="/path/to/audio/"
```

## Session Lifecycle

1. **Creation**: A new session is created when you start an agent run
   - Automatic UUID is generated, or you can specify a custom session ID
   - Session record is registered in the database
2. **Storage**: Messages and tool calls are persisted in real-time
   - Each message is appended to the history table
   - Audio metadata is stored separately (for realtime agents)
3. **Resume**: Sessions can be resumed by ID to continue conversations
4. **Archive**: Sessions can be marked as archived (hidden from default views)

## Resuming Sessions

### From Project Directory

When inside a project directory, sessions are auto-discovered:

```bash
# Navigate to project
cd /path/to/myproject

# Resume with specific session ID
omniagents run --mode cli --session-id abc123

# TUI mode with session picker
omniagents run --mode tui --resume
```

### From Outside Project

Specify the project path:

```bash
# With session ID
omniagents run -P /path/to/myproject --mode cli --session-id abc123

# With session picker
omniagents run -P /path/to/myproject --mode tui --resume
```

### Session Picker (TUI Mode)

The `--resume` flag opens an interactive session picker displaying:

```
┌─ Session History ────────────────────────────────┐
│  Session ID                      Created   Msgs  │
│  ──────────────────────────────────────────────  │
│  analysis-2025-11-11             Nov 11     45   │
│  └─ "Analyze Q1 sales data..."                   │
│                                                   │
│  abc123                          Nov 10     12   │
│  └─ "Help me debug this issue..."                │
│                                                   │
│  [↑↓] Navigate  [Enter] Select  [q] Quit         │
└──────────────────────────────────────────────────┘
```

For each session:
- Session ID (user-specified or UUID)
- Creation timestamp
- Message count
- Preview of first or last message

### GUI Mode (Flet)

The GUI automatically manages sessions:
- New session created on startup (or resumes if session ID provided)
- Session persists across page reloads
- Session history accessible via UI controls

### Server Mode (RPC)

Clients control sessions via the WebSocket connection:

**Creating a new session:**
```json
{
  "jsonrpc": "2.0",
  "method": "start_run",
  "params": {
    "input": "Hello, agent!",
    "session_id": null  // Generates new UUID
  },
  "id": 1
}
```

**Resuming a session:**
```json
{
  "jsonrpc": "2.0",
  "method": "start_run",
  "params": {
    "input": "Continue our discussion",
    "session_id": "abc123"  // Resumes existing
  },
  "id": 2
}
```

**Using RemoteAgentProxy:**
```python
from omniagents.rpc import RemoteAgentProxy

# New session
proxy = RemoteAgentProxy(url="ws://localhost:8000/ws")

# Resume existing
proxy = RemoteAgentProxy(
    url="ws://localhost:8000/ws",
    session_id="abc123"
)
```

## Session Data

### Database Schema

Each session database contains three tables:

#### sessions table
Stores session metadata:
- `session_id` (TEXT, PRIMARY KEY): Unique session identifier
- `archived` (INTEGER): 0 or 1, whether session is archived
- `created_at` (TIMESTAMP): Session creation time

#### history table
Stores conversation messages:
- `id` (INTEGER, PRIMARY KEY): Auto-incrementing message ID
- `session_id` (TEXT): FK to sessions table
- `msg_json` (TEXT): JSON-serialized message object
- `created_at` (TIMESTAMP): Message timestamp

Message JSON includes:
- `role`: "user", "assistant", or "tool"
- `content`: Message text content
- Tool call information (if applicable)

#### audio_metadata table
Stores audio file metadata (realtime agents only):
- `id` (INTEGER, PRIMARY KEY): Auto-incrementing ID
- `session_id` (TEXT): FK to sessions table
- `message_id` (INTEGER): Optional FK to history table
- `item_id` (TEXT): Realtime item identifier
- `content_index` (INTEGER): Content index within item
- `file_path` (TEXT): Path to audio file
- `audio_format` (TEXT): Format (e.g., "pcm16")
- `duration_ms` (INTEGER): Audio duration in milliseconds
- `created_at` (TIMESTAMP): Creation time

## Managing Sessions

### Programmatic Access

Use the session management API to interact with sessions:

```python
from omniagents.core.session.history_db import (
    list_sessions,
    delete_session,
    load_history,
    set_archived
)

# List all sessions for a project/agent
sessions = list_sessions(
    project_slug="my_project",
    agent_slug="data_analyst"
)

for session in sessions:
    print(f"ID: {session['id']}")
    print(f"Created: {session['created_at']}")
    print(f"Messages: {session['message_count']}")
    print(f"Archived: {session['archived']}")
    if 'first_message' in session:
        print(f"First: {session['first_message']['content'][:50]}...")
    if 'last_message' in session:
        print(f"Last: {session['last_message']['content'][:50]}...")
    print()

# Load conversation history
history = load_history(
    "session-id",
    project_slug="my_project",
    agent_slug="data_analyst"
)
for msg in history:
    print(f"{msg['role']}: {msg.get('content', '')[:100]}")

# Archive a session (hides from default views)
set_archived(
    "session-id",
    True,
    project_slug="my_project",
    agent_slug="data_analyst"
)

# Delete a session permanently
success = delete_session(
    "session-id",
    project_slug="my_project",
    agent_slug="data_analyst"
)
print(f"Deleted: {success}")
```

### SessionManager Class

For server implementations, use the SessionManager:

```python
from omniagents.core.session import SessionManager

# Create manager for specific project/agent
manager = SessionManager(
    project_slug="my_project",
    agent_slug="data_analyst"
)

# Get or create a session
session = manager.get_or_create(session_id="abc123")

# Access session data
print(f"Session ID: {session.id}")
print(f"History: {len(session.history)} messages")
print(f"Archived: {session.archived}")

# Append a message
session.append_message({
    "role": "user",
    "content": "Hello!"
})

# Archive a session
manager.archive_session("abc123")

# List all sessions
all_sessions = manager.list_sessions()
```

## Best Practices

### 1. Use Meaningful Session IDs

For important or long-running conversations, use descriptive session IDs:

```bash
# Good - descriptive
omniagents run --session-id "q4-2025-financial-analysis"

# Less useful - random UUID
omniagents run --session-id "f47ac10b-58cc-4372-a567-0e02b2c3d479"
```

### 2. Archive Instead of Delete

Use archiving to hide completed sessions while preserving history:

```python
from omniagents.core.session.history_db import set_archived

# Archive completed sessions
set_archived("old-session-id", True,
            project_slug="project", agent_slug="agent")
```

Archived sessions:
- Don't appear in default session lists
- Are excluded from session pickers
- Can be un-archived later if needed

### 3. Regular Cleanup

Periodically delete old, unnecessary sessions:

```python
from omniagents.core.session.history_db import list_sessions, delete_session
from datetime import datetime, timedelta

sessions = list_sessions(project_slug="project", agent_slug="agent")

# Delete sessions older than 90 days
cutoff = datetime.now() - timedelta(days=90)
for session in sessions:
    created = datetime.fromisoformat(session['created_at'])
    if created < cutoff:
        delete_session(session['id'],
                      project_slug="project", agent_slug="agent")
```

### 4. Backup Important Sessions

Back up session databases for critical conversations:

```bash
# Backup entire sessions directory
tar -czf sessions-backup-$(date +%Y%m%d).tar.gz ~/.omniagents/sessions/

# Backup specific project
cp -r ~/.omniagents/sessions/my_project /backup/location/
```

### 5. Security Considerations

- **File Permissions**: Restrict database file access
  ```bash
  chmod 600 ~/.omniagents/sessions/*/*/sessions.db
  ```
- **Sensitive Data**: Be aware that conversations are stored in plaintext
- **Encryption**: Consider encrypting the sessions directory for sensitive applications
- **Access Control**: Protect the `~/.omniagents` directory with appropriate permissions

### 6. Monitor Database Size

Session databases can grow large over time:

```bash
# Check database size
du -sh ~/.omniagents/sessions/*/*/sessions.db

# Find large databases
find ~/.omniagents/sessions -name "sessions.db" -size +100M
```

Consider archiving or deleting old sessions if databases become too large.

## Session Context & Continuity

Sessions enable agents to maintain context across multiple interactions:

### Conversational Memory

The agent remembers previous exchanges:

```bash
# Session 1
User: "What's 15 * 24?"
Agent: "360"

# Resume same session later
User: "Now divide that by 3"
Agent: "120 (360 ÷ 3)"
```

### Multi-Step Workflows

Break complex tasks across multiple runs:

```bash
# Day 1: Data collection
omniagents run --session-id "market-research-nov-2025"
User: "Gather competitor pricing data"

# Day 2: Analysis
omniagents run --session-id "market-research-nov-2025"
User: "Analyze the data you collected yesterday"

# Day 3: Reporting
omniagents run --session-id "market-research-nov-2025"
User: "Create a summary report of your findings"
```

### Context Limitations

Be aware of model context windows:
- GPT-4: ~128K tokens
- GPT-4o-mini: ~128K tokens

Long sessions may exceed context limits. The agent SDK automatically manages this, but very old messages may be truncated.

## Complete Example

**Scenario**: Financial analysis project with multiple sessions

```bash
# Initialize project
cd ~/projects/financial-analysis

# Q1 Analysis session
omniagents run --mode cli --session-id "q1-2025"
> "Analyze Q1 revenue trends"

# Q2 Analysis session
omniagents run --mode cli --session-id "q2-2025"
> "Analyze Q2 revenue trends"

# Comparative analysis (resume Q1 for context)
omniagents run --mode cli --session-id "q1-2025"
> "Compare these results with Q2"

# Later: Archive completed analyses
python
>>> from omniagents.core.session.history_db import set_archived
>>> set_archived("q1-2025", True,
...             project_slug="financial_analysis",
...             agent_slug="analyst")
>>> set_archived("q2-2025", True,
...             project_slug="financial_analysis",
...             agent_slug="analyst")
```

## Realtime Voice Sessions

Realtime voice agents store audio data alongside text:

```
~/.omniagents/sessions/voice_project/voice_agent/
├── sessions.db        # Text + metadata
└── audio/             # Audio files
    ├── item_abc123_0.pcm
    ├── item_abc123_1.pcm
    └── item_xyz789_0.pcm
```

Audio files are referenced in the `audio_metadata` table with:
- File path
- Format (pcm16, etc.)
- Duration
- Associated message ID

## Next Steps

- **[Running Modes](running-modes.md)** - Learn about different execution modes
- **[Server Backend](../backends/server.md)** - Remote session management via RPC
- **[Configuration](../getting-started/configuration.md)** - Environment variables and storage paths
