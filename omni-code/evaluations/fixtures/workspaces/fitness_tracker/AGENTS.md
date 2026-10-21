# AGENTS.md

You are working on a ticket-driven project. Your job is to drive the
ticket from Backlog to Done while honoring the disciplines defined by
the skills under `.omni_code/skills/`.

## Project shape (fitness_tracker)

- Backend: FastAPI in `src/fitness_tracker/`, started via
  `uv run uvicorn fitness_tracker.main:app --reload` on `:8000`.
- Frontend: Vite + React in `frontend/`, started via
  `npm --prefix frontend run dev` on **`:5173`** (Vite default — no
  `server.port` override in `vite.config.ts`). The frontend proxies
  `/api` → backend at `:8000`.
- Both processes together: `Procfile.dev` (use any Procfile runner, or
  start them in two terminals).
- Tests: `uv run pytest -q`.
- Acceptance: `python acceptance/runner.py` once the runner is installed
  (see `.omni_code/skills/webapp-acceptance-runner/`). Set
  `acceptance/config.json` `baseUrl` to `http://127.0.0.1:5173/` for
  this project — the skill ships with a generic `:3000` placeholder
  that is wrong here.

## Seed data via the API, not the UI

When an acceptance story needs sample workouts on the dashboard, seed
them by `POST /api/v1/workouts/` (curl, requests, or the runner's
`--start-cmd` followed by your own setup script) — **not** by clicking
through the UI form. UI seeding is brittle (form lives on a sub-route,
selectors drift), and the tickets in this project consistently say to
seed via the API.

## The kanban is a commitment, not a checkbox

Each column transition is a contract. Do not move a ticket until the
contract holds.

- **Backlog → Active**: "I have read enough to start. I can name the
  goal, the scope, and which skills will drive the work."
- **Active → Done**: "The work satisfies the acceptance criteria, the
  evidence exists on disk, and reverting my changes would break the
  tests I wrote."

Transitions happen *after* the contract is satisfied, not in
anticipation. Moving prematurely is the failure mode the kanban is
designed to catch.

## Skills are disciplines

When a skill is relevant, read it end-to-end before doing the work it
covers. Re-read it when you hit a failure that the skill might address.

The skill text outranks your training intuition. If a skill enumerates
a supported surface (step kinds, command flags, error codes), only use
items from that list. If you reach for something not on the list,
you're wrong, not the skill. The `webapp-acceptance-runner` DSL list
is the canonical example — only the step kinds enumerated under
"Supported DSL step kinds" in its `SKILL.md` are accepted; anything
else (e.g. random Playwright test API names, made-up assertions) is
rejected at validation time.

When a skill names a hard sequencing requirement, do not collapse it.
"Run X before Y" means run X, observe the result, *then* do Y — not
write both and run once. `bugfix`'s step 1b ("run reproduction in red
BEFORE writing any fix") is the canonical example.

When a skill describes a STOP CONDITION or names a failure mode,
treat that as the highest-priority signal in the file. The skill
authors anticipated the mistake; honor the warning.

## Backlog: explore, plan, commit

Before moving to Active:

1. Read the ticket fully (title, description, acceptance criteria).
2. Read the relevant skills under `.omni_code/skills/` end-to-end.
3. Run non-mutating commands (tests, type checks) to baseline what
   already works — this is `software-planning` Phase 1 (ground in
   the environment) and overlaps with `debug` when the failure is
   not yet localized.
4. Produce a decision-complete plan per `software-planning` Phase 3:
   what changes, in which files, validated by what tests.

Do not edit files in Backlog. The plan is the deliverable of this
column.

## Active: execute against the disciplines

Move the ticket to Active when the plan is decision-complete, then
execute. Common disciplines:

- **TDD shape (bugs, regressions)** — drive with the `bugfix` skill.
  Write the failing test, run it against the broken code, see it
  fail, *then* write the fix. If your test passes against broken
  code, the test is wrong — not done.
- **Runtime investigation** — when the failure is unclear or new,
  use the `debug` skill to isolate the root cause before applying
  a fix. Don't fix what you haven't reproduced.
- **DSL/API-bound work** — enumerate the supported surface from the
  spec; author against it; don't guess. `webapp-acceptance-runner`
  is a concrete example: only the step kinds in its SKILL.md are
  valid, and stories must be authored per the template under
  `acceptance/stories/`.
- **Browser interaction** — use the `playwright-cli` skill for
  ad-hoc exploration (snapshot, click, eval) before authoring a
  YAML acceptance story. Don't build acceptance YAML blind.
- **Runtime dependencies** — install before run. `npm install`
  before `npm run dev`. `uv sync` before `uv run`. Don't assume
  binaries are on PATH.
- **Scope** — only edit files the ticket implies. No "while I'm
  here" refactors. The diff should map directly to the work.

## Active → Done: evidence before sentinel

Before moving to Done and emitting any terminal sentinel:

- All tests pass *and* a sensitivity check passes — revert your code
  changes (not your tests); tests should now fail. This is the
  load-bearing check from `bugfix` step 1b applied at the column
  boundary.
- For UI/acceptance work, the artifacts `webapp-acceptance-runner`
  produces must exist at `<workspace_root>/artifacts/<run_id>/` —
  not anywhere else. The canonical layout is
  `artifacts/<run_id>/run.manifest.json` with non-pending status, the
  per-story `manifest.json`, screenshots, and
  `artifacts/<run_id>/run.report.embedded.html`. Run the runner from
  the workspace root with the default `--out-root` so it lands at
  `artifacts/`; do not pass `--out-root acceptance_artifacts` or any
  other name. Exit code alone is not the gate.
- No out-of-scope files modified.
- A short comment on the ticket summarizing what changed and where
  the regression test lives.

The terminal sentinel ([DONE], etc.) marks that the gate has been
satisfied. It is not the gate itself.

## When stuck

- Re-read the relevant skill before reading source. If
  `webapp-acceptance-runner` rejected your YAML, the answer is in
  its SKILL.md, not in `runner.py`.
- If a runner / tool rejects your input with an error, re-read the
  skill section it references before guessing a different input.
- If the skill seems silent on your situation, you may be in scope
  it doesn't cover — surface that, don't paper over it.
