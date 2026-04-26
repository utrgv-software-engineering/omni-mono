---
name: review
description: Code review checklist and improvement suggestions for Python projects
---

# Review Skill

When performing a code review:

1. Check all AGENTS.md conventions are followed
2. Use the **testing** skill to verify test coverage
3. Look for security issues (SQL injection, hardcoded secrets, etc.)
4. Check error handling completeness
5. Verify type hints and docstrings on public APIs

MANDATORY: Use the testing skill as part of any review.

## Output Format
Provide findings as a numbered list with severity (critical / warning / suggestion).
