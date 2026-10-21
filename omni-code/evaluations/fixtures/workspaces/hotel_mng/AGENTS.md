# AGENTS.md

This is a ticket-driven Rails project. The workflow for driving a
ticket through the kanban is defined in
`.omni_code/skills/software-tickets/SKILL.md` — read it end-to-end
before doing anything else. This file covers only project-specific
runtime details that the workflow skill doesn't.

## Project shape (hotel-mng)

- **Stack**: Rails 8.0.2, Hotwire (Turbo + Stimulus), Tailwind, all
  server-rendered. There is no separate frontend — every UI surface is
  an `.erb` view served by Rails.
- **Database**: SQLite for development and test (`storage/*.sqlite3`).
  No Postgres setup needed; the `pg` gem is in the Gemfile but
  `config/database.yml` selects sqlite3 for dev/test.
- **Web server**: `bin/rails server -p 3000 -b 127.0.0.1`. **Always
  start it via `execute_bash(..., background=True)`** — never with
  shell-level `&` chained inside a foreground compound command. The
  background mode returns a `job_id` immediately; manage the server
  with `bash_status` (poll), `bash_logs` (read combined stdout/stderr
  to confirm it's listening), and `bash_kill` at teardown. Foreground
  + shell `&` will hang the parent call until every `&&`-chained step
  completes, and a failure mid-chain leaks the server because the
  trailing `kill` never runs.
- **Health endpoint**: `GET /up` returns 200 once Rails is booted —
  use this for any health probe (acceptance runner `baseUrl`,
  readiness loops, etc.). Do NOT probe `/`: it redirects to
  `/dashboard` which requires authentication, so an unauthenticated
  GET will 302 through several routes and isn't a stable readiness
  signal.
- **Dev DB is pre-seeded**: `storage/development.sqlite3` ships in the
  fixture. `bin/rails server` works immediately. If you change a
  migration, run `bin/rails db:prepare` to rebuild it.
- **Tailwind watcher**: `bin/rails tailwindcss:watch`. Not required for
  HTML/Capybara assertions on existing styles, only when authoring new
  utility classes that haven't been compiled yet.
- **Tests**: `bin/rails test` (unit + controller + integration) or
  `bin/rails test:system` (Capybara). The full suite uses FactoryBot
  factories under `test/factories/` — prefer these over fixtures.
- **Install**: `bundle install` (will fetch ~50 gems on first run).
  After install, `bin/rails db:test:prepare` creates the test schema.
- **Procfile.dev** also boots agent processes you do not need for
  fixing UI/test bugs — start the Rails server alone unless the ticket
  explicitly requires the agent stack.

## Multi-tenancy: every domain row is account-scoped

The app uses `acts_as_tenant(:account)` on every domain model. Tests
must set `ActsAsTenant.current_tenant = @account` in `setup` and clear
it in `teardown`. Look at any existing controller test (e.g.
`test/controllers/public/availability_controller_test.rb`) for the
pattern. If you write a model or controller test without setting the
tenant, you'll get cryptic "no tenant set" errors or rows from the
wrong account leaking in.

## Seed data: use the eval API, not fixtures or `db:seed`

This project ships `Api::V1::EvalController` specifically for agent
evaluations. It is gated by `ALLOW_EVAL_API=true` and authenticated
by a static bearer token `EVAL_API_TOKEN` (default
`eval-development-token`). **You must export `ALLOW_EVAL_API=true`
in the same execute_bash call that starts the Rails server** —
otherwise the controller returns 403 Forbidden ("Eval API is only
available in test environment or with ALLOW_EVAL_API=true"). E.g.

```
execute_bash(
  command="ALLOW_EVAL_API=true bin/rails server -p 3000 -b 127.0.0.1",
  background=True,
)
```

```bash
# Bootstrap a hotel with two room types and ten rooms.
curl -sS -X POST http://127.0.0.1:3000/api/v1/eval/setup \
  -H "X-Eval-Token: eval-development-token" \
  -H "Content-Type: application/json" \
  -d '{"fixture":"base_hotel_with_availability"}'
```

Response includes `account_id`, `hotel_id`, and an `api_key`. Use the
`hotel_id` to drive the public pages.

Available fixtures: `base_hotel`, `base_hotel_with_availability`,
`hotel_with_returning_guest`, `hotel_with_reservation`,
`hotel_fully_booked`. See `app/controllers/api/v1/eval_controller.rb`
for the full set and what each creates.

`db:seed` exists but destroys everything before reseeding — do not run
it inside the eval workspace. The eval API supports parallel-safe
isolated seeds; that's what to use.

## Public pages are subdomain-scoped, with a query-param fallback

The `Public::*` controllers resolve the hotel via:
1. Middleware-injected `request.env['hotel.current']` (subdomain DNS)
2. `request.subdomain` (when subdomain DNS is set up)
3. `?hotel_id=<id>` query parameter (the **fallback that works in
   eval** — no DNS games required)

For acceptance stories, hit the namespaced route with `?hotel_id=<id>`
appended. Public routes live under `/public/...` because the
`namespace :public do` block wraps them in `config/routes.rb`:

```
http://127.0.0.1:3000/public/availability?hotel_id=<id>&check_in_date=2026-05-10&check_out_date=2026-05-12&guests=2
http://127.0.0.1:3000/public/hotel?hotel_id=<id>
http://127.0.0.1:3000/public/rooms?hotel_id=<id>
```

`/availability` (no `/public/` prefix) is **not** a route — it returns
404 with "No route matches [GET] /availability". Run `bin/rails routes
| grep public` to enumerate the full set.

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

## Execution disciplines (project-specific notes)

The kanban contract in `.omni_code/skills/software-tickets/SKILL.md`
defines column-by-column expectations. These notes layer Rails-
specific texture on top:

- **TDD shape (bugs, regressions)** — drive with the `bugfix` skill.
  Write the failing test, run it against the broken code, see it
  fail, *then* write the fix. For a Rails controller bug, the
  failing test is typically a new case in the existing
  `test/controllers/<area>/*_test.rb` that asserts the *order* or
  *content* of the response — not just `assert_response :success`.
  An `assert_response :success` test is a false green for any bug
  where the response status stays 200.
- **Runtime investigation** — when the failure is unclear or new,
  use the `debug` skill to isolate the root cause before applying a
  fix. Don't fix what you haven't reproduced.
- **DSL/API-bound work** — enumerate the supported surface from the
  spec; author against it; don't guess. `webapp-acceptance-runner`
  is a concrete example: only the step kinds in its SKILL.md are
  valid, and stories must be authored per the template under
  `acceptance/stories/`.
- **Browser interaction** — use the `playwright-cli` skill for
  ad-hoc exploration (snapshot, click, eval) before authoring a
  YAML acceptance story. Don't build acceptance YAML blind.
- **Runtime dependencies** — install before run. `bundle install`
  before `bin/rails server`. The dev sqlite ships pre-built (see
  Project shape), so `db:prepare` is usually unnecessary. Don't
  assume binaries are on PATH.
- **Scope** — only edit files the ticket implies. No "while I'm
  here" refactors. The diff should map directly to the work.

## Acceptance artifact contract

For UI/acceptance work, the artifacts `webapp-acceptance-runner`
produces must exist at `<workspace_root>/artifacts/<run_id>/` — not
anywhere else. The canonical layout is
`artifacts/<run_id>/run.manifest.json` (non-pending status), the
per-story `manifest.json`, screenshots, and
`artifacts/<run_id>/run.report.embedded.html`. Run the runner from
the workspace root with the default `--out-root` so it lands at
`artifacts/`; do not pass `--out-root acceptance_artifacts` or any
other name. **Exit code alone is not the gate** — the manifest must
exist and have non-pending status.

## Sensitivity check before claiming done

Before claiming any column transition that asserts "the work passes
its tests": revert your code changes (not your tests); the tests you
wrote should now fail. This is `bugfix` step 1b applied at the
column boundary. If reverting your code does NOT make tests fail,
your tests don't actually exercise the change — fix the test, not
the column transition.

## When stuck

- Re-read the relevant skill before reading source. If
  `webapp-acceptance-runner` rejected your YAML, the answer is in
  its SKILL.md, not in `runner.py`.
- If a runner / tool rejects your input with an error, re-read the
  skill section it references before guessing a different input.
- If the skill seems silent on your situation, you may be in scope
  it doesn't cover — surface that, don't paper over it.
