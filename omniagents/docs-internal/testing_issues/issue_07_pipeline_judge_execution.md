---
title: "Add integration tests for evaluation pipeline judge execution"
labels: ["testing", "evaluation", "coverage", "priority: low"]
---

## Description

The `pipeline.py` module in `omniagents/core/eval/pipeline.py` has 76% coverage and is generally well-tested through integration tests. However, some edge cases around multiple judges per scenario and concurrent execution remain untested.

## ⚠️ IMPORTANT: Test Intent, Not Current Behavior

**When implementing these tests, you MUST probe the intended behavior, not just verify what the code currently does.**

### When a Test Fails - Investigate Before Fixing

Do NOT immediately change assertions to make tests pass. Instead, ask:
1. **Is the implementation wrong?** (Bug in the code)
2. **Is the test wrong?** (Wrong assumptions)
3. **Is it both?**

### Focus Areas for This Module

For evaluation pipeline, pay attention to:
- **Judge orchestration** - Are all configured judges executed?
- **Judge selection logic** - Does "auto" mode correctly derive judges from measures?
- **Result aggregation** - Are multiple judge results properly combined?

**Example:**
```python
# ❌ BAD: Not verifying orchestration correctness
def test_multiple_judges():
    results = run_pipeline(config)
    assert len(results) > 0  # Too vague

# ✅ GOOD: Verifying judge orchestration intent
def test_pipeline_executes_all_configured_judges():
    """
    When multiple judges are configured for a scenario,
    all should be executed and results aggregated.

    Expected behavior:
    - Each judge runs independently
    - Results include all judge outputs
    - Measures reference correct judges

    If this fails:
    - Check judge collection logic
    - Verify judge execution isn't short-circuiting
    - Ensure result aggregation includes all judges
    """
    config = create_config_with_judges([
        "judge_accuracy",
        "judge_completeness"
    ])

    results = run_evaluation_pipeline(config)

    scenario_result = results["scenarios"][0]
    assert "judge_accuracy" in scenario_result["judges"]
    assert "judge_completeness" in scenario_result["judges"]
    assert len(scenario_result["judges"]) == 2
```

See `docs/TESTING_GUIDE.md` section 2.4 for more details.

---

## Current State

- Current coverage: **76%** (430 lines, 81 missed)
- Core pipeline functionality is well tested
- Some edge cases around judge selection and concurrent execution are missing

## What Needs Testing

### Integration Tests Needed

1. **Multiple judges per scenario**:
   - Test scenario with multiple judge configurations
   - Verify all judges are executed
   - Test judge result aggregation

2. **Judge selection scenarios**:
   - Test with `judges: auto` (derive from measures)
   - Test with `judges: none` (skip judges)
   - Test with explicit judge list

### Unit Tests Needed

1. **Label derivation** (`_catalog_with_labels()` method):
   - Test label extraction from dimension names
   - Test with various naming patterns

2. **Judge key extraction** (`_derive_judge_keys_from_measures()` method):
   - Test key extraction from measure names
   - Test with various measure configurations

## Acceptance Criteria

- [ ] Integration test: Multiple judges per scenario
- [ ] Integration test: Judge selection modes (auto/none/list)
- [ ] Unit test: Label derivation logic
- [ ] Unit test: Judge key extraction
- [ ] Coverage for `pipeline.py` increases to at least **85%**

## Testing Approach

- Integration tests for judge orchestration
- Unit tests for helper functions
- Use real judge configurations

## Files to Modify

- `tests/integration/core/eval/test_pipeline.py` (expand existing)
- `tests/unit/core/eval/test_pipeline.py` (expand existing)

## Example Test Structure

```python
# tests/integration/core/eval/test_pipeline.py

def test_pipeline_executes_multiple_judges_per_scenario(tmp_path, mock_openai):
    """Integration test: Multiple judges for one scenario"""
    config = tmp_path / "agent.yaml"
    config.write_text("""
    name: test-agent
    evaluation:
      judges:
        judge_1:
          agent:
            instructions: "Judge correctness"
            output:
              correct: bool
        judge_2:
          agent:
            instructions: "Judge completeness"
            output:
              complete: bool
      scenarios:
        - id: "001"
          prompt: "Test"
          measures:
            - correctness: judge_1
            - completeness: judge_2
    """)

    # Run pipeline
    results = run_evaluation_pipeline(config)

    # Verify both judges executed
    assert len(results["001"]["judges"]) == 2
    assert "judge_1" in results["001"]["judges"]
    assert "judge_2" in results["001"]["judges"]

# tests/unit/core/eval/test_pipeline.py

def test_derive_judge_keys_from_measures():
    """Unit test: Judge key extraction"""
    measures = [
        {"correctness": "judge_correct"},
        {"completeness": "judge_complete"}
    ]

    keys = _derive_judge_keys_from_measures(measures)

    assert set(keys) == {"judge_correct", "judge_complete"}
```

## Estimated Effort

Small (2-3 hours)
