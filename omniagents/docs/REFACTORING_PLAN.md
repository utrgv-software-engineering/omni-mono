# OmniAgents JSON-RPC Refactoring Plan

## Overview

This document outlines the plan to refactor OmniAgents to centralize all agent operations around a JSON-RPC interface, eliminating code duplication and creating a cleaner, more maintainable architecture.

## Current State

### Architecture
All three modes (CLI, Flet, Server) support both local and remote agents:

```
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────┐
│       CLI Mode          │     │       Flet Mode         │     │Server Mode  │
│ ┌─────────┬───────────┐ │     │ ┌─────────┬───────────┐ │     │             │
│ │Local    │Remote     │ │     │ │Local    │Remote     │ │     │ JSON-RPC    │
│ │Agent    │Agent      │ │     │ │Agent    │Agent      │ │     │  Endpoint   │
│ │(SDK)    │(Proxy)    │ │     │ │(SDK)    │(Proxy)    │ │     │             │
└─┴─────────┴───────────┴─┘     └─┴─────────┴───────────┴─┘     └─────────────┘
         │                                │                              ↑
         └────────────────────────────────┴──── WebSocket ──────────────┘
                                          (when remote)
```

### Problems
1. **Code Duplication**: CLI and Flet modes duplicate agent handling, session management, and tool approval logic
2. **Inconsistent Behavior**: Each mode implements features slightly differently
3. **Maintenance Burden**: Changes must be made in multiple places
4. **Testing Complexity**: Each mode needs separate tests

## Target Architecture

### Unified Design
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  CLI Mode   │     │  Flet Mode  │     │ Future UIs  │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                      ┌─────┴─────┐
                      │ RpcClient │
                      └─────┬─────┘
                            │
                  ┌─────────┴─────────┐
                  │    Transport      │
                  │ ┌───────┬───────┐ │
                  │ │Memory │Socket │ │
                  │ └───────┴───────┘ │
                  └─────────┬─────────┘
                            │
                    ┌───────┴───────┐
                    │ AgentService  │
                    └───────────────┘
```

### Key Components

#### 1. **AgentService**
Central service exposing all agent operations via JSON-RPC:
- `start_run(prompt, session_id)` - Start agent execution
- `stop_run(run_id)` - Cancel active run
- `get_session_history(session_id)` - Retrieve messages
- `list_sessions()` - Get all sessions
- `archive_session(session_id)` - Archive a session
- `tool_approval_response(...)` - Handle tool approvals

#### 2. **Transport Abstraction**
```python
class Transport(Protocol):
    async def send(self, message: dict) -> None
    async def receive(self) -> dict
    def on_notification(self, handler: Callable) -> None
```

Two implementations:
- **InMemoryTransport**: For local agent execution
- **WebSocketTransport**: For remote agent connection

#### 3. **Unified RpcClient**
Single client interface used by all backends, automatically selecting transport based on configuration.

## Implementation Phases

### Prerequisites ✅
- Comprehensive test coverage (28 tests covering all critical paths)
- Clear understanding of current architecture
- Identified code duplication and pain points

### Phase 1: Extract AgentService (Week 1)

**Goal**: Create central service with all agent operations

**Tasks**:
1. Create `omniagents/core/agent_service.py`
2. Extract all RPC methods from `server/app.py` into AgentService
3. Update server to use AgentService
4. Ensure all server tests pass

**Deliverables**:
- AgentService class with all RPC methods
- Updated server using AgentService
- All existing tests passing

### Phase 2: Transport Abstraction (Week 1-2)

**Goal**: Create flexible transport layer

**Tasks**:
1. Define Transport protocol in `omniagents/rpc/transport.py`
2. Implement WebSocketTransport (extract from JsonRpcClient)
3. Implement InMemoryTransport for local execution
4. Create transport tests

**Deliverables**:
- Transport interface defined
- Two transport implementations
- Transport-specific tests

### Phase 3: Unified RPC Client (Week 2)

**Goal**: Single client interface for all modes

**Tasks**:
1. Create `omniagents/rpc/client.py` with unified RpcClient
2. Update client to use Transport abstraction
3. Test with both transports
4. Update integration tests

**Deliverables**:
- Unified RpcClient implementation
- Updated tests using new client
- RemoteAgentProxy migration plan

### Phase 4: Migrate CLI Mode (Week 3)

**Goal**: CLI uses RpcClient for all agent operations

**Tasks**:
1. Update CLI backend to create appropriate transport:
   ```python
   if spec.remote_ws_url:
       transport = WebSocketTransport(spec.remote_ws_url)
   else:
       service = AgentService(spec)
       transport = InMemoryTransport(service)
   client = RpcClient(transport)
   ```
2. Replace direct OpenAI SDK usage with RpcClient calls
3. Remove duplicate session management
4. Update CLI tests

**Deliverables**:
- CLI fully migrated to RpcClient
- All CLI features working (local and remote)
- CLI tests updated and passing

### Phase 5: Migrate Flet Mode (Week 3-4)

**Goal**: Flet uses RpcClient for all agent operations

**Tasks**:
1. Update Flet backend similar to CLI
2. Preserve all UI functionality
3. Remove direct agent handling code
4. Update Flet tests

**Deliverables**:
- Flet fully migrated to RpcClient
- Both local and remote modes working
- All UI features preserved
- Flet tests updated and passing

### Phase 6: Cleanup and Optimization (Week 4)

**Goal**: Remove old code and optimize

**Tasks**:
1. Delete RemoteAgentProxy (replaced by transport selection)
2. Remove duplicate code from backends
3. Consolidate session management
4. Update documentation
5. Performance testing and optimization

**Deliverables**:
- Clean codebase with no duplication
- Updated documentation
- Performance benchmarks
- Final test suite

## Success Criteria

### Functional
- ✅ All existing features work identically
- ✅ Local and remote modes supported in CLI/Flet
- ✅ No user-facing changes required
- ✅ All tests pass

### Technical
- ✅ Single implementation of agent logic
- ✅ No code duplication between modes
- ✅ Clear separation of concerns
- ✅ Improved test coverage
- ✅ Better error handling

### Performance
- ✅ No performance degradation
- ✅ Reduced memory usage (single agent instance)
- ✅ Faster development cycle

## Risk Mitigation

### Approach
1. **Incremental Changes**: Each phase independently testable
2. **Feature Flags**: Can toggle between old/new implementation
3. **Comprehensive Testing**: 28+ tests ensure safety
4. **Backward Compatibility**: Keep old code until new proven

### Rollback Plan
- Git tags at each phase completion
- Feature flags to revert behavior
- Old code kept in separate branches
- Clear rollback instructions

## Benefits

### Immediate
- Eliminates code duplication
- Consistent behavior across modes
- Easier debugging and maintenance
- Better test coverage

### Long-term
- Easy to add new UI backends
- Simple to extend functionality
- Clear architecture for contributors
- Better performance characteristics

## Configuration Changes

No changes required for end users. The same configuration options work:
- `remote_ws_url` - Still determines local vs remote
- `remote_auth_token_key` - Still used for authentication
- All other settings preserved

## Timeline

- **Week 1**: Phases 1-2 (AgentService extraction, Transport abstraction)
- **Week 2**: Phase 3 (Unified RPC Client)
- **Week 3**: Phase 4-5 (CLI and Flet migration)
- **Week 4**: Phase 6 (Cleanup and optimization)

Total: 4 weeks to complete refactoring

## Next Steps

1. Review and approve this plan
2. Create feature branch for refactoring
3. Begin Phase 1: Extract AgentService
4. Set up daily progress tracking
5. Schedule weekly architecture reviews