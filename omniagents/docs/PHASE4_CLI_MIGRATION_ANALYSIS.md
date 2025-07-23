> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 4: CLI Mode Migration Analysis

## Current CLI Architecture

### Overview
The CLI mode currently has a dual implementation:
1. **Local agents**: Direct instantiation of Agent/SafeAgent objects
2. **Remote agents**: Uses RemoteAgentProxy (already using RPC)

### Key Components

#### 1. CLI Backend (`omniagents/backends/cli/__init__.py`)
- Simple wrapper that calls `run_cli()` async function
- Passes AgentSpec and command-line args

#### 2. CLI Runner (`omniagents/backends/cli/runner.py`)
Main implementation with these responsibilities:
- Environment setup and settings loading
- Agent construction (local vs remote)
- MCP server context management
- Chat loop with streaming responses
- Session management via LocalSession
- Tool approval callbacks

### Current Agent Usage Patterns

#### Local Agent Creation (lines 155-162)
```python
base_agent = Agent(
    name=spec.name,
    tools=tools,
    instructions=instructions,
    model=spec.model_name,
    model_settings=model_settings,
    mcp_servers=mcp_list,
)
```

#### SafeAgent Wrapping (lines 168-181)
```python
wrapped_tools = await base_agent.get_all_tools()
agent = SafeAgent(
    name=base_agent.name,
    tools=wrapped_tools,
    instructions=base_agent.instructions,
    model=base_agent.model,
    model_settings=base_agent.model_settings,
    mcp_servers=base_agent.mcp_servers,
    safe_tool_names=spec.safe_agent_options.safe_tool_names,
    safe_tool_patterns=spec.safe_agent_options.safe_tool_patterns,
    skip_approvals=spec.safe_agent_options.skip_approvals,
    halt_on_rejection=spec.safe_agent_options.halt_on_rejection,
    approval_callback=approval_callback,
    debug_mode=spec.debug_flag,
)
```

#### Remote Agent Usage (lines 140-142)
```python
from omniagents.remote_proxy import RemoteAgentProxy
token = os.getenv(spec.remote_auth_token_key) if spec.remote_auth_token_key else None
base_agent = RemoteAgentProxy(spec.remote_ws_url, token)
```

#### Streaming Execution (lines 226-232)
```python
if spec.remote_ws_url and hasattr(agent, "run_streamed"):
    # Bypass Runner and delegate streaming to RemoteAgentProxy
    result = agent.run_streamed(new_input, run_config=run_config)
else:
    result = Runner.run_streamed(
        agent, new_input, max_turns=spec.max_turns, run_config=run_config
    )
```

### Integration Points to Migrate

1. **Agent Creation**
   - Replace direct Agent() instantiation with `create_unified_agent()`
   - This will return either LocalAgentRpcAdapter or RemoteAgentProxy

2. **SafeAgent Wrapping**
   - Move SafeAgent logic into LocalAgentRpcAdapter
   - The adapter should handle safe agent configuration internally

3. **Streaming Interface**
   - Both LocalAgentRpcAdapter and RemoteAgentProxy implement `run_streamed()`
   - No changes needed to streaming event handling

4. **Session Management**
   - LocalSession uses SQLite for persistence
   - This is separate from server's session management
   - Can keep as-is for CLI mode

5. **Tool Approval Callbacks**
   - Currently passed to SafeAgent constructor
   - Need to pass to LocalAgentRpcAdapter instead

### Dependencies to Consider

1. **MCP Server Context**
   - Currently passed directly to Agent constructor
   - LocalAgentRpcAdapter needs to handle MCP servers

2. **Artifact Callback**
   - Set globally via `set_artifact_callback()`
   - Should work unchanged

3. **Model Settings**
   - Currently created based on tool availability
   - Should be handled by LocalAgentRpcAdapter

## Migration Strategy

### Step 1: Update LocalAgentRpcAdapter
Add support for:
- MCP servers
- Model settings
- Safe agent configuration in constructor

### Step 2: Replace Agent Creation
Change lines 139-183 to use:
```python
agent = await create_unified_agent(
    spec=spec,
    settings=settings,
    approval_callback=approval_callback if spec.use_safe_agent else None
)
```

### Step 3: Simplify Streaming Logic
Since both adapters support `run_streamed()`, remove the conditional:
```python
result = agent.run_streamed(new_input, run_config=run_config)
```

### Step 4: Update Tests
- Test local agent creation via adapter
- Test remote agent compatibility
- Test safe agent functionality
- Test MCP integration

## Benefits of Migration

1. **Consistency**: All agent operations go through RPC interface
2. **Simplification**: Remove duplicate agent construction logic
3. **Future-proof**: Easy to add new agent types
4. **Testing**: Can mock RPC interface for better testing

## Risks and Mitigations

1. **MCP Integration**
   - Risk: LocalAgentRpcAdapter doesn't support MCP
   - Mitigation: Add MCP support before migration

2. **Performance**
   - Risk: In-memory RPC adds overhead
   - Mitigation: Profile and optimize if needed

3. **Backward Compatibility**
   - Risk: Breaking existing CLI behavior
   - Mitigation: Comprehensive testing

## Next Steps

1. Enhance LocalAgentRpcAdapter with missing features
2. Create migration branch
3. Implement changes incrementally
4. Test each change thoroughly
5. Update documentation