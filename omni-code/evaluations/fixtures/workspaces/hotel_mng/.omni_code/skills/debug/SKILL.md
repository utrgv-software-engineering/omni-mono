---
name: debug
description:
  Investigate failures by tracing logs, reproducing issues, and isolating root
  causes; use when builds fail, tests break, runs stall, or behavior is
  unexpected.
---

# Debug

## Goals

- Find why a build, test, or runtime behavior is failing or unexpected.
- Isolate the root cause systematically before attempting fixes.
- Produce a clear diagnosis with evidence.

## Log Sources

- CI logs: `gh run view <run-id> --log` for GitHub Actions failures.
- Test output: `npm test -- --verbose 2>&1` for local test failures.
- Build output: `npm run build 2>&1` for compilation errors.
- Git history: `git log --oneline`, `git diff` for recent changes.

## Quick Triage

1. Reproduce the failure: run the failing command and capture full output.
2. Read error messages carefully — identify the exact file, line, and error type.
3. Check recent changes: `git log --oneline -10` and `git diff HEAD~1`.
4. Search for related issues in the codebase.

## Investigation Flow

1. **Locate the failure:**
   - Run the failing command with verbose output if available.
   - Capture stderr and stdout separately if needed.
2. **Establish timeline:**
   - When did it last work? (`git log`, `git bisect` if needed)
   - What changed between then and now?
3. **Classify the problem:**
   - Build error: missing dependency, type error, syntax error.
   - Test failure: assertion mismatch, timeout, flaky test.
   - Runtime error: crash, hang, wrong output.
   - Environment: missing env var, wrong version, missing tool.
   - Stall: process hangs, infinite loop, deadlock.
4. **Narrow the scope:**
   - Can you reproduce with a minimal example?
   - Does it fail in isolation or only in combination?
5. **Capture evidence:**
   - Save key log lines, error messages, stack traces.
   - Record probable root cause and the exact failing stage.

## Commands

```bash
# Check recent changes
git log --oneline -10
git diff HEAD~1

# Run tests with verbose output
npm test -- --verbose 2>&1 | tail -100

# Run build and capture errors
npm run build 2>&1

# Search for error patterns in source
rg -n "ERROR_PATTERN" src/

# Check CI run logs (replace <run-id>)
gh run list --branch "$(git branch --show-current)" --limit 5
gh run view <run-id> --log

# Bisect to find the breaking commit
git bisect start
git bisect bad HEAD
git bisect good <last-known-good-sha>
# Then: git bisect run npm test

# Check environment
node --version
npm --version
```

## Notes

- Prefer `rg` over `grep` for speed.
- Always reproduce before fixing — don't guess at the cause.
- If the fix is non-obvious, explain the root cause in the commit message.
- Check rotated/archived logs before concluding data is missing.
- For flaky tests, run multiple times to confirm flakiness before dismissing.
