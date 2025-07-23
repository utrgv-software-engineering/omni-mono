> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 4: CLI Mode Migration Design

## Implementation Plan

### Prerequisites
Before migrating CLI mode, we need to enhance LocalAgentRpcAdapter to support:

1. **MCP Servers** - Pass through to agent builder
2. **Model Settings** - Pass through to agent builder  
3. **Safe Agent Options** - Already supported via approval_callback

### Step-by-Step Implementation

#### Step 1: Enhance LocalAgentRpcAdapter
Update the adapter to accept and pass through additional parameters:

```python
class LocalAgentRpcAdapter(UnifiedAgentBase):
    def __init__(self, spec: AgentSpec, settings: Optional[Dict[str, Any]] = None, 
                 approval_callback: Optional[Any] = None,
                 mcp_servers: Optional[List[Any]] = None):
        # ... existing code ...
        self.mcp_servers = mcp_servers or []
```

Update the custom_build_agent to pass MCP servers:

```python
async def custom_build_agent(stub_settings, mcp_server, stub_app):
    # Use our captured MCP servers
    return await _default_build_agent(
        settings,
        self.mcp_servers[0] if self.mcp_servers else None,  # Pass first MCP server
        self.spec,
        approval_cb
    )
```

#### Step 2: Update create_unified_agent Factory
Add MCP servers parameter:

```python
async def create_unified_agent(
    spec: AgentSpec, 
    settings: Optional[Dict[str, Any]] = None, 
    approval_callback: Optional[Any] = None,
    mcp_servers: Optional[List[Any]] = None
) -> UnifiedAgent:
    if spec.remote_ws_url:
        # Remote agents don't need MCP servers locally
        token = os.getenv(spec.remote_auth_token_key) if spec.remote_auth_token_key else None
        return RemoteAgentProxy(spec.remote_ws_url, token)
    else:
        return LocalAgentRpcAdapter(spec, settings, approval_callback, mcp_servers)
```

#### Step 3: Migrate CLI Runner
Replace the agent construction logic (lines 139-183) with:

```python
# Build unified agent
agent = await create_unified_agent(
    spec=spec,
    settings=settings,
    approval_callback=approval_callback if spec.use_safe_agent and not spec.safe_agent_options.skip_approvals else None,
    mcp_servers=[mcp_server] if mcp_server else None
)
```

#### Step 4: Simplify Streaming Logic
Replace lines 226-232 with:

```python
# All unified agents support run_streamed
result = agent.run_streamed(new_input, run_config=run_config)
```

#### Step 5: Remove Unnecessary Imports
Remove these imports from CLI runner:
- `from agents import Agent` (inside _chat_loop)
- `from omniagents.core.agent_builder import _default_build_agent`
- `from omniagents.core.safe_agent import SafeAgent`

Keep RemoteAgentProxy import only for backward compatibility check.

### Testing Strategy

#### Unit Tests
1. Test LocalAgentRpcAdapter with MCP servers
2. Test create_unified_agent with all parameter combinations
3. Test CLI runner with mocked unified agents

#### Integration Tests
1. Test CLI with local agent (no MCP)
2. Test CLI with local agent + MCP
3. Test CLI with remote agent
4. Test CLI with safe agent approval flow

#### Manual Testing Checklist
- [ ] Basic chat interaction works
- [ ] Tool calls execute correctly
- [ ] Tool approval prompts appear (safe mode)
- [ ] Session resumption works
- [ ] MCP tools are available
- [ ] Remote agents connect properly
- [ ] Debug mode shows expected output
- [ ] Artifact callback works

### Code Changes Summary

#### Files to Modify
1. `omniagents/core/local_agent_adapter.py` - Add MCP support
2. `omniagents/core/agent_factory.py` - Add MCP parameter
3. `omniagents/backends/cli/runner.py` - Use unified agent

#### Files to Create
1. `tests/test_cli_unified.py` - Comprehensive CLI tests

### Migration Timeline
1. **Day 1**: Enhance LocalAgentRpcAdapter and factory
2. **Day 2**: Modify CLI runner to use unified agent
3. **Day 3**: Write comprehensive tests
4. **Day 4**: Manual testing and bug fixes
5. **Day 5**: Documentation and cleanup

### Success Criteria
1. All existing CLI features work identically
2. No performance regression
3. Cleaner, more maintainable code
4. Better test coverage
5. Unified agent interface across all modes