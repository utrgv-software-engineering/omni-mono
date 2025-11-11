# Testing Coverage Improvement Issues

This document contains well-scoped GitHub issues for improving test coverage in OmniAgents, organized by priority.

## ⚠️ CRITICAL: Tests Should Discover Bugs, Not Lock Them In

**Before implementing any tests, understand this principle:**

Tests must probe the **intended behavior** of the code, not just verify what it currently does. When a test fails, you MUST investigate whether it's:
1. A bug in the implementation (code needs fixing)
2. A bug in the test (wrong assumptions)
3. A combination of both

**Do NOT** make tests pass by blindly accepting current behavior. Tests should help discover bugs, not preserve them.

Each issue below includes specific guidance on testing intent for that module. See `docs/TESTING_GUIDE.md` section 2.4 for detailed principles and examples.

---

## Issue 1: Add integration tests for GEPA agent adapter evaluation workflow

**Priority**: Critical
**Labels**: testing, evaluation, coverage
**Estimated Effort**: Medium (4-6 hours)

### Description

The `OmniAgentsAgentAdapter` class in `omniagents/core/eval/gepa_agent_adapter.py` has only 7% test coverage. This adapter is critical for agent optimization in the evaluation framework but lacks integration tests for its core evaluation workflow.

### Current State

- Current coverage: **7%** (476 lines, 434 missed)
- Existing tests only mock the adapter in `test_optimize.py`
- No tests for actual agent execution, scoring, or evaluation flow

### What Needs Testing

According to `docs/TESTING_GUIDE.md`, we should test this through **agent integration tests** (primary testing layer) focusing on the evaluation pipeline.

**Integration Tests Needed:**

1. **Full evaluation workflow** (`evaluate()` method):
   - Test agent execution with instruction proposals
   - Verify scenario execution and measure collection
   - Test evaluation result persistence (trajectories, scores, outputs)
   - Mock OpenAI API calls, use real config/tool loading

2. **Reflective dataset generation** (`make_reflective_dataset()` method):
   - Test TP/TN/FP/FN categorization from trajectories
   - Verify markdown dataset formatting
   - Test with real evaluation results

3. **Error handling scenarios**:
   - Agent execution failures
   - Tool execution errors during evaluation
   - Missing instruction prompts
   - Invalid compiled YAML configs

**Unit Tests Needed** (for complex logic only):

1. **Score calculation** (`_score_from_measures()` method):
   - Test "fraction" mode scoring (partial credit)
   - Test "all-or-nothing" mode scoring
   - Edge cases: empty measures, all pass, all fail

2. **Tool schema extraction** (`_get_tools_schema_md()` method):
   - Test markdown generation from tool schemas
   - Test with various tool types (function tools, MCP tools)

3. **Fenced code extraction** (`_extract_fenced()` method):
   - Test extraction from ```json blocks
   - Test extraction from ```yaml blocks
   - Test plain text handling (no fences)

### Acceptance Criteria

- [ ] Integration test: Full `evaluate()` workflow with mocked OpenAI, real tools
- [ ] Integration test: `make_reflective_dataset()` with real trajectories
- [ ] Integration test: Error handling for execution failures
- [ ] Unit test: Score calculation for both modes (fraction, all-or-nothing)
- [ ] Unit test: Tool schema markdown generation
- [ ] Unit test: Fenced code extraction
- [ ] Coverage for `gepa_agent_adapter.py` increases to at least **60%**

### Testing Approach

Follow `docs/TESTING_GUIDE.md` principles:
- Use real agent instances with mocked OpenAI API
- Use real YAML configs (minimal test configs)
- Use real SQLite sessions (temporary databases)
- Focus on testing through the evaluation interface
- Keep setup minimal

### Files to Modify

- `tests/integration/core/eval/test_gepa_agent_adapter.py` (new file)
- `tests/unit/core/eval/test_gepa_agent_adapter.py` (new file)

### Example Test Structure

