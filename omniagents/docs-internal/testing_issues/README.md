# Testing Coverage Issues

This directory contains well-scoped GitHub issues for improving test coverage in OmniAgents. Each issue is designed to be completable within a single AI agent context window.

## Overview

Based on the coverage analysis and `docs/TESTING_GUIDE.md`, we've identified 7 areas needing improved test coverage:

| Issue | Priority | Module | Current | Target | Effort |
|-------|----------|--------|---------|--------|--------|
| 01 | Critical | gepa_agent_adapter.py | 7% | 60% | Medium |
| 02 | Critical | gepa_judge_adapter.py | 4% | 60% | Medium |
| 03 | High | safe.py | 49% | 80% | Medium |
| 04 | High | scenarios.py | 51% | 75% | Small |
| 05 | Medium | server.py | 66% | 85% | Small |
| 06 | Low | loader.py | 74% | 85% | Small |
| 07 | Low | pipeline.py | 76% | 85% | Small |

## Issue Files

- `issue_01_gepa_agent_adapter_tests.md` - GEPA agent adapter integration tests
- `issue_02_gepa_judge_adapter_tests.md` - GEPA judge adapter integration tests
- `issue_03_safe_agent_tests.md` - SafeAgent approval workflow tests
- `issue_04_scenarios_execution_tests.md` - Scenario execution integration tests
- `issue_05_rpc_server_error_tests.md` - RPC server error handling tests
- `issue_06_config_loader_edge_cases.md` - Config loader edge case tests
- `issue_07_pipeline_judge_execution.md` - Pipeline judge execution tests

## Creating Issues on GitHub

### Option 1: Using GitHub CLI (Recommended)

You can create these issues automatically using the GitHub CLI:

```bash
# Install GitHub CLI if needed
# brew install gh  # macOS
# See https://cli.github.com/ for other platforms

# Authenticate
gh auth login

# Navigate to project root
cd /Users/ericmartinez/code/omniagents

# Create issues (run from project root)
bash docs/testing_issues/create_issues.sh
```

### Option 2: Manual Creation

1. Navigate to https://github.com/utrgv-software-engineering/omniagents/issues/new
2. Copy the content from each issue file
3. Extract the title from the `title:` field in the frontmatter
4. Extract labels from the `labels:` field
5. Paste the rest as the issue body
6. Create the issue

### Option 3: Using the Script Individually

Create issues one at a time:

```bash
# Issue 01
gh issue create \
  --title "Add integration tests for GEPA agent adapter evaluation workflow" \
  --label "testing,evaluation,coverage,priority: critical" \
  --body-file docs/testing_issues/issue_01_gepa_agent_adapter_tests.md

# Issue 02
gh issue create \
  --title "Add integration tests for GEPA judge adapter evaluation workflow" \
  --label "testing,evaluation,coverage,priority: critical" \
  --body-file docs/testing_issues/issue_02_gepa_judge_adapter_tests.md

# ... and so on
```

## Testing Philosophy

All issues follow the principles in `docs/TESTING_GUIDE.md`:

1. **Favor integration tests** - Test through agent execution/RPC
2. **Unit tests for complex logic only** - Scoring, parsing, formatting
3. **Use real components** - Agents, tools, configs with mocked external APIs
4. **Keep tests simple** - Minimal setup, clear intentions
5. **Focus on business value** - Don't test framework internals
6. **🚨 Test intent, not current behavior** - Tests should discover bugs, not lock them in

### ⚠️ Critical: Tests Should Discover Bugs, Not Lock Them In

**Each issue includes a prominent section about testing intent.** When implementing these tests:

- **Test what the code SHOULD do**, not just what it currently does
- **When a test fails, investigate thoroughly** - Is it a bug in the code, the test, or both?
- **Don't make tests pass blindly** - Resist changing assertions just to get green tests
- **Document your reasoning** - Explain in test docstrings why you expect certain behavior

See the "⚠️ IMPORTANT" section in each issue for specific guidance and examples.

## Recommended Order

We recommend addressing issues in this order:

### Critical Priority (Start Here)
1. Issue 01 - GEPA agent adapter (7% → 60%)
2. Issue 02 - GEPA judge adapter (4% → 60%)

These are the lowest coverage modules and are critical for the evaluation framework.

### High Priority
3. Issue 03 - SafeAgent (49% → 80%)
4. Issue 04 - Scenarios execution (51% → 75%)

### Medium/Low Priority
5. Issue 05 - RPC server (66% → 85%)
6. Issue 06 - Config loader (74% → 85%)
7. Issue 07 - Pipeline (76% → 85%)

## Verification

After implementing tests for an issue:

```bash
# Run tests for specific module
pytest tests/integration/core/eval/test_gepa_agent_adapter.py -v
pytest tests/unit/core/eval/test_gepa_agent_adapter.py -v

# Check coverage for that module
pytest --cov=omniagents.core.eval.gepa_agent_adapter --cov-report=term-missing

# Run all tests
pytest --cov=omniagents --cov-report=term-missing:skip-covered -q
```

## Notes

- Each issue is scoped to be completable in 2-6 hours
- Issues include example test structures
- Follow `docs/TESTING_GUIDE.md` for testing approach
- Use existing test fixtures in `tests/conftest.py`
- See `docs/TESTING_COVERAGE_ISSUES.md` for full analysis
