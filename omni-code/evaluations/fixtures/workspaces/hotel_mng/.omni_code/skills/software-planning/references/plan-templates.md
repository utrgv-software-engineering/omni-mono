# Plan Templates

Output templates for Phase 3 finalization. Use the appropriate template based on the type of change being planned. These are starting points — adapt structure to fit the specific plan.

## Feature Plan Template

```markdown
# [Feature Name]

## Summary
[2-3 sentences: what this adds, why it matters, and the high-level approach.]

## Key Changes
[Group by subsystem or behavior, not file-by-file.]

### [Subsystem/Area 1]
- [Behavior-level description of change]
- [Behavior-level description of change]

### [Subsystem/Area 2]
- [Behavior-level description of change]

## API / Interface Changes
[Only if applicable. Specify signatures, types, contracts.]

- `functionName(param: Type): ReturnType` — [what it does]
- New type: `TypeName { field: Type, ... }` — [purpose]
- New endpoint: `POST /path` — [request/response shape]

## Test Plan
- [Specific test case 1: input -> expected output]
- [Specific test case 2: edge case -> expected behavior]
- [Specific test case 3: failure mode -> expected handling]
- [Integration/E2E scenario if applicable]

## Assumptions
- [Decision made without explicit user input, with rationale]
- [Default chosen for unspecified preference]
```

### Feature Plan Guidelines

- For v1/MVP features, do not invent detailed schema, validation, precedence, fallback, or wire-shape policy unless the request establishes it or it prevents a concrete implementation mistake
- Prefer the intended capability and minimum interface/behavior changes
- Keep scope tight — note what's intentionally deferred

## Bugfix Plan Template

Bugfixes follow a test-driven approach: write a failing test that reproduces the bug *before* designing the fix. The test is the specification of correct behavior; the fix is just "make the test pass."

```markdown
# Fix: [Brief description of the bug]

## Root Cause
[Clear explanation of why the bug occurs. Reference the specific code path.]

## Reproduction Test
[Write this BEFORE the fix. This test must fail on the current code and pass after the fix.]

- **Test framework**: [Name the project's existing test framework, or "standalone script" if none]
- **Test case**: [Exact test — input, action, expected output, actual output]
- [Additional edge-case tests for variants of the same bug]

If the project has no test suite, write a targeted standalone script that:
1. Sets up the minimal conditions to trigger the bug
2. Exercises the buggy code path
3. Asserts the expected behavior (exits non-zero on failure)
4. Can be re-run after the fix to confirm it passes

## Fix Approach
[The minimal change to make the reproduction test pass. Group by subsystem if multiple areas are affected.]

- [Change 1: what to do and why it fixes the issue]
- [Change 2: if applicable]

## Risk Assessment
- **Blast radius**: [What other behavior could be affected by this fix?]
- **Rollback**: [How to revert if the fix causes problems]
- **Confidence**: [High/Medium/Low — based on root cause certainty]

## Assumptions
- [Any assumptions about the intended behavior]
```

### Bugfix Plan Guidelines

- Root cause must be specific — "the bug is in the validation logic" is not specific enough; "the regex on line 42 doesn't handle unicode" is
- **Test first, fix second**: The reproduction test is written before the fix, not after. If you can't write a failing test, the root cause isn't well enough understood.
- Fix approach should be minimal — make the failing test pass, don't refactor the neighborhood
- If the project has no test suite, a standalone reproduction script is mandatory. It should be runnable with a single command and exit non-zero on failure.

## Refactor Plan Template

```markdown
# Refactor: [What's being refactored and why]

## Motivation
[Why this refactor is needed. What pain points it addresses.]

## Current Structure
[Brief description of the current design and its problems.]

## Target Structure
[What the code looks like after the refactor. Focus on the key architectural change.]

### Before
- [Current organization/pattern]

### After
- [New organization/pattern]

## Migration Steps
[Ordered list of changes that can be made incrementally. Each step should leave the codebase in a working state.]

1. [Step 1: what to change and how to verify]
2. [Step 2: what to change and how to verify]
3. [Step 3: what to change and how to verify]

## Rollback
[How to revert the refactor if problems arise. Which steps are independently revertible?]

## Test Plan
- [How to verify behavior is preserved through the refactor]
- [Specific scenarios to test at each migration step]

## Assumptions
- [Scope boundaries — what's intentionally not refactored]
```

### Refactor Plan Guidelines

- Each migration step must leave the codebase in a green (compiling, tests passing) state
- Prefer many small steps over few large ones
- Before/after structure should be concrete enough to implement without judgment calls
- Rollback plan should be realistic — "revert the commits" is fine if the steps are incremental

## Compactness Guidelines

These apply to all plan types:

- **Group by subsystem, not by file**: "Authentication: add token refresh logic" beats "src/auth/token.rs: add refresh(), src/auth/middleware.rs: call refresh(), ..."
- **Mention files only to disambiguate**: When two subsystems have similar names, or when a change is in a non-obvious location
- **3-path rule**: Avoid naming more than 3 file paths unless extra specificity prevents a likely mistake
- **Behavior over symbols**: "Validate input before processing" beats "Add check_input() call before process() in handle_request()"
- **Short bullets**: If a bullet needs a sub-bullet to be clear, the bullet itself isn't clear enough
- **No repeated facts**: Don't restate things discoverable from the repo
- **No filler sections**: Skip Scope, Background, or Rollout sections unless they contain genuinely important information that prevents mistakes
- **Expand on request**: Start compact. If the user asks for more detail on a section, expand just that section
