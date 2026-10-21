---
name: software-tickets
description: Drives a single ticket end-to-end through a six-column kanban (Backlog → Planning → Implementation → QA → Review → Done) — workflow applies to any software project (any language, any framework) using this pipeline. Spells out the definition of done per column, the exact task list shape per column, and the order in which columns are walked. Trigger whenever you pick up a ticket from a project tracker (omni-projects MCP, GitHub Issues, Linear, etc.), are operating as a supervisor on ticket-driven work, or AGENTS.md indicates this is a ticket-driven project. Read it end-to-end before doing anything else.
---

# Software Tickets

This skill is the canonical workflow for driving a single ticket through any project that uses the six-column pipeline below. It names the columns, defines done per column, and shows the shape of the task list you should produce in each one. The workflow is project-agnostic; the *examples* in this file are concrete (you'll see Python / pytest / npm / Vite phrasing) but they are illustrative — substitute your project's equivalents and read **AGENTS.md** for the actual commands, frameworks, ports, and conventions for this codebase.

The task tools (`task_create`, `task_list`, `task_get`, `task_update`) and `move_ticket` are how you walk a ticket through the kanban.

## The pipeline

```
Backlog → Planning → Implementation → QA → Review (HUMAN GATE — STOP) → Done
```

Move only one column forward at a time using `move_ticket`. Don't skip stages. The `Review` column is gated for human approval — your terminal state is `Review`. Do not advance to `Done`; that's the human's job.

Add a brief `add_ticket_comment` summarizing what the prior column produced before you advance. That comment is what makes asynchronous handoff possible.

## Backlog

The ticket starts here. Your **first action** is to advance it to `Planning` with `move_ticket`. **Don't read or analyze the codebase while the ticket is still in `Backlog`.** Reading is the planning column's job. Moving the ticket out is a signal — to humans watching the board and to your own discipline — that work has begun.

Don't create any tasks while you're in `Backlog`. There's nothing to decompose yet.

## Planning

**Definition of done before moving to Implementation:**
- You have read the ticket fully — title, description, acceptance criteria — along with any linked tickets, the project / milestone context, and any relevant project pages.
- You have searched the codebase to identify exactly which files, functions, and systems need to change.
- You have followed the `software-planning` skill end-to-end. Apply the `debug` skill if the failure isn't yet localized.
- You have produced a **decision-complete plan** written as a project page via `create_page`. Page name: `Plan: <ticket title>`. The plan is the deliverable for this column.

**No source edits in Planning.** Edits belong in Implementation.

**Plan structure** — organize the page by column, naming for *this* ticket what done means in each:

```markdown
# Plan: <ticket title>

## Summary
One paragraph: what is being delivered, why, and what success looks like.

## Implementation
**Definition of done for this ticket:**
- Concrete bullets: exact file paths, the test that will be added, the source change.
**Allowed scope:**
- The exact files this column will touch.
**Out of scope:**
- What you are explicitly NOT doing.

## QA
**Definition of done for this ticket:**
- The user-facing assertion the acceptance story will verify.
- The seed data the story will create.
**Allowed scope / Out of scope:**
... same shape ...

## Risks and open questions
- Anything that can't be resolved by the plan; flag, don't silently guess.
```

If you discover the plan is wrong mid-execution, **update the page and continue** — don't proceed against a plan you no longer believe in.

**Task list for the Planning column.** When you enter Planning, your *first* tool calls (before reading any source files) are individual `task_create` calls — one per discrete deliverable. Example for a typical bug-fix ticket on a backend-with-frontend project (substitute the equivalents for your stack):

```
task_create(subject="Read ticket fully", activeForm="Reading ticket fully")
task_create(subject="Read source files the ticket points to", activeForm="Reading source files")
task_create(subject="Read software-planning and bugfix skills", activeForm="Reading relevant skills")
task_create(subject="Localize the failure", activeForm="Localizing the failure")
task_create(subject="Write the plan page", activeForm="Writing the plan page")
```

Five separate `task_create` calls — not one task whose description summarizes the work. After they exist, run `task_list` to verify, then mark each `in_progress` immediately before doing it and `completed` immediately after.

A new feature, a refactor, or a perf investigation would substitute different deliverables (e.g. *"Sketch the new module's interface"* for a feature, *"Audit the legacy callers"* for a refactor, *"Capture the baseline trace"* for perf work) — same shape, different verbs. The point is one `task_create` per discrete deliverable, not the specific subjects.

**Common mistake:** collapsing this into one task called "Drive ticket through Planning column" or similar. That is the column-mirror anti-pattern (see below). The task list exists to surface the *individual completions* — one task per column gives the user nothing to watch.

## Implementation

**Definition of done before moving to QA:**
- The planned source changes are delivered. The diff maps directly to the plan's "Implementation" section — no scope creep.
- A regression test was written **before** the source fix and observed to fail against the broken code (the `bugfix` skill's red-then-green pattern). This is non-negotiable — without it you can't prove the test catches the bug.
- The full test suite (`uv run pytest -q`) passes.
- No `TODO`s, debug prints, half-finished branches, or commented-out code in the diff.

**Task list for the Implementation column.** When you enter Implementation, your *first* tool calls are individual `task_create` calls — one per discrete deliverable. Example for a Python project (Rails would substitute *rspec*, JS *vitest/jest*, Go *go test*, etc. — read AGENTS.md for the project's actual test command):

```
task_create(subject="Read the bugfix skill end-to-end", activeForm="Reading the bugfix skill")
task_create(subject="Add the failing regression test", activeForm="Adding the failing regression test")
task_create(subject="Run the test suite and observe the new test fail (red)", activeForm="Running tests, expecting red")
task_create(subject="Apply the source fix", activeForm="Applying the source fix")
task_create(subject="Run the test suite and observe the new test pass (green)", activeForm="Running tests, expecting green")
task_create(subject="Verify-by-revert: stash the fix, re-run tests, expect red, then pop", activeForm="Verifying the test catches the bug by reverting")
task_create(subject="Run the full test suite", activeForm="Running the full test suite")
```

Seven separate `task_create` calls. Use `addBlockedBy` to link them in the order above. The red-before-green rule means task 3 (observe red) blocks task 4 (apply fix); do not write the test and the fix in one batch and then run the test command once. The verify-by-revert task (per the bugfix skill's section 2b) catches false-green tests before the eval does — don't skip it.

For a feature ticket the shape is similar but without the red-before-green: *"Add unit tests for the new behavior"* → *"Implement the behavior"* → *"Run the test suite"*. For a refactor: *"Capture characterization tests for the current behavior"* → *"Apply the refactor"* → *"Run the test suite, all green"*.

**Common mistake:** collapsing this into one task called "Implement the fix and regression test" or similar. That hides the red-then-green sequence and silently violates the bugfix discipline.

## QA

**Definition of done before moving to Review:**
- For changes that affect user-facing features, drive the system from the user's perspective using `webapp-acceptance-runner`. The runner produces a reviewable artifact (run report, screenshots).
- The acceptance run reports **all stories passing**. A failing or pending run blocks QA → Review.
- You verified your own work the way a QA engineer would: edge cases, error paths, consistency.

**Task list for the QA column.** When you enter QA, your *first* tool calls are individual `task_create` calls — one per discrete deliverable. The shape depends on what kind of system you're testing. Example for a web app with a separate frontend + backend (read AGENTS.md for the project's URLs, install command, and how to start the app):

```
task_create(subject="Read the webapp-acceptance-runner skill end-to-end", activeForm="Reading the runner skill")
task_create(subject="Scaffold the runner via install_acceptance.py", activeForm="Scaffolding the runner")
task_create(subject="Set acceptance/config.json baseUrl to the project's frontend URL", activeForm="Setting baseUrl in config")
task_create(subject="Install dependencies per AGENTS.md", activeForm="Installing dependencies")
task_create(subject="Start the app per AGENTS.md (backend + frontend)", activeForm="Starting the app")
task_create(subject="Verify the app is responding at the base URL", activeForm="Probing the base URL")
task_create(subject="Write the user-flow story with screenshot steps", activeForm="Writing the user-flow story")
task_create(subject="Run the acceptance suite", activeForm="Running the acceptance suite")
task_create(subject="Confirm all stories pass in the run report", activeForm="Verifying the run report")
```

Nine separate `task_create` calls. Note that **starting the app is its own task** — the acceptance runner does not start the app for you (same contract as pytest, jest, playwright test). You bring the app up using whatever idiom the project documents in AGENTS.md (Procfile + honcho, docker-compose, two manual `&`'d shell commands, `make dev`, etc.). If the runner can't reach the base URL after its wait timeout, the app isn't up.

The story task explicitly mentions screenshot steps because the run is graded on screenshot evidence on disk — a story that asserts but never captures a screenshot will pass the assertion and fail evidence. Include `screenshot:` step kinds at meaningful checkpoints.

For other system shapes the QA task list looks different: a CLI tool might be *"Write golden-file fixtures"* → *"Run the smoke test"* → *"Diff against goldens"*; a service-only project might be *"Spin up the test environment"* → *"Run the integration suite"* → *"Capture the run log"*. The web-app example above is one common case.

If the runner errors with "base url not reachable", the app isn't running — go back to the start task and check AGENTS.md.

If the story assertion fails, the bug isn't fixed for the case the story checks — go back to Implementation, don't paper over it.

**Common mistake:** collapsing this into one task called "Set up acceptance runner and write story" or similar. The setup steps (install, start app, verify URL, config baseUrl) are silent failure points that need their own done-state.

## Review

**This is your terminal state. Stop here.**

Don't move the ticket past Review. Output the project's terminal sentinel (`[DONE]`) and let the human approve.

Before stopping, do one `add_ticket_comment` summarizing:
- Which columns the ticket walked through and a one-line "what got done" per column
- The plan page id (so the human can review)
- Any deviation from the original plan and why
- Any flagged risk that's still open

## Anti-pattern: tasks that mirror the columns

This is the most common failure mode of agents using a task list alongside a kanban. It shows up in two forms:

**Surface 1 — column-named subjects.** Tasks called "Planning", "Implementation", "QA" (or paraphrases like "Plan the ticket", "Set up acceptance runner and write story"). Each task swallows hours of work and never reaches a meaningful "done" you can mark mid-flight. The dock looks unchanged for the entire column.

**Surface 2 — `activeForm` mutation.** A subtler version: the task subjects look fine ("Implement workouts ordering fix") but the agent uses `task_update(activeForm=...)` to repaint the spinner verb every time the work shifts to a different sub-step. e.g. `activeForm` goes from "Implementing backend fix and regression test" to "Writing failing test then applying backend fix" — meaningfully different work, treated as one task. Same pathology, different field.

The rule for both surfaces: **once a task is created, its `subject` and `activeForm` stay put**. They describe one specific deliverable. If the work changes enough to need a new verb, that's a new deliverable — `task_create` it. The point of the task list is to show *new completions*, not a single task whose label keeps morphing.

If you find yourself wanting to update `subject` or `activeForm` on an existing task to describe a different sub-step, that's the signal: stop, finish or abandon the current task, and create the next one with its own clear subject and verb.

The shapes above (4–6 tasks for Planning, 5–7 for Implementation, 6–8 for QA) anchor the right granularity. If you're sitting at one task per column, you're mirroring the kanban — that's the failure mode.

## When to delegate to other skills

This skill is the orchestrator. Don't reimplement what other skills already cover.

- **Planning column:** read `software-planning` end-to-end. Apply `debug` if the failure isn't localized.
- **Implementation:** read `bugfix` end-to-end before writing the test. The red-before-green discipline is non-negotiable.
- **QA:** read `webapp-acceptance-runner` end-to-end before scaffolding. The skill enumerates the supported step kinds — only use steps from that list.
- **Ad-hoc UI exploration:** `playwright-cli` for one-off browser interactions outside the runner.

When you enter a new column and a relevant skill exists, read its `SKILL.md` end-to-end before doing the column's work. The skill's discipline outranks your training intuition for that domain.
