## ⚠️ IMPORTANT: Tests Should Discover Bugs, Not Lock Them In

**Before writing any test, read this carefully:**

When implementing tests for this issue, you MUST probe the **intended behavior** of the functionality, not just verify what the code currently does. Tests should help us discover bugs, not preserve them.

### When a Test Fails

**DO NOT** immediately change the test to make it pass. Instead, investigate:

1. **Is this a bug in the implementation?**
   - Does the code behave incorrectly?
   - Does it violate documented behavior?
   - Does it produce illogical results?

2. **Is this a bug in the test?**
   - Are your test assumptions wrong?
   - Did you misunderstand the intended behavior?
   - Is your test setup incorrect?

3. **Is it both?**
   - Sometimes tests reveal bugs AND the test itself has issues

### How to Test Intent, Not Current Behavior

✅ **DO:**
- Read docstrings, comments, and related code to understand intent
- Reason about what the function/class *should* do logically
- Test edge cases that might expose bugs
- Document your reasoning in test docstrings
- Ask "does this result make sense?" before asserting it

❌ **DON'T:**
- Run the code, see what it returns, and assert that value blindly
- Make tests pass by accepting wrong behavior
- Skip investigating failures
- Assume current behavior is correct

### Example

```python
# ❌ BAD: Locking in current behavior without questioning it
def test_score_from_measures():
    measures = [{"pass": False}, {"pass": False}, {"pass": True}]
    score = adapter._score_from_measures(measures)
    assert score == 0.5  # Just testing what it currently returns!

# ✅ GOOD: Testing intended behavior with reasoning
def test_score_from_measures_fraction_mode():
    """
    In 'fraction' mode, score should be: (passing) / (total)

    Given: 1 pass out of 3 measures
    Expected: 1/3 ≈ 0.333

    If this fails with score=0.5:
    - Check if formula is wrong
    - Check if 'fraction' mode is correctly set
    - Investigate _score_from_measures() implementation
    """
    measures = [{"pass": False}, {"pass": False}, {"pass": True}]
    adapter = OmniAgentsAgentAdapter(..., score_mode="fraction")
    score = adapter._score_from_measures(measures)
    assert score == pytest.approx(0.333, abs=0.01)
```

### Your Responsibility

As the implementer of these tests, you are responsible for:
1. Understanding the intended behavior before writing assertions
2. Investigating test failures thoroughly
3. Reporting bugs you discover in the implementation
4. Documenting ambiguous behavior
5. Making reasoned decisions about correct behavior

**Remember:** Tests are not just about coverage numbers. They're about ensuring correctness.

See `docs/TESTING_GUIDE.md` section 2.4 for more details.
