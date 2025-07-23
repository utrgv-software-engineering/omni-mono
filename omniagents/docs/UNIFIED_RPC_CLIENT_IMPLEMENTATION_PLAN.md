# Unified RPC Client Implementation Plan

## Overview

This document outlines the correct implementation of the original refactoring vision from `REFACTORING_PLAN.md`. The goal is to create a single unified RPC client that all backends (CLI, Flet, Server) use directly with appropriate transports, eliminating the need for separate proxy classes.

## Current State (What I Did Wrong)

### Architecture I Created
```
Backends → create_unified_agent() → Factory decides:
  - Local: Returns LocalAgentRpcAdapter (wraps AgentService)
  - Remote: Returns RemoteAgentProxy (wraps WebSocket)
```

### Problems with My Implementation
1. Created two separate proxy classes instead of one unified client
2. Backends never see or manage transports
3. Factory pattern hides all the complexity
4. Both proxies duplicate RPC client logic
5. LocalAgentRpcAdapter even imports from RemoteAgentProxy!

## Target Architecture (Original Vision)

### How It Should Work
```
Backends directly manage transport selection:

if spec.remote_ws_url:
    transport = WebSocketTransport(spec.remote_ws_url)
else:
    service = AgentService(spec)
    transport = InMemoryTransport(service)

client = UnifiedRpcClient(transport)
# client provides agent-like interface
```

### Key Principles
1. **One RPC Client**: Single `UnifiedRpcClient` class for all modes
2. **Direct Transport Usage**: Backends explicitly create and manage transports
3. **No Proxy Classes**: Delete both RemoteAgentProxy and LocalAgentRpcAdapter
4. **Transport Determines Behavior**: Local vs remote is just a transport difference

## Implementation Details

### 1. UnifiedRpcClient Design

```python
class UnifiedRpcClient:
    """
    Single RPC client that provides an agent-like interface.
    Works with any transport (WebSocket or InMemory).
    """
    
    # Agent-like properties for compatibility
    name: str
    tools: List[Any]
    instructions: str
    model: str
    model_settings: Dict[str, Any]
    input_type = str
    output_type = str
    
    def __init__(self, transport: Transport, spec: AgentSpec):
        self.transport = transport
        self.spec = spec
        self._rpc = JsonRpcClient(transport, self._create_event_methods())
        self._session_id: Optional[str] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        
        # Set agent properties from spec
        self.name = spec.name
        self.tools = []  # Remote/local agents don't expose tools directly
        self.instructions = spec.instructions
        self.model = spec.model_name
        self.model_settings = {}
    
    async def __aenter__(self):
        await self._rpc.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        await self._rpc.__aexit__(exc_type, exc, tb)
    
    def run_streamed(self, messages: List[Dict[str, str]], *, run_config=None):
        """
        Main agent interface - returns object with .stream_events() method.
        """
        async def _events_gen():
            # Extract prompt from messages
            prompt = messages[-1]["content"] if messages else ""
            
            # Start run via RPC
            response = await self._rpc.other.start_run(
                prompt=prompt, 
                session_id=self._session_id
            )
            
            # Cache session_id if first run
            if not self._session_id and "session_id" in response:
                self._session_id = response["session_id"]
            
            run_id = response.get("run_id")
            
            # Stream events until run_end
            while True:
                event_data = await self._event_queue.get()
                if event_data["method"] == "run_end":
                    if not run_id or event_data["params"].get("run_id") == run_id:
                        break
                
                # Convert RPC notification to StreamEvent
                event = self._map_notification_to_event(
                    event_data["method"], 
                    event_data["params"]
                )
                if event:
                    yield event
        
        # Return object matching Runner interface
        return _StreamResult(_events_gen())
    
    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List available sessions."""
        response = await self._rpc.other.list_sessions()
        return response
    
    async def archive_session(self, session_id: str) -> None:
        """Archive a session."""
        await self._rpc.other.archive_session(session_id=session_id)
    
    def get_all_tools(self) -> List[Any]:
        """For compatibility - unified clients don't expose tools."""
        return []
    
    def reset_run_state(self) -> None:
        """For SafeAgent compatibility - no-op for RPC clients."""
        pass
    
    def _create_event_methods(self):
        """Create RPC methods object that queues events."""
        return _EventMethods(self._event_queue)
    
    def _map_notification_to_event(self, method: str, params: dict):
        """Convert RPC notifications to StreamEvent objects."""
        # This is the same logic currently in remote_proxy.py
        # Should be extracted to a shared module
        ...
```

### 2. Backend Changes

#### CLI Backend (runner.py)
```python
async def run_cli(config_path: str, args=None, spec=None):
    # ... setup code ...
    
    # Create transport based on spec
    if spec.remote_ws_url:
        # Remote agent via WebSocket
        transport = WebSocketTransport(spec.remote_ws_url)
    else:
        # Local agent via in-memory RPC
        service = AgentService(spec)
        # Pass approval callback to service if needed
        if spec.use_safe_agent:
            service.set_approval_callback(cli_approval_callback)
        transport = InMemoryTransport(service)
    
    # Create unified client
    async with UnifiedRpcClient(transport, spec) as agent:
        # Use agent exactly as before
        result = agent.run_streamed(messages, run_config=run_config)
        async for event in result.stream_events():
            # ... handle events ...
```

