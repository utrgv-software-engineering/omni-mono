---
name: webapp-acceptance-runner
description: Create and run YAML-based acceptance/system tests for web apps using playwright-cli, capturing screenshot/video evidence and generating a single embedded HTML run report artifact. Use when asked to add an acceptance test runner, write user-story flows, validate acceptance criteria, or produce repeatable UI evidence.
---

# Web App Acceptance Runner (YAML DSL)

## Install into a project

Run the installer to copy the runner scaffold into a target project. The
installer ships next to this SKILL.md — invoke it with a path that resolves
in the current workspace:

```bash
# From the project root:
python .omni_code/skills/webapp-acceptance-runner/scripts/install_acceptance.py \
  --project-root .
```

(If your skills tree lives elsewhere — e.g. `/home/user/.config/omni_code/...` —
substitute that path. The install script just copies `assets/acceptance/`
into `<project-root>/acceptance/`.)

This creates:
- `<project-root>/acceptance/runner.py`
- `<project-root>/acceptance/config.json`
- `<project-root>/acceptance/stories/`

## Author stories

Create one file per story in `acceptance/stories/*.yaml`.

Guidelines:
- Prefer stable selectors (`data-testid`) over CSS/layout selectors.
- Keep 3–5 `screenshot` checkpoints per story.
- Use `video.embedPolicy: on-fail` by default to keep the run report small.

## Run

**Always run from the workspace root.** The runner defaults `--out-root` to
`<cwd>/artifacts/`, so artifacts land at `<workspace_root>/artifacts/<run_id>/`
— this is the canonical, expected location. Do **not** pass `--out-root`
unless you have a specific reason to redirect output. Do not invent a
different name like `acceptance_artifacts/` — the runner, the report
display step, and any acceptance measures all assume `artifacts/`.

### Prerequisites

Install runtime dependencies before invoking the runner. The runner cannot
start a frontend dev server without them:

```bash
# From the workspace root, install whatever the project uses
uv sync                  # or: pip install -e ., poetry install, etc.
npm --prefix frontend ci # or: cd frontend && npm ci
```

### Determine the dev server URL for THIS project

`acceptance/config.json` ships with a generic placeholder
(`http://127.0.0.1:3000/`). **Do not assume that port — most projects
do not use 3000.** Before authoring stories, confirm the actual URL:

- Vite projects default to `:5173` (check `frontend/vite.config.*` for
  `server.port` overrides).
- Create-React-App / Next.js dev typically `:3000`.
- Rails `:3000`, Django `:8000`, FastAPI `:8000`, etc.
- Check `Procfile.dev`, `package.json` "scripts.dev", README, or run
  the dev command and observe the URL it prints.

Once you know the project's URL, **either** edit `baseUrl` in
`acceptance/config.json` **or** pass `--base-url` on the runner invocation.
The runner errors out if `baseUrl` is empty (`runner.py:172`).

Project-specific notes (port, dev command, env vars) belong in the
project's `AGENTS.md`, not in this skill.

### Canonical invocation

The runner does **not** start the app under test. Bring it up
yourself first (Procfile, docker-compose, two manual `&`'d
commands, `make dev` — whatever the project uses; see AGENTS.md),
verify it's responding, then run:

```bash
python acceptance/runner.py --base-url http://127.0.0.1:<your-port>/
```

The runner polls `--base-url` until it returns 2xx (timeout
configurable via `--wait-timeout-s`, default 30). If you forgot to
start the app, you'll see a clear "base url not reachable" error
after the timeout — start the app and re-run.

This is the standard contract for test runners (pytest, jest,
playwright test all assume the app is up before they run). It
keeps the runner focused on test execution and lets project
idioms own startup.

After a successful run, the artifacts you must verify exist on disk:

```
<workspace_root>/artifacts/<run_id>/
  run.manifest.json              # non-pending status
  run.report.embedded.html       # the single embedded HTML report
  <story_id>/
    manifest.json                # per-story manifest
    *.png                        # screenshots
    run.webm                     # (when video.embedPolicy fires)
```

### Knowing the run_id

