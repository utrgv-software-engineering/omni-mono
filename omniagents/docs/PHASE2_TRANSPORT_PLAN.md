> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 2: Transport Abstraction Implementation Plan

## Overview
Create a flexible transport layer that abstracts WebSocket communication, allowing the same JSON-RPC interface to work with both remote (WebSocket) and local (in-memory) transports.

## Current State Analysis

### WebSocket Usage Patterns
1. **Server-side (JsonRpcChannel)**:
   - Uses FastAPI's WebSocket directly
   - Methods: `send_json()`, `receive_json()`, `accept()`
   - Handles WebSocketDisconnect exceptions

2. **Client-side (JsonRpcClient)**:
   - Uses `websockets` library (different from FastAPI's WebSocket)
   - Methods: `connect()`, `send()`, `close()`, async iteration
   - Maintains connection lifecycle with context managers

3. **Key Differences**:
   - Server uses `websocket.send_json(dict)` 
   - Client uses `ws.send(json.dumps(dict))`
   - Different exception types and APIs

## Transport Interface Design

### Base Protocol
```python
from typing import Protocol, Any, Dict, Optional, AsyncIterator
import asyncio

class Transport(Protocol):
    """Abstract transport interface for JSON-RPC communication."""
    
    async def connect(self) -> None:
        """Establish connection."""
        ...
    
    async def disconnect(self) -> None:
        """Close connection gracefully."""
        ...
    
    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data through transport."""
        ...
    
    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from transport."""
        ...
    
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        ...
```

### WebSocketTransport Implementation

#### Client-side WebSocketTransport
```python
class ClientWebSocketTransport:
    """WebSocket transport for client-side connections."""
    
    def __init__(self, url: str):
        self.url = url
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
    
    async def connect(self):
        import websockets
        self._ws = await websockets.connect(self.url)
    
    async def disconnect(self):
        if self._ws:
            await self._ws.close()
            self._ws = None
    
    async def send_json(self, data: Dict[str, Any]):
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps(data))
    
    async def receive_json(self) -> Dict[str, Any]:
        if not self._ws:
            raise RuntimeError("Not connected")
        msg = await self._ws.recv()
        return json.loads(msg)
    
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.open
```

#### Server-side WebSocketTransport
```python
class ServerWebSocketTransport:
    """WebSocket transport wrapping FastAPI WebSocket."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self._connected = False
    
    async def connect(self):
        await self.websocket.accept()
        self._connected = True
    
    async def disconnect(self):
        if self._connected:
            await self.websocket.close()
            self._connected = False
    
    async def send_json(self, data: Dict[str, Any]):
        await self.websocket.send_json(data)
    
    async def receive_json(self) -> Dict[str, Any]:
        return await self.websocket.receive_json()
    
    def is_connected(self) -> bool:
        return self._connected
```

### InMemoryTransport Implementation

```python
class InMemoryTransport:
    """In-memory transport for local agent execution."""
    
    def __init__(self):
        self.client_to_server: asyncio.Queue = asyncio.Queue()
        self.server_to_client: asyncio.Queue = asyncio.Queue()
        self._connected = False
        self.remote: Optional['InMemoryTransport'] = None
    
    @classmethod
    def create_pair(cls) -> tuple['InMemoryTransport', 'InMemoryTransport']:
        """Create a connected pair of transports."""
        client = cls()
        server = cls()
        # Cross-wire the queues
        client.remote = server
        server.remote = client
        return client, server
    
    async def connect(self):
        self._connected = True
    
    async def disconnect(self):
        self._connected = False
        # Optionally put sentinel values in queues to unblock receivers
    
    async def send_json(self, data: Dict[str, Any]):
        if not self._connected or not self.remote:
            raise RuntimeError("Not connected")
        # Put in remote's receiving queue
        await self.remote.server_to_client.put(data) if self.remote else None
    
    async def receive_json(self) -> Dict[str, Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        # Get from our receiving queue
        return await self.server_to_client.get()
    
    def is_connected(self) -> bool:
        return self._connected and self.remote is not None
```

## Implementation Steps

### Step 1: Create Transport Protocol and Implementations
1. Create `/omniagents/rpc/transport.py` with:
   - Transport protocol definition
   - ClientWebSocketTransport class
   - ServerWebSocketTransport class
   - InMemoryTransport class
   - TransportError exception class

### Step 2: Update JsonRpcClient
1. Modify constructor to accept Transport instead of URL:
   ```python
   def __init__(self, transport: Transport, methods: Optional[RpcMethodsBase] = None):
       self._transport = transport
       self._methods = methods
   ```

2. Update connection handling:
   ```python
   async def __aenter__(self):
       await self._transport.connect()
       self._recv_task = asyncio.create_task(self._recv_loop())
       return self
   ```

3. Update send/receive methods to use transport

### Step 3: Update JsonRpcChannel
1. Modify to use ServerWebSocketTransport:
   ```python
   def __init__(self, transport: Transport):
       self.transport = transport
   
   async def async_call(self, method: str, args: Optional[Dict[str, Any]] = None):
       payload = {"jsonrpc": "2.0", "method": method, "params": args or {}}
       await self.transport.send_json(payload)
   ```

### Step 4: Update JsonRpcEndpoint
1. Create transport in the handler:
   ```python
   async def handler(websocket: WebSocket):
       transport = ServerWebSocketTransport(websocket)
       await transport.connect()
       channel = JsonRpcChannel(transport)
   ```

### Step 5: Create Transport Factory
```python
class TransportFactory:
    @staticmethod
    def create_client_transport(url: Optional[str] = None) -> Transport:
        if url:
            return ClientWebSocketTransport(url)
        else:
            # For in-memory, we'll need to coordinate with server
            raise NotImplementedError("In-memory client transport requires server coordination")
    
    @staticmethod
    def create_transport_pair() -> tuple[Transport, Transport]:
        """Create connected client/server transport pair for in-memory use."""
        return InMemoryTransport.create_pair()
```

## Migration Strategy

### Phase 2a: Transport Implementation (Days 1-2)
1. Create transport module with all implementations
2. Add comprehensive unit tests for each transport type
3. Test InMemoryTransport bidirectional communication

### Phase 2b: JSON-RPC Integration (Days 3-4)
1. Update JsonRpcClient to use Transport
2. Update JsonRpcChannel to use Transport
3. Update JsonRpcEndpoint to create transports
4. Ensure all existing tests pass

### Phase 2c: Backward Compatibility (Day 5)
1. Add convenience constructors that maintain old API:
   ```python
   @classmethod
   def from_url(cls, url: str, methods=None):
       transport = ClientWebSocketTransport(url)
       return cls(transport, methods)
   ```

2. Update integration tests to use new transport API

## Testing Plan

### Unit Tests
1. **Transport Tests** (`test_transport.py`):
   - Test each transport implementation in isolation
   - Test connection lifecycle
   - Test error handling
   - Test InMemoryTransport pair communication

2. **Integration Tests**:
   - Test JsonRpcClient with each transport type
   - Test JsonRpcChannel with ServerWebSocketTransport
   - Test full round-trip communication

### Test Scenarios
1. Basic send/receive
2. Multiple concurrent messages
3. Connection failures and reconnection
4. Large message handling
5. Bidirectional communication patterns

## Benefits

1. **Abstraction**: Clean separation between transport and RPC logic
2. **Flexibility**: Easy to add new transport types (Unix sockets, TCP, etc.)
3. **Testability**: In-memory transport enables fast, reliable tests
4. **Local Agent Support**: Foundation for running agents in-process with RPC interface
5. **Code Reuse**: Same RPC logic works for both local and remote agents

## Risks and Mitigation

### Risk 1: Breaking Existing Code
- **Mitigation**: Maintain backward compatibility with factory methods
- Keep old constructors as deprecated aliases

### Risk 2: Performance Overhead
- **Mitigation**: InMemoryTransport uses queues with minimal overhead
- No serialization needed for local communication if we enhance later

### Risk 3: Complex State Management
- **Mitigation**: Keep transport interface simple and focused
- Clear connection lifecycle (connect/disconnect)

## Success Criteria

1. All existing tests pass without modification
2. New transport abstraction properly isolates WebSocket dependencies
3. InMemoryTransport enables local agent execution with RPC interface
4. No performance degradation for remote agents
5. Clean, documented API that's easy to extend