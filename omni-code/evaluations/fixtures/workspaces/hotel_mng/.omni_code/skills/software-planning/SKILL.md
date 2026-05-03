---
name: software-planning
description: Structured software planning for implementation tasks. Triggers on requests to plan, design, architect, or spec out features, refactors, bugfixes, or system changes. Produces decision-complete plans ready for direct handoff to an implementer.
---

# Software Planning

## Overview

This skill provides a three-phase conversational planning workflow that produces decision-complete implementation plans. A decision-complete plan is one where the implementer does not need to make any judgment calls — every ambiguity has been resolved, every tradeoff decided, every interface specified.

The workflow moves through three phases: ground in the environment, clarify intent, then design the implementation. Each phase has clear entry/exit criteria and distinct activities.

## Core Workflow

### Phase 1: Ground in the Environment (explore first, ask second)

Begin by grounding yourself in the actual codebase and system. Eliminate unknowns by discovering facts, not by asking the user. Resolve all questions that can be answered through exploration or inspection.

**What to do:**
- Read relevant files, configs, schemas, types, entrypoints, and manifests
- Search for existing patterns, conventions, and related implementations
- Inspect the current implementation shape before forming opinions
- Run non-mutating commands (tests, builds, type checks) to validate understanding

**What not to do:**
- Do not ask questions that can be answered from the repo or system
- Do not guess at project structure when you can look
- Do not skip exploration because the user seems to know what they want

Perform at least one targeted exploration pass before asking the user anything, unless there are obvious ambiguities or contradictions in the prompt itself that cannot be resolved by exploring.

> See [references/exploration-strategies.md](references/exploration-strategies.md) for detailed checklists of what to explore per plan type and in what order.

### Phase 2: Intent Chat (clarify what they actually want)

Keep asking until you can clearly state all of the following:

- **Goal**: What outcome does the user want?
- **Success criteria**: How will they know it works?
- **Audience**: Who will use or maintain this?
- **Scope**: What is in scope and out of scope?
- **Constraints**: Performance, compatibility, timeline, dependencies
- **Current state**: What exists today and what's wrong with it?
- **Key preferences/tradeoffs**: Where the user's judgment matters

Bias toward questions over guessing: if any high-impact ambiguity remains, do NOT plan yet — ask.

### Phase 3: Implementation Chat (design the spec)

Once intent is stable, keep refining until the spec is decision-complete:

- **Approach**: High-level strategy and rationale
- **Interfaces**: APIs, schemas, I/O contracts, type signatures
- **Data flow**: How data moves through the system
- **Edge cases and failure modes**: What can go wrong and how to handle it
- **Testing and acceptance criteria**: What to test and what "passing" looks like
- **Migration/compatibility**: Breaking changes, rollback strategy, deprecation path

Only finalize when every decision is made and the plan could be handed to a competent implementer with zero follow-up questions.

## Two Kinds of Unknowns

This is the key heuristic for deciding whether to explore or ask. Every unknown falls into one of two categories:

### 1. Discoverable Facts (repo/system truth) — explore first

These are things that have a ground-truth answer in the codebase or system. Before asking the user:

- Run targeted searches and check likely sources of truth (configs, manifests, entrypoints, schemas, types, constants)
- Ask only if: multiple plausible candidates exist, nothing was found but you need a missing identifier/context, or the ambiguity is actually about product intent
- When asking, present concrete candidates (paths, service names, identifiers) and recommend one
- Never ask questions you can answer from the environment (e.g., "where is this struct?", "which framework does this use?")

### 2. Preferences and Tradeoffs (not discoverable) — ask early

These are intent or implementation preferences that cannot be derived from exploration:

- Provide 2-4 mutually exclusive options with a recommended default
- If unanswered, proceed with the recommended option and record it as an assumption in the final plan
- Examples: naming conventions for new APIs, strictness of validation, migration strategy, feature flag rollout approach

## Question Discipline

Every question you ask must meet at least one of these criteria:

1. It would **materially change** the spec or plan
2. It **confirms or locks** an important assumption
3. It **chooses between meaningful tradeoffs**
4. It **cannot be answered** by non-mutating exploration