`run_id` is auto-generated as a UTC ISO timestamp (e.g. `2026-04-29T04-06-50Z`)
unless you pass `--run-id <name>`. You do **not** have to guess or list the
directory to find it — the runner prints the absolute path to the report
as its **final line of stdout**, e.g.:

```
/path/to/workspace/artifacts/2026-04-29T04-06-50Z/run.report.embedded.html
```

Capture that line and pass it to `display_artifact(path=..., mode="html")`.
If you prefer a stable, predictable ID (e.g. so you can refer to artifacts
from other tools), pass `--run-id <name>`:

```bash
python acceptance/runner.py --run-id 2026-04-29-recent-workouts
```

### Display the report

- Display the `run.report.embedded.html` (the path printed by the runner)
  via `display_artifact(mode="html")`.
- Reuse the same `artifact_id` while running stories to update the report
  incrementally.

## Headed mode + login reuse

Use headed mode when you want to watch the run live (or help with login):

```bash
python acceptance/runner.py --headed
```

If you need the browser session to stay logged in:

- Reuse the same browser session during a run:

```bash
python acceptance/runner.py --reuse-session
```

- Persist browser storage across runs (recommended for auth-heavy apps):

```bash
python acceptance/runner.py --persistent
# or specify a stable profile directory
python acceptance/runner.py --profile ./acceptance/profile
```

- Save/load auth state explicitly:

```bash
python acceptance/runner.py --state-load ./acceptance/auth.json
python acceptance/runner.py --state-save ./acceptance/auth.json
```

To pause after opening the browser so you can log in manually:

```bash
python acceptance/runner.py --headed --pause-after-open --reuse-session
```

For VNC/noVNC environments, set `browser.display` in `acceptance/config.json` or pass `--display=:0`.

## Supported DSL step kinds

Anything not on this list is rejected by the runner. Do not use Playwright
test API names that are not enumerated here.

### Navigation

- `goto`: `<url>` — navigate to URL. Absolute (`https://...`) used as-is;
  relative (`/dashboard`, `dashboard`) resolved against `baseUrl`.
- `reload`: (no args) — reload the current page.
- `back`: (no args) — `page.goBack()`.
- `forward`: (no args) — `page.goForward()`.
- `waitForUrl`: `<substring>` — wait until `page.url()` contains the
  substring (e.g. after a click that triggers a redirect).

For no-args steps, a bare scalar is the natural form — both work:

```yaml
steps:
  - reload                # bare scalar (preferred)
  - reload: {}            # explicit empty mapping
```

### Interaction

- `click`: `<sel>`
- `fill`: `{ sel, value }`
- `type`: `{ sel, text }`
- `select`: `{ sel, value }`
- `check` / `uncheck`: `<sel>`

### Synchronization

- `waitFor`: `{ sel, state? }` where `state` is `attached|detached|visible|hidden`
- `sleepMs`: `<int>` — last resort; prefer `waitFor` or `waitForUrl`.

### Assertions

- `expectVisible`: `<sel>`
- `expectUrlContains`: `<substring>`
- `expectCount`: `{ sel, equals }`
- `expectTextEquals`: `{ sel, equals }`
- `expectTextContains`: `{ sel, contains }`
- `expectAttrEquals`: `{ sel, attr, equals }`
- `expectEnabled` / `expectDisabled`: `<sel>`
- `expectEval`: `{ expr, equals }` — run JS, assert return value matches.

### Side-effect JS

- `evaluate`: `<expr>` — run JS for side effects, no return-value
  assertion. Use this for setup work like POSTing to your API to seed
  data:

  ```yaml
  - evaluate: |
      (async () => {
        await fetch('/api/v1/widgets/', { method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name: 'Seed' }) });
      })();
  - reload   # refresh so the page shows the seeded state
  ```

### Notes on navigation

- The runner navigates to `baseUrl` once at the start of each story.
  After that, use `goto`/`reload`/`back`/`forward` to move.
- **Do not** use `expectEval` to assign `window.location.href`. The
  navigation tears down the eval context before the result is returned;
  the runner reports `missing ### Result json`. Use `goto` instead.
- After API seeding via `evaluate`, follow with `reload` (if you're
  already on the right page) or `goto` (if you need to navigate). Don't
  assume the page reflects new state without one of those.
