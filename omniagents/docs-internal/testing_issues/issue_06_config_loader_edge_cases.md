---
title: "Add unit tests for config loader edge cases"
labels: ["testing", "config", "coverage", "priority: low"]
---

## Description

The `loader.py` module in `omniagents/core/config/loader.py` has 74% coverage and is generally well-tested. However, some edge cases around inline agent parsing, theme colors, and complex handoffs remain untested.

## ⚠️ IMPORTANT: Test Intent, Not Current Behavior

**When implementing these tests, you MUST probe the intended behavior, not just verify what the code currently does.**

### When a Test Fails - Investigate Before Fixing

Do NOT immediately change assertions to make tests pass. Instead, ask:
1. **Is the implementation wrong?** (Bug in the code)
2. **Is the test wrong?** (Wrong assumptions)
3. **Is it both?**

### Focus Areas for This Module

For config loader, pay attention to:
- **Validation logic** - Are invalid configs properly rejected with helpful errors?
- **Default merging** - Are project defaults correctly merged with agent-specific settings?
- **Warning messages** - Do warnings actually help users fix their configs?

**Example:**
```python
# ❌ BAD: Accepting unclear error messages
def test_invalid_color():
    result = _parse_theme_color("BADCOLOR")
    assert result == "BADCOLOR"  # What's the user experience here?

# ✅ GOOD: Verifying helpful user feedback
def test_invalid_color_warns_with_helpful_message(caplog):
    """
    Invalid Flet colors should:
    1. Log a warning with the invalid value
    2. Suggest valid color names or formats
    3. Fall back to the input value (user responsibility)

    If this fails:
    - Check if warning message is helpful
    - Verify valid colors are suggested
    - Ensure fallback behavior is documented
    """
    result = _parse_theme_color("INVALID_COLOR")

    # Should warn with helpful message
    assert "Unknown Flet color" in caplog.text
    assert "INVALID_COLOR" in caplog.text

    # Should fall back to input
    assert result == "INVALID_COLOR"
```

See `docs/TESTING_GUIDE.md` section 2.4 for more details.

---

## Current State

- Current coverage: **74%** (561 lines, 144 missed)
- Most core functionality is well tested
- Some edge cases and error paths are missing

## What Needs Testing

### Unit Tests Needed

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

## Acceptance Criteria

- [ ] Unit test: Inline agent spec parsing (all fields)
- [ ] Unit test: Theme color parsing edge cases
- [ ] Unit test: Settings fields merging
- [ ] Unit test: Error message verification
- [ ] Coverage for `loader.py` increases to at least **85%**

## Testing Approach

- Focus on unit tests for parsing logic
- Test error messages explicitly
- Use minimal YAML configs

## Files to Modify

- `tests/unit/core/config/test_loader.py` (expand existing)

## Example Test Structure

```python
# tests/unit/core/config/test_loader.py

def test_parse_inline_agent_spec_with_all_fields():
    """Unit test: Inline agent parsing with all fields"""
    spec = {
        "name": "helper",
        "instructions": "Help the user",
        "tools": ["calculator"],
        "model": "gpt-4",
        "handoffs": []
    }

    result = _parse_inline_agent_spec(spec)

    assert result.name == "helper"
    assert result.instructions == "Help the user"
    assert "calculator" in result.tools

def test_parse_theme_color_invalid_warns(caplog):
    """Unit test: Invalid theme color warnings"""
    result = _parse_theme_color("INVALID_COLOR")

    assert "Unknown Flet color" in caplog.text
    assert result == "INVALID_COLOR"  # Falls back to input

def test_settings_fields_merge_with_project_defaults():
    """Unit test: Settings merging with project defaults"""
    project_settings = {
        "show_tool_results": True,
        "theme": "dark"
    }

    agent_settings = {
        "theme": "light"  # Override
    }

    merged = _merge_settings(project_settings, agent_settings)

    assert merged["show_tool_results"] is True
    assert merged["theme"] == "light"
```

## Estimated Effort

Small (2-3 hours)