```python
# tests/integration/core/eval/test_gepa_agent_adapter.py

def test_agent_adapter_evaluates_scenarios_with_measures(tmp_path, mock_openai):
    """Integration test: Full evaluation workflow"""
    # Create minimal compiled YAML config
    config_yaml = tmp_path / "agent.yaml"
    config_yaml.write_text("""
    name: test-agent
    instructions: "You help with tasks"
    tools: ["calculator"]
    evaluation:
      failure_categories:
        arithmetic:
          - accuracy
    """)

    # Create adapter
    adapter = OmniAgentsAgentAdapter(
        compiled_yaml=config_yaml,
        category="arithmetic",
        score_mode="fraction"
    )

    # Mock OpenAI to return success
    mock_openai.responses = [...]

    # Run evaluation
    result = adapter.evaluate(
        task_prompt="What is 2+2?",
        scenario_id="test-001",
        ...
    )

    # Verify results
    assert result.output is not None
    assert result.score >= 0.0
    assert len(result.trajectory) > 0
```

---

## Issue 2: Add integration tests for GEPA judge adapter evaluation workflow

**Priority**: Critical
**Labels**: testing, evaluation, coverage
**Estimated Effort**: Medium (4-6 hours)

### Description

The `OmniAgentsJudgeAdapter` class in `omniagents/core/eval/gepa_judge_adapter.py` has only 4% test coverage. This adapter is critical for judge optimization but lacks tests for its core evaluation and scoring logic.

### Current State

- Current coverage: **4%** (647 lines, 611 missed)
- Existing tests only mock the adapter
- No tests for judge execution, label prediction, or TP/TN/FP/FN scoring

### What Needs Testing

**Integration Tests Needed:**

1. **Full judge evaluation workflow** (`evaluate()` method):
   - Test judge agent building with TypedDict output schema
   - Verify label prediction from judge outputs
   - Test TP/TN/FP/FN scoring matrix
   - Test with real agent run data from runs file

2. **Judge agent building** (`_build_judge_agent()` method):
   - Test TypedDict creation from output schema
   - Verify template rendering with context variables
   - Test with different judge configurations

3. **Reflective dataset generation** (`make_reflective_dataset()` method):
   - Test TP/TN/FP/FN example selection
   - Verify markdown dataset formatting
   - Test scoring reward/penalty application

**Unit Tests Needed:**

1. **Label prediction** (`_predict_label_from_judge_output()` method):
   - Test boolean extraction from various formats
   - Test JSON parsing from fenced code blocks
   - Test TypedDict output parsing
   - Edge cases: malformed outputs, missing fields

2. **Scoring matrix calculation**:
   - TP (true positive): reward = 1.0
   - TN (true negative): reward = 0.0
   - FP (false positive): penalty applied
   - FN (false negative): penalty applied

3. **Template rendering** with agent metadata:
   - Test Jinja template rendering with judge context
   - Test with various judge configurations

### Acceptance Criteria

- [ ] Integration test: Full `evaluate()` workflow with judge execution
- [ ] Integration test: Judge agent building with TypedDict
- [ ] Integration test: `make_reflective_dataset()` with TP/TN/FP/FN examples
- [ ] Unit test: Label prediction from various output formats
- [ ] Unit test: Scoring matrix (TP/TN/FP/FN) calculations
- [ ] Unit test: Template rendering with judge metadata
- [ ] Coverage for `gepa_judge_adapter.py` increases to at least **60%**

### Testing Approach

- Use real judge agent instances with mocked OpenAI API
- Use real runs file data (JSON format)
- Test through judge evaluation interface
- Focus on label prediction accuracy and scoring correctness

### Files to Modify

- `tests/integration/core/eval/test_gepa_judge_adapter.py` (new file)
- `tests/unit/core/eval/test_gepa_judge_adapter.py` (new file)

### Example Test Structure

```python
# tests/unit/core/eval/test_gepa_judge_adapter.py

def test_predict_label_from_json_output():
    """Unit test: Label extraction from JSON"""
    output = '```json\n{"correct": true}\n```'
    label = OmniAgentsJudgeAdapter._predict_label_from_judge_output(output)
    assert label is True

def test_predict_label_from_boolean():
    """Unit test: Direct boolean extraction"""
    assert OmniAgentsJudgeAdapter._predict_label_from_judge_output(True) is True
    assert OmniAgentsJudgeAdapter._predict_label_from_judge_output(False) is False
```

---

