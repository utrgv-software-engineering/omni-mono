> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 1: AgentService Extraction Plan

## Overview
Extract the `AgentService` class from its current nested location in `server/app.py:build_app()` to a standalone class in `core/agent_service.py`.

## Current State Analysis

### Location
- Currently: Nested class inside `build_app()` function (lines 77-290 of `server/app.py`)
- Target: Standalone class in `/omniagents/core/agent_service.py`

### Dependencies Captured via Closure
The current implementation captures these via closure:
1. `spec: AgentSpec` - Agent configuration
2. `auth_token: str | None` - Not used within AgentService itself

### External Dependencies
- `omniagents.jsonrpc.rpc.RpcMethodsBase` - Base class
- `omniagents.server.sessions.sm` - Global SessionManager instance
- `omniagents.server.runner_bridge.stream_agent` - Agent execution
- `omniagents.server.history_db.append_message` - History persistence
- `omniagents.core.agent_builder._default_build_agent` - Agent creation

### RPC Methods
1. `tool_approval_response(approval_id, approved, always_approve, error_message)` - Handle tool approval
2. `start_run(prompt, session_id)` - Start agent execution
3. `stop_run(run_id)` - Cancel active run
4. `get_session_history(session_id)` - Retrieve conversation history
5. `list_sessions()` - List all sessions
6. `archive_session(session_id)` - Archive a session

### Internal Methods
1. `_safe_notify(channel, method, payload)` - Safe WebSocket notification
2. `_request_tool_approval(tool_name, formatted_args, _sess)` - Request approval from client

## Extraction Steps

### Step 1: Create AgentService Class
Create `/omniagents/core/agent_service.py` with:
- Class that inherits from `RpcMethodsBase`
- Constructor accepting `spec: AgentSpec`
- All RPC and internal methods moved from nested class

### Step 2: Handle Dependencies
- Pass `spec` as constructor parameter instead of closure
- Import all external dependencies at module level
- Maintain `_pending_approvals` as instance variable

### Step 3: Fix Agent Building
The tricky part is the approval callback creation in `start_run`:
- Currently creates a closure capturing `sess` for pending event handling
- Need to refactor to pass session to `_request_tool_approval` method

### Step 4: Update server/app.py
- Import AgentService from core
- Instantiate AgentService with spec
- Pass instance to JsonRpcEndpoint

## Implementation Details

### Constructor
```python
class AgentService(RpcMethodsBase):
    def __init__(self, spec: AgentSpec):
        super().__init__()
        self.spec = spec
        self._pending_approvals: dict[str, asyncio.Future] = {}
```

### Approval Callback Handling
The approval callback needs access to both the service instance and the session:
```python
# In start_run method:
stub_app = SimpleNamespace(
    spec=self.spec,
    _request_tool_approval=lambda name, args: self._request_tool_approval(name, args, _sess=sess),
)
```

### Updated build_app
```python
def build_app(config_path: str | None = None, spec: 'AgentSpec' | None = None, auth_token: str | None = None) -> FastAPI:
    # ... resolve spec ...
    
    app = FastAPI()
    
    # Create service instance
    service = AgentService(spec)
    endpoint = JsonRpcEndpoint(service)
    
    # Register route with auth if needed
    if auth_token:
        endpoint.register_route(app, "/ws", dependencies=[_token_dependency(auth_token)])
    else:
        endpoint.register_route(app, "/ws")
    
    return app
```

## Testing Considerations
- All existing tests should pass without modification
- The external API remains unchanged
- Internal behavior is preserved

## Risk Mitigation
- Keep the extraction minimal - only move the class, don't refactor internals
- Preserve all method signatures exactly
- Maintain the same import structure for dependencies
- Test incrementally after each change