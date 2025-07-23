# Server Mode Migration Analysis

## Current Server Architecture

### Overview
The server backend provides a JSON-RPC/WebSocket interface for remote agent execution. It's already well-structured with AgentService handling RPC methods, but it still uses `Runner.run_streamed` directly instead of the unified agent architecture.

### Key Components

#### 1. Server App (`server/app.py`)
- Creates FastAPI application
- Sets up WebSocket endpoint at `/ws`
- Creates AgentService instance
- Registers JsonRpcEndpoint for handling RPC calls
- Already uses AgentService (from Phase 1)

#### 2. AgentService (`core/agent_service.py`)
- Handles all RPC methods (start_run, stop_run, etc.)
- Manages sessions and history
- Builds agents using spec.build_agent or _default_build_agent
- **Key Issue**: Still builds agents directly instead of using unified factory

#### 3. Runner Bridge (`server/runner_bridge.py`)
- **Critical Component**: Uses `Runner.run_streamed` directly
- Converts stream events to JSON-RPC notifications
- Handles WebSocket disconnections gracefully
- Manages event broadcasting to multiple channels

#### 4. Session Management (`server/sessions.py`)
- Tracks active sessions and running tasks
- Stores conversation history
- Handles multiple concurrent WebSocket connections
- Manages pending events for disconnected clients

### Current Agent Building (AgentService lines 152-180)
```python
if not hasattr(sess, "agent"):
    stub_app = SimpleNamespace(
        spec=self.spec,
        _request_tool_approval=lambda name, args: self._request_tool_approval(name, args, _sess=sess),
    )
    
    if getattr(self.spec, "build_agent", None):
        builder = self.spec.build_agent
        if inspect.iscoroutinefunction(builder):
            agent = await builder({}, None, stub_app)
        else:
            agent = builder({}, None, stub_app)
    else:
        agent = await _default_build_agent(
            {}, None, self.spec,
            stub_app._request_tool_approval if self.spec.use_safe_agent else None
        )
    sess.agent = agent
```

### Current Streaming (runner_bridge.py lines 101-157)
```python
async def stream_agent(agent, prompt: str, session, rpc_channel, *, max_turns: int = 20) -> None:
    # ... session handling ...
    
    run_config = RunConfig()
    result = Runner.run_streamed(agent, agent_input, max_turns=max_turns, run_config=run_config)
    
    async for event in result.stream_events():
        # Convert events to JSON-RPC notifications
        # Broadcast to all connected channels
        # Handle disconnections gracefully
```

## Key Challenges for Migration

### 1. Streaming Architecture Difference
- **Current**: Direct `Runner.run_streamed` → stream_events() → WebSocket notifications
- **Unified**: agent.run_streamed() → RPC events → stream_events()
- **Challenge**: Adding RPC layer might introduce unnecessary overhead

### 2. Session Management Integration
- Sessions are tightly coupled with WebSocket channels
- Tool approval callbacks need session context
- Multiple concurrent connections per session

### 3. Event Broadcasting
- Current system broadcasts to multiple WebSocket channels
- Handles disconnections and queues pending events
- Need to preserve this functionality

### 4. Tool Approval Flow
- Tool approval requests are sent as RPC notifications
- Responses come back via RPC method calls
- Session-specific approval handling

## Migration Options

### Option 1: Full Unified Agent (Recommended)
Use `create_unified_agent` factory and adapt streaming:

**Pros:**
- Consistent with CLI/Flet modes
- Reuses existing LocalAgentRpcAdapter
- Future-proof for new agent types

**Cons:**
- Double RPC layer (WebSocket RPC → In-memory RPC)
- Potential performance overhead
- Complex event flow

### Option 2: Partial Migration
Keep direct agent usage but use unified factory for creation:

**Pros:**
- Minimal performance impact
- Simpler event flow
- Less risky change

**Cons:**
- Inconsistent with other modes
- Doesn't fully leverage unified architecture
- Technical debt remains

### Option 3: Custom Server Adapter
Create a ServerAgentAdapter that implements UnifiedAgent but optimizes for server use:

**Pros:**
- Optimized for server streaming
- Consistent interface
- No double RPC layer

**Cons:**
- More code to maintain
- Duplicates some LocalAgentRpcAdapter logic

## Recommended Approach: Option 1 with Optimizations

### Phase 1: Migrate to Unified Factory
1. Update AgentService to use `create_unified_agent`
2. Adapt `stream_agent` to work with unified agents
3. Handle the double-streaming scenario

### Phase 2: Optimize Event Flow
1. Create direct streaming path for local agents
2. Bypass unnecessary RPC serialization
3. Maintain WebSocket notification structure

### Phase 3: Preserve Features
1. Ensure session management works
2. Maintain tool approval flow
3. Keep multi-channel broadcasting

## Implementation Considerations

### 1. Streaming Adaptation
The unified agent returns a result with `stream_events()` method. We need to:
- Call `agent.run_streamed()` instead of `Runner.run_streamed()`
- Convert the unified agent's events to WebSocket notifications
- Preserve all existing event types and payloads

### 2. Session Context
- Pass session_id to unified agent
- Maintain session.agent reference
- Handle approval callbacks with session context

### 3. Performance Impact
- Profile the double RPC overhead
- Consider optimization if significant
- May need direct path for local agents

### 4. Backward Compatibility
- Maintain existing WebSocket API
- No changes to client-side code
- Preserve all event formats

## Testing Strategy

1. **Unit Tests**
   - Mock unified agent in stream_agent tests
   - Verify event conversion
   - Test session management

2. **Integration Tests**
   - Full server with unified agents
   - WebSocket connection handling
   - Multi-client scenarios

3. **Performance Tests**
   - Measure streaming latency
   - Compare with current implementation
   - Identify bottlenecks

## Migration Steps

1. **Update AgentService**
   - Import create_unified_agent
   - Replace agent building logic
   - Store unified agent in session

2. **Adapt stream_agent**
   - Use agent.run_streamed()
   - Convert event stream
   - Maintain broadcasting logic

3. **Handle Edge Cases**
   - Tool approval with unified agents
   - Session persistence
   - Error handling

4. **Test Thoroughly**
   - All existing functionality
   - Performance characteristics
   - Multi-client scenarios