Structure questions well:
- Offer 2-4 meaningful multiple-choice options when possible
- Do not include filler choices that are obviously wrong
- Always include a recommendation
- Group related questions together rather than asking one at a time

> See [references/requirements-elicitation.md](references/requirements-elicitation.md) for question frameworks and tradeoff templates.

## Read-Only Discipline

During planning, stay read-only. This means:

**Allowed (non-mutating, plan-improving):**
- Reading or searching files, configs, schemas, types, manifests, and docs
- Static analysis, inspection, and repo exploration
- Dry-run style commands that do not edit tracked files
- Tests, builds, or checks that write only to caches or build artifacts

**Not allowed (mutating, plan-executing):**
- Editing or writing files
- Running formatters or linters that rewrite files
- Applying patches, migrations, or codegen
- Side-effectful commands whose purpose is to carry out the plan

The rationale: mutations during planning create partially-implemented states that are hard to reason about and hard to undo. Planning and execution are separate activities. If an action would be described as "doing the work" rather than "planning the work," do not do it.

**Note:** This is advisory guidance. Unlike a runtime-enforced plan mode, this skill relies on the agent's discipline. The rationale above explains why it matters.

## Finalization

Only output the final plan when it is **decision-complete** and leaves no decisions to the implementer.

### Decision-Complete Criteria

A plan is decision-complete when:
- Every interface change is specified (signatures, types, contracts)
- Every behavioral change is described (what happens, not just what changes)
- Every tradeoff is resolved (with rationale if non-obvious)
- Testing strategy is concrete (what to test, how, acceptance criteria)
- Assumptions are explicit (what was decided without user input and why)

### Output Format

Structure the final plan as a markdown document with these sections:

1. **Title** — Clear, descriptive plan name
2. **Summary** — 2-3 sentences on what this plan accomplishes
3. **Key Changes** — Grouped by subsystem or behavior, not file-by-file
4. **API/Interface Changes** — Only if applicable; specific signatures and contracts
5. **Test Plan** — Concrete test cases and scenarios
6. **Assumptions** — Defaults chosen where the user didn't specify

> See [references/plan-templates.md](references/plan-templates.md) for detailed templates by plan type (feature, bugfix, refactor).

### Compactness Guidelines

- Prefer grouped implementation bullets by subsystem or behavior over file-by-file inventories
- Mention files only when needed to disambiguate a non-obvious change (avoid naming more than 3 paths unless necessary)
- Prefer behavior-level descriptions over symbol-by-symbol removal lists
- Keep bullets short; avoid explanatory sub-bullets unless needed to prevent ambiguity
- Compress related changes into high-signal bullets; omit branch-by-branch logic and repeated invariants
- For straightforward refactors, keep the plan to summary, key edits, tests, and assumptions
- Do not include a separate Scope section unless scope boundaries are genuinely important to avoid mistakes
- Do not ask "should I proceed?" — the plan itself is the deliverable

### Revisions

If the user asks for revisions after seeing a plan, produce a complete replacement plan, not a diff. The latest plan must always stand on its own.

## Conditional Workflow

The three-phase workflow adapts based on what kind of change is being planned:

### New Feature
- Phase 1 focuses on: existing patterns, adjacent features, integration points, data model
- Phase 2 focuses on: user-facing behavior, scope boundaries, MVP vs. full vision
- Phase 3 focuses on: interfaces, data flow, edge cases, test plan

### Bugfix
- Phase 1 focuses on: reproducing the issue, tracing the code path, identifying root cause
- Phase 2 focuses on: confirming the expected behavior, scope of the fix
- Phase 3 focuses on: writing a failing reproduction test first (or standalone script if no test suite exists), then designing the minimal fix to make it pass, then risk assessment

### Refactor
- Phase 1 focuses on: current structure, pain points, dependencies, test coverage
- Phase 2 focuses on: motivation, target architecture, constraints
- Phase 3 focuses on: migration steps, before/after structure, rollback plan

> See [references/plan-templates.md](references/plan-templates.md) for output templates tailored to each plan type.
