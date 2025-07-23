# RPC Package Reorganization Plan

## Current Structure

```
omniagents/
  jsonrpc/                    # Low-level JSON-RPC implementation
    __init__.py
    rpc.py                    # JsonRpcClient, JsonRpcEndpoint, RpcMethodsBase
    transport.py              # Transport protocol and implementations
  
  remote_proxy.py             # RemoteAgentProxy (uses jsonrpc)
  core/
    local_agent_adapter.py    # LocalAgentRpcAdapter (uses jsonrpc)
```

## What's in jsonrpc/ Package

### rpc.py
- `RpcMethodsBase` - Base class for defining RPC methods
- `JsonRpcChannel` - Server-side helper for sending notifications
- `JsonRpcEndpoint` - Server-side WebSocket endpoint for FastAPI
- `JsonRpcClient` - Client for making RPC calls

### transport.py
- `Transport` (Protocol) - Abstract transport interface
- `BaseTransport` - Base class with common functionality
- `ClientWebSocketTransport` - WebSocket client transport
- `ServerWebSocketTransport` - FastAPI WebSocket server transport
- `InMemoryTransport` - In-memory transport for local execution
- `TransportError`, `TransportClosedError` - Exceptions

## Proposed New Structure

```
omniagents/
  rpc/                        # All RPC-related code
    __init__.py               # Public API exports
    
    # Core JSON-RPC implementation
    client.py                 # JsonRpcClient
    server.py                 # JsonRpcEndpoint, JsonRpcChannel
    base.py                   # RpcMethodsBase
    
    # Transport layer
    transport/
      __init__.py             # Transport protocol and exceptions
      websocket.py            # ClientWebSocketTransport, ServerWebSocketTransport
      inmemory.py             # InMemoryTransport
    
    # Agent implementations (reference implementations)
    agents/
      __init__.py             # Export RemoteAgent, LocalAgent
      remote.py               # RemoteAgent (formerly RemoteAgentProxy)
      local.py                # LocalAgent (formerly LocalAgentRpcAdapter)
      _events.py              # Shared event handling code
    
    # Protocol documentation
    protocol.md               # Remote Agent Protocol specification
```

## Benefits of This Organization

1. **Logical Grouping**: All RPC-related code in one place
2. **Clear Hierarchy**: 
   - Core RPC mechanics (client.py, server.py)
   - Transport implementations (transport/)
   - Agent reference implementations (agents/)
3. **Better Imports**:
   ```python
   from omniagents.rpc import JsonRpcClient, JsonRpcEndpoint
   from omniagents.rpc.transport import InMemoryTransport, WebSocketTransport
   from omniagents.rpc.agents import RemoteAgent, LocalAgent
   ```

## Migration Steps

### Phase 1: Create New Structure
1. Create `omniagents/rpc/` directory tree
2. Move and split `jsonrpc/rpc.py`:
   - `JsonRpcClient` → `rpc/client.py`
   - `JsonRpcEndpoint`, `JsonRpcChannel` → `rpc/server.py`
   - `RpcMethodsBase` → `rpc/base.py`
3. Move and split `jsonrpc/transport.py`:
   - Base classes and protocol → `rpc/transport/__init__.py`
   - WebSocket transports → `rpc/transport/websocket.py`
   - InMemoryTransport → `rpc/transport/inmemory.py`

### Phase 2: Migrate Agent Implementations
1. Extract shared event code from `remote_proxy.py`
2. Move to `rpc/agents/`:
   - `RemoteAgentProxy` → `rpc/agents/remote.py` as `RemoteAgent`
   - `LocalAgentRpcAdapter` → `rpc/agents/local.py` as `LocalAgent`
   - Shared event code → `rpc/agents/_events.py`

### Phase 3: Update Imports
1. Update all imports throughout codebase
2. Add compatibility imports with deprecation warnings
3. Update tests

### Phase 4: Documentation
1. Write `rpc/protocol.md` documenting the Remote Agent Protocol
2. Update package documentation
3. Add examples

## Import Examples

### Before:
```python
from omniagents.jsonrpc import JsonRpcClient
from omniagents.jsonrpc.transport import InMemoryTransport
from omniagents.remote_proxy import RemoteAgentProxy
from omniagents.core.local_agent_adapter import LocalAgentRpcAdapter
```

### After:
```python
from omniagents.rpc import JsonRpcClient
from omniagents.rpc.transport import InMemoryTransport
from omniagents.rpc.agents import RemoteAgent, LocalAgent
```

## Compatibility

Add temporary compatibility layer:
```python
# omniagents/jsonrpc/__init__.py
import warnings
warnings.warn("omniagents.jsonrpc is deprecated, use omniagents.rpc", DeprecationWarning)
from ..rpc import *  # Re-export everything
```

## Summary

This reorganization:
- Consolidates all RPC code under `omniagents.rpc`
- Provides clear, consistent naming
- Makes the architecture more discoverable
- Sets up a good foundation for documenting the protocol
- Maintains backward compatibility during transition