#### Flet Backend (app_base.py)
```python
async def _create_or_update_agent(self):
    # Create transport
    if self.spec.remote_ws_url:
        transport = WebSocketTransport(self.spec.remote_ws_url)
    else:
        service = AgentService(self.spec)
        if self.spec.use_safe_agent:
            service.set_approval_callback(self._request_tool_approval)
        transport = InMemoryTransport(service)
    
    # Create client
    self.agent_instance = UnifiedRpcClient(transport, self.spec)
    await self.agent_instance.__aenter__()
```

### 3. AgentService Updates

The AgentService needs minor updates to support approval callbacks:

```python
class AgentService(RpcMethodsBase):
    def __init__(self, spec: AgentSpec):
        super().__init__()
        self.spec = spec
        self._approval_callback = None
        self._pending_approvals = {}
    
    def set_approval_callback(self, callback):
        """Set the approval callback for safe agent mode."""
        self._approval_callback = callback
    
    async def _request_tool_approval(self, tool_name: str, formatted_args: str):
        """Request approval using the configured callback."""
        if not self._approval_callback:
            return True, False, None  # Auto-approve if no callback
        
        approval_id = uuid.uuid4().hex
        
        # Notify client about approval request
        await self.channel.async_call("tool_approval_request", {
            "approval_id": approval_id,
            "tool": tool_name,
            "arguments": formatted_args,
        })
        
        # For local mode, directly call the callback
        if hasattr(self._approval_callback, '__call__'):
            return await self._approval_callback(tool_name, formatted_args)
        
        # For remote mode, wait for RPC response
        fut = asyncio.get_event_loop().create_future()
        self._pending_approvals[approval_id] = fut
        return await fut
```

### 4. Shared Event Mapping

Extract the event mapping logic to a shared module:

```python
# omniagents/core/event_mapping.py

from agents import RunItemStreamEvent

def map_notification_to_event(method: str, params: dict):
    """Convert RPC notification to StreamEvent."""
    if method == "tool_called":
        item = ToolCallItem(
            params.get("tool"), 
            params.get("input", "{}"), 
            params.get("call_id")
        )
        return RunItemStreamEvent("tool_called", item)
    
    elif method == "tool_result":
        item = ToolCallOutputItem(
            params.get("tool"), 
            params.get("output", ""), 
            params.get("call_id")
        )
        return RunItemStreamEvent("tool_output", item)
    
    elif method == "message_output":
        item = MessageOutputItem(params.get("content", ""))
        return RunItemStreamEvent("message_output_created", item)
    
    elif method == "tool_approval_request":
        return RunItemStreamEvent("tool_approval_request", params)
    
    return None

# Also move the item wrapper classes here
class ToolCallItem:
    # ... implementation ...

class ToolCallOutputItem:
    # ... implementation ...

class MessageOutputItem:
    # ... implementation ...
```

## Migration Steps

### Phase 1: Create UnifiedRpcClient
1. Create `omniagents/core/unified_rpc_client.py`
2. Implement the UnifiedRpcClient class
3. Extract shared event mapping to `omniagents/core/event_mapping.py`
4. Write comprehensive tests

### Phase 2: Update AgentService
1. Add approval callback support to AgentService
2. Update local session management integration
3. Ensure all RPC methods work correctly

### Phase 3: Migrate CLI Backend
1. Update CLI to create transports directly
2. Replace `create_unified_agent()` with transport + UnifiedRpcClient
3. Test both local and remote modes
4. Ensure all features work (approvals, sessions, etc.)

### Phase 4: Migrate Flet Backend
1. Update Flet to create transports directly
2. Replace factory usage with direct client creation
3. Test all UI functionality
4. Verify settings management works

### Phase 5: Migrate Server Backend
1. Update server to use UnifiedRpcClient for its own agent operations
2. Ensure WebSocket endpoint still works
3. Test server mode thoroughly

### Phase 6: Cleanup
1. Delete `create_unified_agent()` factory function
2. Delete `LocalAgentRpcAdapter` class
3. Delete `RemoteAgentProxy` class
4. Delete `agent_factory.py` file
5. Update all imports and tests
6. Remove the `UnifiedAgent` protocol (no longer needed)

## Benefits of Correct Implementation

1. **True Unification**: One RPC client for all use cases
2. **Explicit Control**: Backends manage their own transports
3. **Less Code**: Eliminate two proxy classes and factory
4. **Clearer Architecture**: Transport selection is visible and explicit
5. **Easier Testing**: Can test client with mock transports
6. **Better Separation**: Transport concerns separated from RPC logic

## Testing Strategy

1. **Unit Tests**:
   - UnifiedRpcClient with mock transport
   - Event mapping functions
   - AgentService RPC methods

2. **Integration Tests**:
   - CLI with both transport types
   - Flet with both transport types
   - Server mode functionality

3. **End-to-End Tests**:
   - Full conversation flows
   - Approval workflows
   - Session management

## Risks and Mitigation

1. **Risk**: Breaking existing functionality
   - **Mitigation**: Comprehensive test suite before changes
   - **Mitigation**: Implement in feature branch

2. **Risk**: Performance regression
   - **Mitigation**: Benchmark before and after
   - **Mitigation**: Profile in-memory RPC overhead

3. **Risk**: Approval callback complexity
   - **Mitigation**: Clear separation between local and remote approval handling
   - **Mitigation**: Extensive testing of approval flows

## Conclusion

This plan corrects the mistakes I made in the original implementation. Instead of creating two separate proxy classes and hiding everything behind a factory, we'll implement the original vision: backends directly manage transports and use a single unified RPC client. This results in a cleaner, more maintainable architecture that truly unifies the codebase.