## Issue 3: Add comprehensive tests for SafeAgent approval workflow

**Priority**: High
**Labels**: testing, safe-agent, coverage
**Estimated Effort**: Medium (4-6 hours)

### Description

The `SafeAgent` class in `omniagents/core/agents/safe.py` has only 49% test coverage despite being a critical security feature. Many important workflows like handoff wrapping, pattern matching, and approval metadata are completely untested.

### Current State

- Current coverage: **49%** (215 lines, 90 missed)
- Only 2 basic tests exist (rejection and always approve)
- No tests for handoffs, patterns, metadata, tool use behavior

### What Needs Testing

**Integration Tests Needed (Critical):**

1. **Full approval workflow with RPC**:
   - Test tool approval request sent via RPC
   - Verify approval/rejection handling
   - Test halt on rejection behavior
   - Test "always approve" flag propagation

2. **Handoff agent wrapping** (`_wrap_handoffs()` method):
   - Test approval callback propagation to handoff agents
   - Verify safe tools inherited by handoff agents
   - Test nested handoffs

3. **Skip approvals mode**:
   - Test that `skip_approvals=True` bypasses approval
   - Verify all tools execute without prompting

4. **Multi-tool execution with halt**:
   - Test execution stops after rejection
   - Verify partial tool execution results

**Unit Tests Needed:**

1. **Safe tool pattern matching** (`is_tool_safe()` method):
   - Test exact name matching
   - Test regex pattern matching
   - Test global vs instance-level safe tools

2. **Tool use behavior modes** (`_custom_tool_use_behavior()` method):
   - Test `run_llm_again` mode
   - Test `stop_on_first_tool` mode
   - Test `StopAtTools` dict mode

3. **Approval metadata computation** (`_compute_approval_metadata()` method):
   - Test metadata generation for different tool types
   - Test preview rendering
   - Test with/without approval metadata decorators

4. **Safe tool management**:
   - `add_safe_tool()` - instance level
   - `add_safe_tool_pattern()` - regex validation
   - `remove_safe_tool()` - removal logic
   - `clear_safe_tools()` - clearing all
   - `set_global_safe_tools()` - global configuration
   - `add_global_safe_tool()` - global addition

### Acceptance Criteria

- [ ] Integration test: Full approval workflow via RPC with tool execution
- [ ] Integration test: Handoff agent wrapping and callback propagation
- [ ] Integration test: Skip approvals mode end-to-end
- [ ] Integration test: Multi-tool execution with halt on rejection
- [ ] Unit test: Pattern matching (exact + regex)
- [ ] Unit test: Tool use behavior modes (all 3 modes)
- [ ] Unit test: Approval metadata computation
- [ ] Unit test: Safe tool management (add/remove/clear)
- [ ] Coverage for `safe.py` increases to at least **80%**

### Testing Approach

- Test through RPC protocol for approval flows (integration)
- Use real SafeAgent instances with mocked OpenAI
- Test tool execution with real tool implementations
- Unit test complex logic (pattern matching, metadata)

### Files to Modify

- `tests/integration/core/agents/test_safe_agent_approval.py` (new file)
- `tests/unit/core/agents/test_safe_agent.py` (expand existing)

### Example Test Structure

```python
# tests/integration/core/agents/test_safe_agent_approval.py

async def test_safe_agent_requests_approval_via_rpc(mock_openai, mock_rpc_client):
    """Integration test: Full approval workflow through RPC"""
    # Create safe agent
    agent = SafeAgent(
        name="safe",
        tools=[bash_tool],
        instructions="Help with tasks",
        approval_callback=lambda name, args, meta: (True, False, None)
    )

    # Mock OpenAI to call bash tool
    mock_openai.tool_calls = [{"name": "bash", "arguments": {"command": "ls"}}]

    # Execute
    response = await agent.run("List files")

    # Verify approval was requested
    assert mock_rpc_client.requests[0]["function"] == "ui.request_tool_approval"
    assert mock_rpc_client.requests[0]["params"]["tool_name"] == "bash"
```

---

## Issue 4: Add integration tests for evaluation scenarios execution workflow

**Priority**: High
**Labels**: testing, evaluation, coverage
**Estimated Effort**: Small (2-3 hours)

