> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 3: Unified RPC Client Design

## Overview

This document outlines the design for Phase 3 of the OmniAgents refactoring: creating a unified RPC client that provides a consistent interface for both local and remote agent execution.

## Current State Analysis

### Agent Creation Patterns

1. **CLI Mode** (`backends/cli/runner.py`):
   - Checks `spec.remote_ws_url` to determine local vs remote
   - Creates `RemoteAgentProxy` for remote, or local Agent for local
   - Applies SafeAgent wrapper if configured

2. **Flet Mode** (`backends/flet/app_base.py`):
   - Similar pattern to CLI mode
   - Different approval callback implementation for GUI

3. **Server Mode** (`server/app.py` + `core/agent_service.py`):
   - Always creates local agents via AgentService
   - Manages agents per session

### Key Interfaces

All agents (local and remote) must provide:
- `run_streamed()` - Returns streaming result with `.stream_events()` method
- Properties: `name`, `tools`, `instructions`, `model`, `model_settings`
- Optional: `get_all_tools()`, `reset_run_state()`

## Unified RPC Client Design

### Architecture

```
┌─────────────────────┐
│   UnifiedAgent      │  <-- New unified interface
├─────────────────────┤
│ - run_streamed()    │
│ - list_sessions()   │
│ - archive_session() │
└──────────┬──────────┘
           │
    ┌──────┴───────┐
    │              │
┌───▼────┐    ┌───▼──────┐
│ Local  │    │ Remote   │
│ Agent  │    │ Agent    │
└────────┘    └──────────┘
     │              │
     │         ┌────▼────┐
     │         │ WebSocket│
     │         │Transport │
     │         └─────────┘
     │
┌────▼──────────┐
│InMemoryTransport│
└────────────────┘
```

### Implementation Plan

#### 1. Create UnifiedAgent Interface

```python
# omniagents/core/unified_agent.py
from typing import Protocol, Any, List, Dict, Optional
from agents import RunConfig

class UnifiedAgent(Protocol):
    """Unified interface for both local and remote agents."""
    
    # Required properties (duck-typed compatibility)
    name: str
    tools: List[Any]
    instructions: str
    model: str
    model_settings: Dict[str, Any]
    
    async def run_streamed(self, messages: List[Dict[str, str]], *, run_config: Optional[RunConfig] = None):
        """Run agent with streaming output."""
        ...
    
    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List available sessions."""
        ...
    
    async def archive_session(self, session_id: str) -> None:
        """Archive a session."""
        ...
    
    def get_all_tools(self) -> List[Any]:
        """Get all available tools."""
        ...
    
    def reset_run_state(self) -> None:
        """Reset agent state (SafeAgent compatibility)."""
        ...
```

#### 2. Create LocalAgentRpcAdapter

```python
# omniagents/core/local_agent_adapter.py
class LocalAgentRpcAdapter:
    """Adapts local agents to work through RPC interface."""
    
    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self._agent_service: Optional[AgentService] = None
        self._rpc_client: Optional[JsonRpcClient] = None
        self._transport_pair: Optional[Tuple[Transport, Transport]] = None
        self._endpoint_task: Optional[asyncio.Task] = None
        
    async def __aenter__(self):
        # Create in-memory transport pair
        client_transport, server_transport = InMemoryTransport.create_pair()
        
        # Create and start AgentService with server transport
        self._agent_service = AgentService(self.spec)
        endpoint = JsonRpcEndpoint(self._agent_service)
        
        # Start endpoint in background task
        self._endpoint_task = asyncio.create_task(
            self._run_endpoint(endpoint, server_transport)
        )
        
        # Create RPC client
        self._rpc_client = JsonRpcClient(client_transport)
        await self._rpc_client.__aenter__()
        
        return self
        
    async def _run_endpoint(self, endpoint, transport):
        """Run the RPC endpoint with in-memory transport."""
        await transport.connect()
        channel = JsonRpcChannel(transport)
        endpoint.methods.channel = channel
        
        try:
            while transport.is_connected():
                data = await transport.receive_json()
                # Process RPC call (similar to JsonRpcEndpoint.handler)
                ...
        finally:
            await transport.disconnect()
```

#### 3. Update RemoteAgentProxy

The existing `RemoteAgentProxy` already provides most of what we need. We'll ensure it fully implements the `UnifiedAgent` interface.

#### 4. Create Agent Factory

```python
# omniagents/core/agent_factory.py
async def create_unified_agent(spec: AgentSpec) -> UnifiedAgent:
    """Create a unified agent based on spec configuration."""
    
    if spec.remote_ws_url:
        # Remote agent via WebSocket
        return RemoteAgentProxy(spec.remote_ws_url, spec.remote_token)
    else:
        # Local agent via in-memory RPC
        adapter = LocalAgentRpcAdapter(spec)
        await adapter.__aenter__()
        return adapter
```

### Migration Strategy

1. **Phase 3a**: Implement core components
   - Create UnifiedAgent protocol
   - Implement LocalAgentRpcAdapter
   - Update RemoteAgentProxy for full compatibility
   - Create agent factory

2. **Phase 3b**: Create compatibility layer
   - Ensure all existing agent operations work through unified interface
   - Add session management methods
   - Handle tool approval workflows

3. **Phase 3c**: Testing
   - Unit tests for each component
   - Integration tests for local vs remote execution
   - Performance tests to ensure minimal overhead

### Benefits

1. **Consistency**: Same interface for local and remote agents
2. **Testability**: Easy to mock and test with in-memory transport
3. **Flexibility**: Can switch between local and remote without code changes
4. **Maintainability**: Single code path for all agent operations
5. **Future-proof**: Easy to add new transport types (e.g., HTTP, gRPC)

### Considerations

1. **Performance**: In-memory transport adds minimal overhead for local execution
2. **Backwards Compatibility**: Existing code continues to work during migration
3. **Error Handling**: Consistent error handling across all transports
4. **Session Management**: Abstract session handling for both local and remote
5. **Tool Approval**: Ensure approval workflows work consistently

### Next Steps

1. Implement UnifiedAgent protocol
2. Create LocalAgentRpcAdapter with in-memory RPC
3. Update RemoteAgentProxy for full interface compliance
4. Create comprehensive test suite
5. Begin migrating CLI and Flet modes in subsequent phases