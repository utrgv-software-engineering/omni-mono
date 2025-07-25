# OmniAgents Architecture Issues

This document tracks identified architectural issues and code organization problems in the OmniAgents codebase.

## ✅ Resolved Issues

### 1. Misplaced Multi-Mode Runner (RESOLVED)
- **Issue**: `AgentApp` was a general-purpose multi-mode runner living in the Flet backend directory
- **Solution**: Created `omniagents/core/runner.py` with a unified `Runner` class
- **Details**: See `docs/RUNNER.md` for full resolution details

## 🔴 Open Issues

### 2. Duplicate Database/Persistence Implementations

**Problem:**
- `omniagents/core/local_sessions.py`: Basic SQLite implementation for CLI/local sessions
- `omniagents/server/history_db.py`: Extended SQLite implementation with session archiving
- Both implement the same core functionality (load_history, append_message) with slight variations
- The server version adds session management features that could benefit all backends

**Example:**
```python
# In local_sessions.py
def load_history(session_id: str) -> list[dict]:
    with _lock:
        cur = _conn.execute(
            "SELECT msg_json FROM history WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    return [json.loads(r[0]) for r in rows]

# In history_db.py - same function, same implementation
def load_history(session_id: str) -> list[dict]:
    with _lock:
        cur = _conn.execute(
            "SELECT msg_json FROM history WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    return [json.loads(r[0]) for r in rows]
```

**Impact:**
- Code duplication
- Features like session archiving only available to server backend
- Maintenance burden - need to update multiple files for database changes

**Proposed Solution:**
- Move the extended history_db.py functionality to core
- Create `omniagents/core/persistence/` module
- Have all backends use the unified persistence layer

### 3. Server-Specific Code in Core

**Problem:**
`AgentService` (`omniagents/core/agent_service.py`) imports from `omniagents.server.*`:
```python
from omniagents.server.sessions import sm, Session  # line 17
from omniagents.server.runner_bridge import stream_agent  # line 18
from omniagents.server.history_db import append_message  # line 19
```

**Impact:**
- Creates a backwards dependency: core → server
- Core modules should not depend on specific backend implementations
- Makes it harder to use AgentService without the server backend

**Proposed Solution:**
- Move shared functionality from server to core
- Have server backend extend/use core functionality instead
- AgentService should only import from core modules

### 4. Inconsistent Settings Storage

**Problem:**
Each backend handles settings differently:
- **CLI Backend**: Uses environment variables only
- **Flet Backend**: Uses client-side browser storage + environment fallback
- **Server Backend**: No persistent settings storage

**Example:**
```python
# CLI backend (run_cli):
settings = {}
for field in spec.settings_fields:
    env_val = os.getenv(field.key.upper())
    settings[field.key] = env_val if env_val not in (None, "") else field.default

# Flet backend (storage.py):
async def load_settings(page: ft.Page, prefix: str, fields: List[SettingField]) -> Dict[str, Any]:
    # First try client storage, then environment variables
```

**Impact:**
- Inconsistent user experience across backends
- Settings don't persist consistently
- Each backend reimplements settings logic

**Proposed Solution:**
- Create a unified settings manager in core
- Support multiple storage backends (env vars, file, memory)
- Let each UI backend choose its storage method while using the same interface

### 5. Session Management Duplication

**Problem:**
Different session implementations across backends:
- **CLI/Flet**: `LocalSession` class (simple)
- **Server**: `Session` + `SessionManager` classes (advanced with channels, archiving)

**Example:**
```python
# LocalSession (simple)
class LocalSession:
    def __init__(self, session_id: str | None = None) -> None:
        self.id: str = session_id or str(uuid.uuid4())
        self.history: list[dict] = load_history(self.id) if session_id else []

# Server Session (advanced)
class Session:
    def __init__(self, session_id: str | None = None) -> None:
        self.id: str = session_id or str(uuid.uuid4())
        self.history: list[dict] = load_history(self.id) if session_id else []
        self.current_task: Optional[asyncio.Task] = None
        self.pending_events: list[tuple[str, dict]] = []
        self.channels: list[Any] = []
        self.archived: bool = get_archived(self.id) if session_id else False
```

**Impact:**
- Features like archiving only available in server backend
- Code duplication for basic functionality
- Inconsistent session capabilities across backends

**Proposed Solution:**
- Create a unified session interface in core
- Basic implementation for simple use cases
- Extended implementation for advanced features
- Let backends choose which features they need

### 6. Backend Initialization Inconsistency

**Problem:**
Some backends check for dependencies at import time, others at runtime:
```python
# Good - checks at runtime (FletBackend)
try:
    import flet as ft
    from .app_base import AgentApp
except ImportError as e:
    print(f"Error: Flet dependencies not installed...")
    sys.exit(1)

# Could be improved - dependencies imported at module level
# This fails at import time if dependencies missing
```

**Impact:**
- Inconsistent error handling
- Some backends fail at import, others at runtime
- Harder to provide helpful error messages

**Proposed Solution:**
- Standardize dependency checking across all backends
- Check dependencies when backend is actually used, not imported
- Provide consistent, helpful error messages

## Priority Ranking

1. **High Priority**: Issues #2, #3, #5 (Database/Session unification and core dependencies)
   - These cause the most duplication and architectural problems
   
2. **Medium Priority**: Issue #4 (Settings storage)
   - Important for user experience but not blocking
   
3. **Low Priority**: Issue #6 (Backend initialization)
   - More of a polish issue than architectural problem

## Notes

- These issues are interconnected - fixing the database layer (#2) would help with session management (#5)
- Moving server code out of core (#3) should be done before unifying the database layer
- The settings issue (#4) could be addressed independently