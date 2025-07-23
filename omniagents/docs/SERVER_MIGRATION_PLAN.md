# Server Mode Migration Plan

## Overview
Migrate the server backend to use the unified agent architecture while preserving all existing functionality, including WebSocket streaming, session management, and tool approval flows.

## Migration Strategy: Minimal Risk Approach

We'll implement Option 1 from the analysis (Full Unified Agent) but with careful attention to preserving the existing streaming behavior.

## Step-by-Step Implementation Plan

### Step 1: Update AgentService Agent Creation

#### 1.1 Update Imports
```python
# In agent_service.py, add:
from omniagents.core.agent_factory import create_unified_agent
```

#### 1.2 Replace Agent Building Logic
Replace lines 152-180 in `agent_service.py` with:

```python
if not hasattr(sess, "agent"):
    # Create approval callback that captures session context
    approval_callback = None
    if self.spec.use_safe_agent:
        approval_callback = lambda name, args: self._request_tool_approval(name, args, _sess=sess)
    
    # Build unified agent using factory
    sess.agent = await create_unified_agent(
        spec=self.spec,
        settings={},  # Server doesn't use settings from UI
        approval_callback=approval_callback,
        mcp_servers=None  # Server doesn't support MCP yet
    )
```

### Step 2: Adapt stream_agent Function

#### 2.1 Update runner_bridge.py
The key challenge is that unified agents return a different streaming interface. We need to adapt it:

```python
async def stream_agent(agent, prompt: str, session, rpc_channel, *, max_turns: int = 20) -> None:
    """Drive agent.run_streamed and forward events to *rpc_channel*."""
    
    # Build input preserving session history
    agent_input = session.history + [{"role": "user", "content": prompt}]
    
    # Persist user message
    user_msg = {"role": "user", "content": prompt}
    session.history.append(user_msg)
    append_message(session.id, user_msg)
    
    # Get the stream result from unified agent
    stream_result = agent.run_streamed(agent_input)
    
    # Stream events - unified agent provides async generator
    async for event in stream_result.stream_events():
        # The unified agent events need to be converted to our format
        # This depends on the exact event structure
        await process_unified_event(event, session, rpc_channel)
```

#### 2.2 Event Conversion
We need to map unified agent events to the existing WebSocket notification format:

```python
async def process_unified_event(event, session, rpc_channel):
    """Convert unified agent event to WebSocket notifications."""
    run_id = asyncio.current_task().get_name()
    
    # Map event types (this needs to match actual unified agent events)
    if event.type == "run_item_stream_event":
        item = event.item
        method, payload = _stream_event_payload(item, run_id)
        if method:
            delivered = await _broadcast(session, method, payload)
            if not delivered:
                session.pending_events.append((method, payload))
        
        # Persist to history
        if hasattr(item, "to_input_item"):
            try:
                inp = item.to_input_item()
                if inp:
                    session.history.append(inp)
                    append_message(session.id, inp)
            except Exception:
                pass
```

### Step 3: Handle Special Cases

#### 3.1 Remote Agents
The server might receive specs with remote_ws_url. The unified factory already handles this:
- RemoteAgentProxy will be created automatically
- No special handling needed

#### 3.2 Custom Build Agents
Specs with custom build_agent functions are handled by LocalAgentRpcAdapter:
- The adapter wraps the custom builder
- Maintains compatibility

#### 3.3 Tool Approval
The approval callback is passed to create_unified_agent:
- SafeAgent wrapping happens inside the factory
- Approval flow remains unchanged

### Step 4: Testing Plan

#### 4.1 Unit Tests
Create `test_server_unified.py`:
1. Mock create_unified_agent
2. Test agent creation in AgentService
3. Test streaming with mock unified agent
4. Verify event conversion

#### 4.2 Integration Tests
1. Start server with test agent
2. Connect WebSocket client
3. Send messages and verify responses
4. Test tool approval flow
5. Test session management

#### 4.3 Performance Testing
1. Measure baseline performance (current)
2. Measure with unified agents
3. Compare latency and throughput
4. Optimize if needed

### Step 5: Optimization (If Needed)

If performance testing shows significant overhead:

#### Option A: Direct Streaming for Local Agents
```python
# In LocalAgentRpcAdapter
def run_streamed_direct(self, messages, session, rpc_channel):
    """Optimized streaming for server mode."""
    # Bypass RPC and stream directly
    # This would require refactoring
```

#### Option B: Server-Specific Adapter
Create a ServerAgentAdapter that:
- Implements UnifiedAgent protocol
- Optimizes for server streaming
- Avoids double RPC layer

## Implementation Timeline

### Day 1: Core Changes
1. Update AgentService to use create_unified_agent
2. Basic adaptation of stream_agent
3. Get simple messages working

### Day 2: Event Handling
1. Complete event conversion logic
2. Ensure all event types work
3. Test tool calls and results

### Day 3: Testing
1. Write comprehensive tests
2. Test all edge cases
3. Performance profiling

### Day 4: Optimization
1. Address any performance issues
2. Polish and cleanup
3. Documentation

## Risk Mitigation

### 1. Gradual Rollout
- Add feature flag to toggle unified agents
- Test extensively before removing flag
- Keep old code path temporarily

### 2. Event Compatibility
- Log all events during testing
- Compare with current implementation
- Ensure no client changes needed

### 3. Performance Monitoring
- Add timing logs
- Monitor in production
- Have optimization plan ready

## Success Criteria

1. **Functional Parity**: All existing features work identically
2. **No Client Changes**: WebSocket API remains unchanged
3. **Performance**: Latency within 10% of current
4. **Code Quality**: Cleaner, more maintainable
5. **Test Coverage**: Comprehensive test suite

## Rollback Plan

If issues arise:
1. The changes are isolated to AgentService and runner_bridge
2. Can revert to direct agent building
3. No database or client changes to undo

## Next Steps

1. Review and approve plan
2. Create feature branch
3. Implement Step 1 (AgentService changes)
4. Test basic functionality
5. Continue with remaining steps