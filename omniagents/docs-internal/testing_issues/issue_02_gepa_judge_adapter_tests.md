---
title: "Add integration tests for GEPA judge adapter evaluation workflow"
labels: ["testing", "evaluation", "coverage", "priority: critical"]
---

## Description

The `OmniAgentsJudgeAdapter` class in `omniagents/core/eval/gepa_judge_adapter.py` has only 4% test coverage. This adapter is critical for judge optimization but lacks tests for its core evaluation and scoring logic.

## ⚠️ IMPORTANT: Test Intent, Not Current Behavior

**When implementing these tests, you MUST probe the intended behavior, not just verify what the code currently does.**

### When a Test Fails - Investigate Before Fixing

Do NOT immediately change assertions to make tests pass. Instead, ask:
1. **Is the implementation wrong?** (Bug in the code)
2. **Is the test wrong?** (Wrong assumptions)
3. **Is it both?**

### Focus Areas for This Module

For the judge adapter, pay special attention to:
- **Label prediction logic** - Does `_predict_label_from_judge_output()` correctly extract booleans from various formats?
- **TP/TN/FP/FN scoring** - Are the reward/penalty calculations correct? (TP=1.0, TN=0.0, etc.)
- **JSON parsing** - Does it handle malformed outputs correctly?

**Example:**
```python
# ❌ BAD: Just testing current parsing behavior
def test_predict_label():
    result = _predict_label_from_judge_output('{"correct": true}')
    assert result == True  # What about edge cases?

# ✅ GOOD: Testing intent with edge cases
def test_predict_label_from_json_fenced():
    """
    Should extract boolean from fenced JSON block.
    Tests the parsing logic explicitly.

    If this fails:
    - Check regex pattern for fenced code blocks
    - Verify JSON parsing handles all valid JSON formats
    """
    inputs_and_expected = [
        ('```json\n{"correct": true}\n```', True),
        ('```json\n{"correct": false}\n```', False),
        ('{"correct": true}', True),  # Without fences
        ('true', True),  # Plain boolean
    ]
    for input_val, expected in inputs_and_expected:
        result = _predict_label_from_judge_output(input_val)
        assert result == expected, f"Failed for input: {input_val}"
```

See `docs/TESTING_GUIDE.md` section 2.4 for more details.

---

## Current State

- Current coverage: **4%** (647 lines, 611 missed)
- Existing tests only mock the adapter
- No tests for judge execution, label prediction, or TP/TN/FP/FN scoring

## What Needs Testing

### Integration Tests Needed

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

### Unit Tests Needed

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

## Acceptance Criteria

- [ ] Integration test: Full `evaluate()` workflow with judge execution
- [ ] Integration test: Judge agent building with TypedDict
- [ ] Integration test: `make_reflective_dataset()` with TP/TN/FP/FN examples
- [ ] Unit test: Label prediction from various output formats
- [ ] Unit test: Scoring matrix (TP/TN/FP/FN) calculations
- [ ] Unit test: Template rendering with judge metadata
- [ ] Coverage for `gepa_judge_adapter.py` increases to at least **60%**

## Testing Approach

- Use real judge agent instances with mocked OpenAI API
- Use real runs file data (JSON format)
- Test through judge evaluation interface
- Focus on label prediction accuracy and scoring correctness

## Files to Modify

- `tests/integration/core/eval/test_gepa_judge_adapter.py` (new file)
- `tests/unit/core/eval/test_gepa_judge_adapter.py` (new file)

## Example Test Structure

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

# tests/integration/core/eval/test_gepa_judge_adapter.py

def test_judge_adapter_evaluates_runs_with_scoring(tmp_path, mock_openai):
    """Integration test: Full judge evaluation with TP/TN/FP/FN scoring"""
    # Setup judge config and runs file
    config_yaml = tmp_path / "agent.yaml"
    runs_file = tmp_path / "runs.json"

    config_yaml.write_text("""
    name: test-agent
    evaluation:
      judges:
        accuracy_judge:
          agent:
            name: "Accuracy Judge"
            instructions: "Judge if answer is correct"
            output:
              correct: bool
    """)

    runs_file.write_text('{"runs": [{"scenario_id": "001", "output": "4"}]}')

    # Create adapter
    adapter = OmniAgentsJudgeAdapter(
        compiled_yaml=config_yaml,
        judge_key="accuracy_judge",
        runs_file=runs_file
    )

    # Mock judge response
    mock_openai.responses = [{"correct": True}]

    # Run evaluation
    result = adapter.evaluate(scenario_id="001", label=True)

    # Verify TP scoring
    assert result.score == 1.0  # True positive
```

## Estimated Effort

Medium (4-6 hours)
