# Naming Consistency Proposal for Agent Implementations

## Current State

We have two classes that implement the agent interface using JSON-RPC:

1. **`RemoteAgentProxy`** (`omniagents/remote_proxy.py`)
   - Connects to remote agents via WebSocket
   - Uses JsonRpcClient with WebSocket transport
   - Located at top level of omniagents package

2. **`LocalAgentRpcAdapter`** (`omniagents/core/local_agent_adapter.py`)
   - Runs local agents through in-memory RPC
   - Uses JsonRpcClient with InMemoryTransport
   - Located in core subpackage

## Problems with Current Naming

1. **Inconsistent terminology**: "Proxy" vs "Adapter"
2. **Different emphasis**: Remote focuses on location, Local includes "Rpc" in name
3. **Package organization**: One at top level, one in core
4. **Confusing for users**: Not clear they serve the same purpose

## What These Classes Actually Do

Both classes:
- Implement the agent interface (run_streamed, list_sessions, etc.)
- Use JSON-RPC protocol internally
- Handle the same events (tool_called, message_output, etc.)
- Provide reference implementations of the "Remote Agent Protocol"

The only difference is transport:
- Remote uses WebSocket to connect to external servers
- Local uses InMemoryTransport to run agents in-process

## Naming Options

### Option 1: Transport-Focused Names
```python
omniagents.rpc.WebSocketAgent      # formerly RemoteAgentProxy
omniagents.rpc.InMemoryAgent       # formerly LocalAgentRpcAdapter
```

### Option 2: Location-Focused Names (Consistent)
```python
omniagents.rpc.RemoteAgent         # formerly RemoteAgentProxy
omniagents.rpc.LocalAgent          # formerly LocalAgentRpcAdapter
```

### Option 3: Client Terminology
```python
omniagents.rpc.RemoteAgentClient   # formerly RemoteAgentProxy
omniagents.rpc.LocalAgentClient    # formerly LocalAgentRpcAdapter
```

### Option 4: RPC-First Names
```python
omniagents.rpc.RpcAgent            # base class or protocol
omniagents.rpc.RemoteRpcAgent      # formerly RemoteAgentProxy
omniagents.rpc.LocalRpcAgent       # formerly LocalAgentRpcAdapter
```

## Recommendation

I recommend **Option 2** with the following structure:

```
omniagents/
  rpc/
    __init__.py         # Exports RemoteAgent, LocalAgent
    remote_agent.py     # RemoteAgent class (WebSocket-based)
    local_agent.py      # LocalAgent class (InMemory-based)
    protocol.md         # Remote Agent Protocol documentation
```

### Why Option 2?

1. **Clear and Simple**: `RemoteAgent` and `LocalAgent` are immediately understandable
2. **Consistent**: Both follow the same pattern
3. **No Redundancy**: Don't need "Rpc" in the name since they're in the rpc package
4. **User-Friendly**: Easy to remember and use

### Implementation Consistency

Along with renaming, we should ensure both classes:

1. **Have the same constructor pattern**:
   ```python
   # Remote
   agent = RemoteAgent(url="ws://...", token="...")
   
   # Local
   agent = LocalAgent(spec=agent_spec, settings={...})
   ```

2. **Share common base functionality** (they already inherit from UnifiedAgentBase)

3. **Use the same event handling code** (currently LocalAgent imports from remote_proxy!)

4. **Have consistent documentation** explaining they're reference implementations

## Migration Plan

1. Create new `omniagents/rpc/` package
2. Move and rename classes:
   - `RemoteAgentProxy` → `omniagents.rpc.RemoteAgent`
   - `LocalAgentRpcAdapter` → `omniagents.rpc.LocalAgent`
3. Extract shared event handling code to `omniagents.rpc._events`
4. Update all imports in the codebase
5. Add deprecation warnings to old locations
6. Document the Remote Agent Protocol

## Benefits

1. **Clarity**: Users immediately understand what each class does
2. **Organization**: Related classes are in the same package
3. **Discoverability**: Easy to find both implementations
4. **Extensibility**: Clear pattern for adding new transports
5. **Documentation**: Natural place for protocol documentation