> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 5: Flet Mode Migration Design

## Implementation Plan

### Overview
Migrate Flet GUI backend to use the unified RPC client architecture, maintaining all UI functionality while simplifying agent management code.

### Step-by-Step Implementation

#### Step 1: Update Imports in app_base.py
Remove these imports:
```python
from omniagents.core.agent_builder import _default_build_agent
from agents import Agent, Runner
```

Add this import:
```python
from omniagents.core.agent_factory import create_unified_agent
```

Keep these imports (still needed):
```python
from agents import ItemHelpers  # For message parsing
from agents.mcp import MCPServerSse  # For MCP management
from omniagents.core.safe_agent import SafeAgent  # For SafeAgent compatibility in from_agent
```

#### Step 2: Replace Agent Creation Logic
Replace the entire agent creation section (lines 720-763) with:

```python
async def _create_or_update_agent(self):
    """Create or update the agent instance using unified factory."""
    try:
        # Ensure we have the latest MCP connection
        await self._update_mcp_connection()
        
        # Build unified agent
        mcp_servers = [self._mcp_server_instance] if self._mcp_server_instance else None
        
        self.agent_instance = await create_unified_agent(
            spec=self.spec,
            settings=self.current_settings,
            approval_callback=self._request_tool_approval if self.spec.use_safe_agent else None,
            mcp_servers=mcp_servers
        )
        
        print(f"Agent instance ready: {type(self.agent_instance)}")
        
    except Exception as e:
        print(f"Error creating agent: {e}")
        self.agent_instance = None
        raise
```

Update `_process_message` to call this new method:
```python
# Replace lines 716-763 with:
if self.agent_instance is None:
    await self._create_or_update_agent()
```

#### Step 3: Simplify Streaming Logic
Replace the streaming execution (lines 792-798) with:

```python
# All unified agents support run_streamed
result = self.agent_instance.run_streamed(agent_input)
```

#### Step 4: Handle Agent State Reset
The reset_run_state check (lines 774-777) can remain as-is since UnifiedAgent implementations may have this method:

```python
# Reset agent state if applicable (SafeAgent compatibility)
if hasattr(self.agent_instance, "reset_run_state") and callable(
    self.agent_instance.reset_run_state
):
    self.agent_instance.reset_run_state()
```

#### Step 5: Update from_agent Class Method
The `from_agent` method needs to handle the fact that we're moving away from direct agent instances. Options:

1. **Deprecate from_agent** (Recommended)
   - Add deprecation warning
   - Guide users to use YAML config instead

2. **Convert to Spec-based**
   - Extract properties from agent to create AgentSpec
   - Use create_unified_agent internally

For now, add deprecation warning:
```python
@classmethod
def from_agent(cls, agent: AgentLike, **kwargs):
    """Create an AgentApp from an existing agent instance.
    
    DEPRECATED: This method is deprecated. Please use YAML configuration
    or create an AgentSpec directly.
    """
    import warnings
    warnings.warn(
        "AgentApp.from_agent is deprecated. Use YAML configuration instead.",
        DeprecationWarning,
        stacklevel=2
    )
    # ... existing implementation ...
```

#### Step 6: Handle Settings Changes
When settings change, we need to rebuild the agent. Add this method:

```python
async def _on_settings_changed(self):
    """Handle settings changes by rebuilding the agent."""
    if self.agent_instance is not None:
        print("Settings changed, rebuilding agent...")
        try:
            await self._create_or_update_agent()
        except Exception as e:
            print(f"Error rebuilding agent after settings change: {e}")
            # Keep existing agent if rebuild fails
```

Update the save settings handler to call this:
```python
async def _save_settings_handler(self, e):
    # ... existing save logic ...
    # After successful save:
    await self._on_settings_changed()
```

#### Step 7: Handle MCP Reconnection
Update `_update_mcp_connection` to trigger agent rebuild when MCP status changes:

```python
async def _update_mcp_connection(self):
    """Update MCP connection and rebuild agent if needed."""
    prev_status = self._mcp_connection_status
    
    # ... existing MCP connection logic ...
    
    # If status changed and we have an agent, rebuild it
    if (self._mcp_connection_status != prev_status and 
        self.agent_instance is not None and
        prev_status != MCPConnectionStatus.UNKNOWN):
        print("MCP connection status changed, rebuilding agent...")
        await self._create_or_update_agent()
```

### Testing Strategy

#### Unit Tests
Create `test_flet_unified.py` with:
1. Mock create_unified_agent
2. Test agent creation with various configurations
3. Test settings change triggers rebuild
4. Test MCP connection changes

#### Integration Tests
1. Test full chat flow
2. Test tool approval UI
3. Test settings persistence
4. Test MCP status indicator

#### Manual Testing Checklist
- [ ] Basic chat functionality
- [ ] Tool execution and display
- [ ] Tool approval flow (approve/reject/always)
- [ ] Settings dialog save/cancel
- [ ] MCP connection/disconnection
- [ ] Session history display
- [ ] Error handling for failed agent creation
- [ ] Remote agent mode
- [ ] Local agent with SafeAgent

### Migration Timeline
1. **Day 1**: Update imports and agent creation
2. **Day 2**: Handle settings and MCP changes
3. **Day 3**: Write tests
4. **Day 4**: Manual testing and bug fixes

### Code Changes Summary

#### Files to Modify
1. `omniagents/backends/flet/app_base.py`
   - Update imports
   - Replace agent creation logic
   - Simplify streaming
   - Add rebuild handlers

#### Files to Create
1. `tests/test_flet_unified.py` - Test Flet with unified agents

### Success Criteria
1. All Flet UI features work identically
2. No visual changes for users
3. Code is simpler and more maintainable
4. Tests pass
5. Both local and remote agents work
6. MCP integration continues to function

### Potential Issues and Solutions

1. **Async UI Updates**
   - Issue: Agent rebuild might block UI
   - Solution: Show progress indicator during rebuild

2. **State Preservation**
   - Issue: Agent rebuild might lose state
   - Solution: Conversation is separate from agent

3. **Error Recovery**
   - Issue: Agent creation might fail
   - Solution: Show error in UI, allow retry

4. **Performance**
   - Issue: In-memory RPC might add latency
   - Solution: Profile and optimize if needed