### Description

The `scenarios.py` module in `omniagents/core/eval/scenarios.py` has 51% coverage. While scenario generation is well tested, the critical `run_scenarios_against_agent()` function that executes scenarios against agents is completely untested.

### Current State

- Current coverage: **51%** (444 lines, 208 missed)
- Scenario generation is tested
- **No tests for scenario execution workflow**

### What Needs Testing

**Integration Tests Needed:**

1. **Full scenario execution** (`run_scenarios_against_agent()` function):
   - Test agent execution against multiple scenarios
   - Verify workflow tracing integration
   - Test history inclusion option
   - Test progress callback invocation
   - Test error handling during agent runs

2. **Scenario execution with tracing**:
   - Verify traces are captured correctly
   - Test trace metadata (scenario_id, dimensions, etc.)

3. **Error handling**:
   - Test agent execution failures
   - Verify error recording in results
   - Test partial execution (some scenarios fail)

**Unit Tests Needed:**

1. **Combination enumeration** (`_enumerate_combos()` method):
   - Test with various dimension filters
   - Test deduplication logic

2. **Scenario ID computation**:
   - Test ID generation and uniqueness
   - Test deterministic IDs for same dimensions

### Acceptance Criteria

- [ ] Integration test: `run_scenarios_against_agent()` with full workflow
- [ ] Integration test: Tracing integration verification
- [ ] Integration test: Error handling during execution
- [ ] Integration test: Progress callback invocation
- [ ] Unit test: Combination enumeration with filters
- [ ] Unit test: Scenario ID computation
- [ ] Coverage for `scenarios.py` increases to at least **75%**

### Testing Approach

- Test through full scenario execution (integration)
- Use real agent instances with mocked OpenAI
- Use real scenario configurations
- Verify tracing and error handling

### Files to Modify

- `tests/integration/core/eval/test_scenarios_execution.py` (new file)
- `tests/unit/core/eval/test_scenarios.py` (expand existing)

---

## Issue 5: Add unit tests for RPC server error handling

**Priority**: Medium
**Labels**: testing, rpc, coverage
**Estimated Effort**: Small (2-3 hours)

### Description

The `server.py` module in `omniagents/rpc/server.py` has 66% coverage. While integration tests exist for basic RPC functionality, error handling and edge cases are untested.

### Current State

- Current coverage: **66%** (90 lines, 27 missed)
- Integration tests cover basic RPC calls
- **No tests for error handling, malformed messages, concurrent connections**

### What Needs Testing

**Unit Tests Needed:**

1. **JsonRpcChannel error handling**:
   - Test `send_result()` with transport errors
   - Test `send_error()` payload construction
   - Test `async_call()` timeout handling

2. **JsonRpcEndpoint message handling**:
   - Test malformed JSON parsing
   - Test missing "jsonrpc" field
   - Test notification handling (no "id" field)
   - Test method not found errors

3. **Debug logging**:
   - Verify debug output when `DEBUG_SERVER=true`
   - Test log message formatting

**Integration Tests Needed:**

1. **Concurrent connections**:
   - Test multiple WebSocket connections
   - Verify connection isolation

2. **Connection cleanup**:
   - Test cleanup on disconnect
   - Verify resource cleanup

### Acceptance Criteria

- [ ] Unit test: JsonRpcChannel error handling (send errors)
- [ ] Unit test: JsonRpcEndpoint malformed message handling
- [ ] Unit test: Notification handling (no id)
- [ ] Unit test: Debug logging verification
- [ ] Integration test: Concurrent connections
- [ ] Integration test: Connection cleanup
- [ ] Coverage for `server.py` increases to at least **85%**

### Testing Approach

- Unit tests for error handling and message parsing
- Integration tests for connection lifecycle
- Use real WebSocket connections for integration tests

### Files to Modify

- `tests/unit/rpc/test_server.py` (new file)
- `tests/integration/test_server_rpc.py` (expand existing)

---

## Issue 6: Add unit tests for config loader edge cases

**Priority**: Low
**Labels**: testing, config, coverage
**Estimated Effort**: Small (2-3 hours)

### Description

