> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 5: Flet Mode Migration Analysis

## Current Flet Architecture

### Overview
The Flet backend is more complex than CLI, providing a full GUI with:
- Real-time chat interface with streaming responses
- Tool approval UI with interactive buttons
- Settings management with persistence
- Session history display
- MCP server connection management
- Tab-based navigation (Chat, Logs, Artifacts)

### Key Components

#### 1. Flet Backend (`omniagents/backends/flet/__init__.py`)
- Entry point that creates AgentApp instance
- Handles config loading and debug mode
- Launches Flet GUI application

#### 2. AgentApp Class (`omniagents/backends/flet/app_base.py`)
Main application class with:
- UI creation and management
- Agent lifecycle management
- MCP server connection handling
- Session persistence
- Tool approval workflow

### Current Agent Usage Patterns

#### Agent Initialization (lines 720-763)
```python
if self.spec.remote_ws_url:
    # Remote agent via proxy
    from omniagents.remote_proxy import RemoteAgentProxy
    token = os.getenv(self.spec.remote_auth_token_key) if self.spec.remote_auth_token_key else None
    self.agent_instance = RemoteAgentProxy(self.spec.remote_ws_url, token)
else:
    # Local agent with optional custom builder
    if self.spec.build_agent is None:
        builder_func = _default_build_agent
    else:
        builder_func = self.spec.build_agent
    
    # Call builder with settings, MCP server, and approval callback
    if builder_func == _default_build_agent:
        self.agent_instance = await builder_func(
            self.current_settings,
            self._mcp_server_instance,
            self.spec,
            self._request_tool_approval if self.spec.use_safe_agent else None,
        )
    else:
        self.agent_instance = await builder_func(
            self.current_settings,
            self._mcp_server_instance,
            self,  # Pass app instance for legacy builders
        )
```

#### Streaming Execution (lines 792-798)
```python
if self.spec.remote_ws_url and hasattr(self.agent_instance, "run_streamed"):
    # Use proxy's built-in streamed runner
    result = self.agent_instance.run_streamed(agent_input)
else:
    result = Runner.run_streamed(
        self.agent_instance, agent_input, max_turns=self.spec.max_turns
    )
```

#### Tool Approval Callback (lines 1062-1117)
The Flet app has a sophisticated approval UI:
- Creates visual approval request with buttons
- Handles "Approve", "Approve All", and "Cancel" actions
- Updates UI in real-time based on user choice
- Returns approval result to agent

### Integration Points to Migrate

1. **Agent Creation**
   - Replace direct agent instantiation with `create_unified_agent()`
   - Pass MCP server instance and approval callback

2. **Streaming Interface**
   - Simplify to always use `agent.run_streamed()`
   - Remove conditional logic for remote vs local

3. **Session Management**
   - Currently uses LocalSession (same as CLI)
   - This can remain unchanged

4. **MCP Server Management**
   - Complex connection lifecycle with UI updates
   - Pass MCP server to unified agent factory

5. **Tool Approval Flow**
   - Approval callback creates UI elements
   - Must work with unified agent architecture

### Key Differences from CLI

1. **Async UI Updates**
   - Flet requires careful async handling
   - UI updates must happen on main thread
   - Progress indicators and real-time feedback

2. **MCP Connection UI**
   - Visual status indicator
   - Connection/disconnection handling
   - Error state display

3. **Settings Persistence**
   - Browser local storage for settings
   - Dynamic settings updates
   - Immediate effect on agent behavior

4. **Rich Tool Display**
   - Tool calls show as interactive cards
   - Approval requests have buttons
   - Tool results displayed with formatting

### Dependencies to Consider

1. **UI Components** (`ui.py`)
   - Message, ChatMessage, ToolInteractionDisplay
   - ToolApprovalRequest with button handlers
   - Must continue working with unified agents

2. **Settings Management** (`settings.py`, `storage.py`)
   - Load/save settings from browser storage
   - Apply settings to agent on change
   - No changes needed

3. **MCP Integration**
   - MCPServerSse lifecycle management
   - Connection status tracking
   - UI indicator updates

## Migration Strategy

### Step 1: Update Agent Creation
Replace lines 720-763 with:
```python
# Build unified agent
mcp_servers = [self._mcp_server_instance] if self._mcp_server_instance else None

self.agent_instance = await create_unified_agent(
    spec=self.spec,
    settings=self.current_settings,
    approval_callback=self._request_tool_approval if self.spec.use_safe_agent else None,
    mcp_servers=mcp_servers
)
```

### Step 2: Simplify Streaming
Replace lines 792-798 with:
```python
# All unified agents support run_streamed
result = self.agent_instance.run_streamed(agent_input)
```

### Step 3: Handle Agent Rebuild
When settings change or MCP reconnects, rebuild agent:
```python
async def _rebuild_agent(self):
    """Rebuild agent with current settings and MCP connection."""
    mcp_servers = [self._mcp_server_instance] if self._mcp_server_instance else None
    
    self.agent_instance = await create_unified_agent(
        spec=self.spec,
        settings=self.current_settings,
        approval_callback=self._request_tool_approval if self.spec.use_safe_agent else None,
        mcp_servers=mcp_servers
    )
```

### Step 4: Update Imports
Remove:
- `from omniagents.core.agent_builder import _default_build_agent`
- `from agents import Agent`
- `from agents import Runner`

Add:
- `from omniagents.core.agent_factory import create_unified_agent`

## Benefits of Migration

1. **Code Simplification**
   - Remove 40+ lines of agent construction logic
   - Eliminate conditional streaming logic
   - Consistent with CLI implementation

2. **Maintainability**
   - Single code path for all agents
   - Easier to add new agent types
   - Less duplication between backends

3. **Feature Parity**
   - Both CLI and Flet use same agent architecture
   - Consistent behavior across modes
   - Easier to test and debug

## Risks and Mitigations

1. **UI Responsiveness**
   - Risk: RPC overhead affects UI performance
   - Mitigation: In-memory transport is fast, profile if needed

2. **Approval Callback Complexity**
   - Risk: UI-based approval may have timing issues
   - Mitigation: Thoroughly test approval flow

3. **MCP Reconnection**
   - Risk: Agent rebuild on MCP reconnect may disrupt chat
   - Mitigation: Preserve conversation state during rebuild

## Testing Approach

1. **Unit Tests**
   - Mock create_unified_agent in AgentApp tests
   - Verify correct parameters passed
   - Test agent rebuild scenarios

2. **Integration Tests**
   - Test full chat flow with unified agent
   - Verify tool approval UI works
   - Test MCP connection/disconnection

3. **Manual Testing**
   - Chat interaction
   - Tool approval flow
   - Settings changes
   - MCP connection status
   - Session persistence

## Next Steps

1. Create migration branch
2. Update AgentApp to use unified factory
3. Simplify streaming logic
4. Test all UI interactions
5. Update documentation