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

Against an already-running server:

```bash
python acceptance/runner.py --out-root /home/user/workspace/artifacts
```

Let the runner start the server:

```bash
python acceptance/runner.py \
  --start-cmd "npm run dev -- --host 127.0.0.1 --port 3000 --strictPort" \
  --start-cwd . \
  --out-root /home/user/workspace/artifacts
```

Show the single run report as an artifact:
- Display `artifacts/<run_id>/run.report.embedded.html` via `display_artifact(mode="html")`.
- Reuse the same `artifact_id` while running stories to update the report incrementally.

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

- `waitFor`: `{ sel, state? }` where `state` is `attached|detached|visible|hidden`
- `expectVisible`: `<sel>`
- `click`: `<sel>`
- `fill`: `{ sel, value }`
- `type`: `{ sel, text }`
- `select`: `{ sel, value }`
- `check` / `uncheck`: `<sel>`
- `expectEval`: `{ expr, equals }`
- `expectUrlContains`: `<substring>`
- `expectCount`: `{ sel, equals }`
- `expectTextEquals`: `{ sel, equals }`
- `expectTextContains`: `{ sel, contains }`
- `expectAttrEquals`: `{ sel, attr, equals }`
- `expectEnabled` / `expectDisabled`: `<sel>`
- `screenshot`: `{ key, label? }`
- `sleepMs`: `<int>`