The `loader.py` module in `omniagents/core/config/loader.py` has 74% coverage and is generally well-tested. However, some edge cases around inline agent parsing, theme colors, and complex handoffs remain untested.

### Current State

- Current coverage: **74%** (561 lines, 144 missed)
- Most core functionality is well tested
- Some edge cases and error paths are missing

### What Needs Testing

**Unit Tests Needed:**

1. **Inline agent spec parsing** (`_parse_inline_agent_spec()` method):
   - Test with all possible fields
   - Test nested inline agents
   - Test missing required fields

2. **Theme color parsing** (`_parse_theme_color()` method):
   - Test valid Flet color constants
   - Test invalid color warnings
   - Test hex color codes

3. **Settings fields merging**:
   - Test project defaults merging
   - Test settings field deduplication
   - Test icon map processing

4. **Error messages**:
   - Test missing tools warning
   - Test missing guardrails warning
   - Test invalid YAML syntax errors

### Acceptance Criteria

- [ ] Unit test: Inline agent spec parsing (all fields)
- [ ] Unit test: Theme color parsing edge cases
- [ ] Unit test: Settings fields merging
- [ ] Unit test: Error message verification
- [ ] Coverage for `loader.py` increases to at least **85%**

### Testing Approach

- Focus on unit tests for parsing logic
- Test error messages explicitly
- Use minimal YAML configs

### Files to Modify

- `tests/unit/core/config/test_loader.py` (expand existing)

---

## Issue 7: Add integration tests for evaluation pipeline judge execution

**Priority**: Low
**Labels**: testing, evaluation, coverage
**Estimated Effort**: Small (2-3 hours)

### Description

The `pipeline.py` module in `omniagents/core/eval/pipeline.py` has 76% coverage and is generally well-tested through integration tests. However, some edge cases around multiple judges per scenario and concurrent execution remain untested.

### Current State

- Current coverage: **76%** (430 lines, 81 missed)
- Core pipeline functionality is well tested
- Some edge cases around judge selection and concurrent execution are missing

### What Needs Testing

**Integration Tests Needed:**

1. **Multiple judges per scenario**:
   - Test scenario with multiple judge configurations
   - Verify all judges are executed
   - Test judge result aggregation

2. **Judge selection scenarios**:
   - Test with `judges: auto` (derive from measures)
   - Test with `judges: none` (skip judges)
   - Test with explicit judge list

**Unit Tests Needed:**

1. **Label derivation** (`_catalog_with_labels()` method):
   - Test label extraction from dimension names
   - Test with various naming patterns

2. **Judge key extraction** (`_derive_judge_keys_from_measures()` method):
   - Test key extraction from measure names
   - Test with various measure configurations

### Acceptance Criteria

- [ ] Integration test: Multiple judges per scenario
- [ ] Integration test: Judge selection modes (auto/none/list)
- [ ] Unit test: Label derivation logic
- [ ] Unit test: Judge key extraction
- [ ] Coverage for `pipeline.py` increases to at least **85%**

### Testing Approach

- Integration tests for judge orchestration
- Unit tests for helper functions
- Use real judge configurations

### Files to Modify

- `tests/integration/core/eval/test_pipeline.py` (expand existing)
- `tests/unit/core/eval/test_pipeline.py` (expand existing)

---

## Summary

| Issue | Priority | Module | Current Coverage | Target Coverage | Effort |
|-------|----------|--------|------------------|-----------------|--------|
| 1 | Critical | gepa_agent_adapter.py | 7% | 60% | Medium |
| 2 | Critical | gepa_judge_adapter.py | 4% | 60% | Medium |
| 3 | High | safe.py | 49% | 80% | Medium |
| 4 | High | scenarios.py | 51% | 75% | Small |
| 5 | Medium | server.py | 66% | 85% | Small |
| 6 | Low | loader.py | 74% | 85% | Small |
| 7 | Low | pipeline.py | 76% | 85% | Small |

**Total Estimated Effort**: 17-23 hours

All issues follow the testing philosophy in `docs/TESTING_GUIDE.md`:
- Favor integration tests through agent execution
- Unit tests only for complex logic (scoring, parsing, formatting)
- Use real components (agents, tools, configs) with mocked external APIs
- Keep tests simple with minimal setup
