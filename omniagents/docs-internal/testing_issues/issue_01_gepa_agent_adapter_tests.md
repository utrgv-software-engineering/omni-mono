---
title: "Add integration tests for GEPA agent adapter evaluation workflow"
labels: ["testing", "evaluation", "coverage", "priority: critical"]
---

## Description

The `OmniAgentsAgentAdapter` class in `omniagents/core/eval/gepa_agent_adapter.py` has only 7% test coverage. This adapter is critical for agent optimization in the evaluation framework but lacks integration tests for its core evaluation workflow.

## ⚠️ IMPORTANT: Test Intent, Not Current Behavior

**When implementing these tests, you MUST probe the intended behavior, not just verify what the code currently does.**

### When a Test Fails - Investigate Before Fixing

Do NOT immediately change assertions to make tests pass. Instead, ask:
1. **Is the implementation wrong?** (Bug in the code)
2. **Is the test wrong?** (Wrong assumptions)
3. **Is it both?**

### How to Approach Testing

✅ **DO:**
- Read docstrings and related code to understand intent
- Reason about what *should* happen logically
- Document your reasoning in test docstrings
- Question results that don't make logical sense

❌ **DON'T:**
- Run code, see output, assert that output blindly
- Make tests pass by accepting wrong behavior
- Skip investigating failures

**Example:**
```python
# ❌ BAD: Locking in potentially wrong behavior
def test_score():
    result = adapter._score_from_measures([...])
    assert result == 0.5  # What if this is wrong?

# ✅ GOOD: Testing intended behavior
def test_score_fraction_mode():
    """
    Fraction mode: score = (passing measures) / (total measures)
    Given 1 pass out of 3: should be 0.333

    If this fails, investigate the formula in _score_from_measures()
    """
    measures = [{"pass": False}, {"pass": False}, {"pass": True}]
    adapter = OmniAgentsAgentAdapter(..., score_mode="fraction")
    score = adapter._score_from_measures(measures)
    assert score == pytest.approx(0.333, abs=0.01)
```

See `docs/TESTING_GUIDE.md` section 2.4 for more details.

---

## Current State

- Current coverage: **7%** (476 lines, 434 missed)
- Existing tests only mock the adapter in `test_optimize.py`
- No tests for actual agent execution, scoring, or evaluation flow

## What Needs Testing

According to `docs/TESTING_GUIDE.md`, we should test this through **agent integration tests** (primary testing layer) focusing on the evaluation pipeline.

### Integration Tests Needed

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

### Unit Tests Needed (for complex logic only)

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

## Acceptance Criteria

- [ ] Integration test: Full `evaluate()` workflow with mocked OpenAI, real tools
- [ ] Integration test: `make_reflective_dataset()` with real trajectories
- [ ] Integration test: Error handling for execution failures
- [ ] Unit test: Score calculation for both modes (fraction, all-or-nothing)
- [ ] Unit test: Tool schema markdown generation
- [ ] Unit test: Fenced code extraction
- [ ] Coverage for `gepa_agent_adapter.py` increases to at least **60%**

## Testing Approach

Follow `docs/TESTING_GUIDE.md` principles:
- Use real agent instances with mocked OpenAI API
- Use real YAML configs (minimal test configs)
- Use real SQLite sessions (temporary databases)
- Focus on testing through the evaluation interface
- Keep setup minimal

## Files to Modify

- `tests/integration/core/eval/test_gepa_agent_adapter.py` (new file)
- `tests/unit/core/eval/test_gepa_agent_adapter.py` (new file)

## Example Test Structure

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

## Estimated Effort

Medium (4-6 hours)
