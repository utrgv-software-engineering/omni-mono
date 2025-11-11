---
title: "Add integration tests for evaluation scenarios execution workflow"
labels: ["testing", "evaluation", "coverage", "priority: high"]
---

## Description

The `scenarios.py` module in `omniagents/core/eval/scenarios.py` has 51% coverage. While scenario generation is well tested, the critical `run_scenarios_against_agent()` function that executes scenarios against agents is completely untested.

## ⚠️ IMPORTANT: Test Intent, Not Current Behavior

**When implementing these tests, you MUST probe the intended behavior, not just verify what the code currently does.**

### When a Test Fails - Investigate Before Fixing

Do NOT immediately change assertions to make tests pass. Instead, ask:
1. **Is the implementation wrong?** (Bug in the code)
2. **Is the test wrong?** (Wrong assumptions)
3. **Is it both?**

### Focus Areas for This Module

For scenario execution, pay attention to:
- **Tracing integration** - Are traces captured correctly with proper metadata?
- **Error handling** - Do partial failures still record results for successful scenarios?
- **Progress callbacks** - Are they invoked at the right times with correct data?

**Example:**
```python
# ❌ BAD: Not verifying tracing correctness
def test_scenarios_execution():
    results = run_scenarios_against_agent(agent, scenarios)
    assert len(results) == 2  # Just checking count

# ✅ GOOD: Verifying tracing intent
def test_scenarios_execution_captures_traces_with_metadata():
    """
    Each scenario execution should:
    1. Create a trace with scenario_id
    2. Include dimension metadata
    3. Record full conversation history

    If traces are missing metadata, investigate:
    - Is workflow_name passed correctly?
    - Are traces being created before execution?
    - Check tracing integration in run_scenarios_against_agent()
    """
    scenarios = [
        {"scenario_id": "001", "prompt": "Test", "dimensions": {"difficulty": "easy"}},
    ]
    results = run_scenarios_against_agent(
        agent_yaml,
        scenarios,
        workflow_name="test-eval"
    )

    assert len(results) == 1
    trace = results[0]["trace"]
    assert trace["scenario_id"] == "001"
    assert trace["dimensions"]["difficulty"] == "easy"
    assert trace["workflow_name"] == "test-eval"
```

See `docs/TESTING_GUIDE.md` section 2.4 for more details.

---

## Current State

- Current coverage: **51%** (444 lines, 208 missed)
- Scenario generation is tested
- **No tests for scenario execution workflow**

## What Needs Testing

### Integration Tests Needed

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

### Unit Tests Needed

1. **Combination enumeration** (`_enumerate_combos()` method):
   - Test with various dimension filters
   - Test deduplication logic

2. **Scenario ID computation**:
   - Test ID generation and uniqueness
   - Test deterministic IDs for same dimensions

## Acceptance Criteria

- [ ] Integration test: `run_scenarios_against_agent()` with full workflow
- [ ] Integration test: Tracing integration verification
- [ ] Integration test: Error handling during execution
- [ ] Integration test: Progress callback invocation
- [ ] Unit test: Combination enumeration with filters
- [ ] Unit test: Scenario ID computation
- [ ] Coverage for `scenarios.py` increases to at least **75%**

## Testing Approach

- Test through full scenario execution (integration)
- Use real agent instances with mocked OpenAI
- Use real scenario configurations
- Verify tracing and error handling

## Files to Modify

- `tests/integration/core/eval/test_scenarios_execution.py` (new file)
- `tests/unit/core/eval/test_scenarios.py` (expand existing)

## Example Test Structure

```python
# tests/integration/core/eval/test_scenarios_execution.py

def test_run_scenarios_against_agent_with_tracing(tmp_path, mock_openai):
    """Integration test: Full scenario execution with tracing"""
    from omniagents.core.eval.scenarios import run_scenarios_against_agent

    # Create test scenarios
    scenarios = [
        {"scenario_id": "001", "prompt": "What is 2+2?"},
        {"scenario_id": "002", "prompt": "What is 3+3?"}
    ]

    # Create agent config
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text("""
    name: test-agent
    instructions: "Help with math"
    """)

    # Mock OpenAI responses
    mock_openai.responses = ["4", "6"]

    # Run scenarios
    results = run_scenarios_against_agent(
        agent_spec_path=agent_yaml,
        scenarios=scenarios,
        workflow_name="test-eval"
    )

    # Verify execution
    assert len(results) == 2
    assert results[0]["scenario_id"] == "001"
    assert results[1]["scenario_id"] == "002"
    # Verify traces were captured
    assert "trace_id" in results[0]
```

## Estimated Effort

Small (2-3 hours)
