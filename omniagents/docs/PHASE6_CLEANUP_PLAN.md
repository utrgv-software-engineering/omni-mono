> **Note**: This is a historical document from the OmniAgents refactoring project. 
> For the current architecture, see [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md).

---

# Phase 6: Cleanup and Optimization Plan

## Overview
After completing phases 1-5, we've successfully migrated CLI and Flet modes to use the unified RPC client architecture. However, there are several cleanup opportunities to improve code quality and remove technical debt.

## Cleanup Opportunities

### 1. Unused Imports

#### CLI Backend (`backends/cli/runner.py`)
- **Issue**: Line 10 imports `Runner` but it's no longer used
- **Fix**: Change `from agents import Runner, RunConfig, ItemHelpers` to `from agents import RunConfig, ItemHelpers`

#### LocalAgentRpcAdapter (`core/local_agent_adapter.py`)
- **Issue**: Line 10 imports `Tuple` but it's not used
- **Fix**: Remove `Tuple` from the import statement

### 2. Dead Code

#### TransportFactory (`jsonrpc/transport.py`)
- **Issue**: The `TransportFactory` class is defined but never used anywhere
- **Analysis**: 
  - Only the transport.py file itself references it
  - No other code uses TransportFactory methods
- **Fix**: Remove the entire TransportFactory class

### 3. Deprecated Methods

#### AgentApp.from_agent (`backends/flet/app_base.py`)
- **Issue**: The `from_agent` class method still accepts direct Agent instances, which goes against the unified architecture
- **Current State**: No deprecation warning
- **Fix**: 
  1. Add proper deprecation warning
  2. Plan for removal in future version
  3. Update documentation to discourage its use

### 4. Incomplete Migrations

#### Server Backend (`server/runner_bridge.py`)
- **Issue**: Still uses `Runner.run_streamed` directly instead of unified agent
- **Impact**: Server mode doesn't benefit from the unified architecture
- **Complexity**: High - requires careful migration to maintain WebSocket streaming

#### Examples
- **Issue**: Examples still use old patterns with direct Agent imports
- **Affected Files**:
  - `/examples/basic_python/agent.py`
  - `/examples/safe_agent_python/agent.py`
  - `/examples/remote_agent/server.py`
  - `/examples/coding_agent/tests/test_no_worktree_diffs.py`
- **Fix**: Update examples to use new patterns or mark as legacy

### 5. Code Organization Issues

#### _default_build_agent Usage
- **Issue**: Still imported in multiple places when it should be internal to LocalAgentRpcAdapter
- **Current Usage**:
  - `core/agent_service.py` - imports and uses directly
  - `core/local_agent_adapter.py` - imports and wraps
  - `backends/flet/app_base.py` - references in comments
- **Fix**: Make it private or move to a more appropriate location

#### Duplicate Agent Building Logic
- **Issue**: LocalAgentRpcAdapter's `custom_build_agent` duplicates some logic from `_default_build_agent`
- **Fix**: Refactor to reduce duplication

### 6. Documentation Cleanup

#### Migration Documents
- **Issue**: Phase 1-5 documents in `/docs/` are now historical
- **Fix**: Either:
  1. Move to an archive folder
  2. Add headers indicating these are historical migration documents
  3. Create a summary document of the refactoring

### 7. Type Hints and Code Quality

#### String Type Hints
- **Issue**: Some files use string type hints unnecessarily (e.g., `'AgentSpec'`)
- **Fix**: Clean up for better type checking

#### Missing Type Annotations
- **Issue**: Some methods lack proper return type annotations
- **Fix**: Add comprehensive type hints

### 8. Error Handling and Logging

#### Bare Except Clauses
- **Issue**: Found bare `except:` clause in `backends/flet/app_base.py` line 1483
- **Fix**: Change to specific exception handling or at least `except Exception:`

#### Print Statements vs Logging
- **Issue**: Found 46 print statements across 6 core files
- **Affected Files**:
  - `core/config_loader.py` - 16 occurrences
  - `core/safe_agent.py` - 12 occurrences  
  - `core/tooling.py` - 13 occurrences
  - `core/agent_service.py` - 3 occurrences
  - `core/artifacts.py` - 1 occurrence
  - `core/agent_builder.py` - 1 occurrence
- **Fix**: Replace with proper logging using Python's logging module

### 9. Module Organization

#### Circular Import Issues
- **Issue**: LocalAgentRpcAdapter has imports inside functions to avoid circular dependencies
- **Locations**:
  - Line 97: Imports `_default_build_agent` inside `custom_build_agent` function
  - Line 200: Imports from `remote_proxy` inside `run_streamed` method
- **Root Cause**: Module dependencies are not well organized
- **Fix**: Refactor module structure to eliminate circular dependencies

## Prioritized Cleanup Plan

### Phase 6.1: Quick Wins (Low Risk)
1. Remove unused `Runner` import from CLI runner
2. Remove unused `Tuple` import from LocalAgentRpcAdapter
3. Remove TransportFactory class
4. Add deprecation warning to AgentApp.from_agent
5. Fix bare except clause in app_base.py

### Phase 6.2: Code Organization (Medium Risk)
1. Make `_default_build_agent` private or move it
2. Refactor duplicate agent building logic
3. Update type hints throughout
4. Replace print statements with proper logging
5. Resolve circular import issues in LocalAgentRpcAdapter

### Phase 6.3: Documentation (Low Risk)
1. Archive or annotate migration documents
2. Update README to reflect new architecture
3. Create architecture overview document

### Phase 6.4: Examples Update (Medium Risk)
1. Update examples to use unified agent pattern
2. Mark any legacy examples clearly
3. Add new examples demonstrating best practices

### Phase 6.5: Server Migration (High Risk, Optional)
1. Analyze server streaming requirements
2. Design server migration strategy
3. Implement server unified agent support
4. Maintain backward compatibility

## Implementation Order

1. **Start with Phase 6.1** - Quick wins with immediate benefits
2. **Then Phase 6.3** - Documentation helps future maintainers
3. **Follow with Phase 6.2** - Code organization improves maintainability
4. **Then Phase 6.4** - Examples help users adopt new patterns
5. **Consider Phase 6.5** - Server migration as separate project

## Success Metrics

1. No unused imports in core modules
2. No dead code in transport layer
3. Clear deprecation path for legacy methods
4. Examples use modern patterns
5. Documentation reflects current architecture

## Risks and Mitigations

1. **Risk**: Breaking existing user code
   - **Mitigation**: Use deprecation warnings, not immediate removal

2. **Risk**: Server migration complexity
   - **Mitigation**: Consider as separate future project

3. **Risk**: Example updates confuse users
   - **Mitigation**: Keep legacy examples with clear warnings

## Next Steps

1. Get approval for cleanup plan
2. Implement Phase 6.1 (quick wins)
3. Create PR with initial cleanups
4. Plan subsequent phases based on feedback