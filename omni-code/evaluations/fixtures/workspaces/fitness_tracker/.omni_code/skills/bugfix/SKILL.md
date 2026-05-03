---
name: bugfix
description:
  Fix a known bug end-to-end. Use when a bug is described concretely
  enough to act on (a ticket, a stack trace, a user report), or after
  `debug` has identified a root cause.
---

# Bugfix

Take a TDD-style red / green / refactor approach: reproduce, fix, clean
up. The reproduction is the load-bearing artifact — without it you can't
prove the fix actually works.

## 1. Reproduce (red)

Two actions, in this order. Do not collapse them.

### 1a. Write the reproduction

Pick the form that fits the situation:

- **Failing test** — a unit/integration test that captures the bug.
- **Failing script** — a small script (curl, Python snippet, shell
  one-liner) that triggers the bug.
- **MCP / tool call** — invoke the relevant tool with input that exposes
  the bug.
- **Manual run** — start the system, perform the steps.

### 1b. Run it against the current (broken) code, BEFORE writing any fix

Execute the reproduction and observe the wrong behavior with your own
eyes (failing test, wrong script output, wrong tool response). **Do not
write or apply any fix until you have run the reproduction in red state
and seen it fail.**

This is a hard sequencing requirement, not a suggestion. Writing the
test and the fix in successive edits without running the test in
between defeats the entire purpose of step 1 — you have no evidence
that your reproduction actually exercises the bug.

**STOP CONDITION.** If your reproduction does NOT show the bug — your
test passes, your script returns the right answer, the tool gives the
expected output — then your reproduction is wrong, not the code. The
most common cause is inputs that satisfy both the broken and the
correct implementation (e.g. an ordering test where the input order
already matches the expected output regardless of the sort key).
Revise the inputs, re-run, and only move on once the bug is visibly
reproduced.

## 2. Fix (green)

Make the smallest change that turns the reproduction green. Resist
"while I'm here" refactors — the diff should map directly to the fix.

Re-run the reproduction; it should now pass / return the right answer.
Re-run the broader test suite or sanity checks — the fix shouldn't
break anything unrelated.

## 2b. Verify-by-revert (prove the test catches the bug)

Even after step 1b, it's worth proving once more that the regression
test actually catches the broken behavior — temporarily revert the
source fix and re-run. If the test still passes against the broken
code, your test is false-green and you need to rewrite it. This catches
a class of subtle mistakes (test exercises the wrong path, depends on
data that satisfies both old and new behavior, asserts on a tautology)
that step 1b might miss when the test was edited after the first red
observation.

**Pre-flight: only do this if the source file is git-tracked AND the
file's only uncommitted change is your fix.** Otherwise you risk
losing other work or stashing the wrong things.

```bash
# Are we in a git repo?
git rev-parse --is-inside-work-tree

# Is the path tracked? (errors if not)
git ls-files --error-unmatch <source-file>
```

If both succeed, use a path-scoped stash:

```bash
# Stash JUST the fix (the `--` separates the path from flags)
git stash push -- <source-file>

# Re-run the test command — the regression test should fail (red)
<test-command>

# Restore the fix
git stash pop

# Re-run to confirm green again
<test-command>
```

**STOP CONDITION.** If the test passes against the stashed (broken)
state, the reproduction doesn't exercise the bug. Don't paper over
it — rewrite the test until the verify-by-revert step shows red. Then
re-apply, re-verify, move on.

If the workspace isn't a git repo (or other dirty state on the file
prevents a clean stash), don't fake the verification — note in your
summary that verify-by-revert was skipped and explain why. A `cp`
backup + manual revert works in principle but you have no canonical
"broken" version to revert to without git, so the safer move is to
flag the gap.

## 3. Refactor / cleanup

Only after the reproduction is green:

- If the reproduction was a temporary script, decide: convert it into
  a permanent regression test, or delete it (with a note).
- Tidy minor things noticed but deferred — but don't expand scope
  beyond the bug.
- Summarize: what changed, why it fixes the bug, where the regression
  test lives.

## Anti-patterns

- **"My reproduction passes against the broken code."** Most common
  bugfix mistake. Means the reproduction doesn't exercise the bug.
  Never apply a fix on top of a passing reproduction.
- **Skipping reproduction.** "I see the bug from reading the code" is
  not a reproduction. You can be wrong about what's broken; the
  reproduction is what makes you right.
- **Refactoring during the fix.** Mixing scope makes the diff harder
  to review and revert. Refactor as a separate change.
