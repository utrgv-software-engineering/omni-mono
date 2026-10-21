# AGENTS.md — Omni Desktop

## Scope

- This file covers the whole `omni-desktop` package.
- Prefer proving launcher behavior from the user's perspective before declaring UI work complete.
- Agents may install project dependencies and use temporary runtime state for verification.

## Setup

- Required runtime: Node `22+` and npm.
- Install dependencies with `npm install`. This runs `electron-rebuild` (node-pty) and downloads `uv`.
- Do not run `npm audit fix` or broad upgrade commands unless explicitly asked.
- Keep generated app state out of the repo while running Vite watchers. Use a temp config directory such as `XDG_CONFIG_HOME=/tmp/omni-desktop-e2e-xdg`; never put `XDG_CONFIG_HOME` under the repo, because Vite will watch Electron cache files and thrash reloads.

## Credentials And Models

- The agent environment may provide OpenAI-compatible credentials as `SANDBOX_OPENAI_BASE_URL` and `SANDBOX_OPENAI_API_KEY`. Export them as `OPENAI_BASE_URL` and `OPENAI_API_KEY` before launching the app.
- Never write literal API keys into tracked files, logs, or docs. A `.env` must stay ignored.
- Models live in omni-code's `models.json`, shared by the desktop and the `omni` CLI: `$XDG_CONFIG_HOME/omni_code/models.json`, or `~/.config/omni_code/models.json` without `XDG_CONFIG_HOME`. The launcher store (`<config>/Omni Code/config.json`) holds app state such as `onboardingComplete` and `envVars`.
- Keep `envVars` empty when relying on exported credentials. Setting it to `${SANDBOX_OPENAI_BASE_URL}` passes that literal string to the agent server and breaks model calls.

Seed an isolated Electron profile with an OpenAI-compatible `gpt-5.2` provider:

```bash
rm -rf /tmp/omni-desktop-e2e-xdg
mkdir -p '/tmp/omni-desktop-e2e-xdg/Omni Code' /tmp/omni-desktop-e2e-xdg/omni_code
cat > '/tmp/omni-desktop-e2e-xdg/Omni Code/config.json' <<'JSON'
{ "onboardingComplete": true, "envVars": "" }
JSON
cat > /tmp/omni-desktop-e2e-xdg/omni_code/models.json <<'JSON'
{
  "version": 3,
  "default": "sandbox/gpt-5.2",
  "voice_default": null,
  "providers": {
    "sandbox": {
      "type": "openai-compatible",
      "base_url": "${OPENAI_BASE_URL}",
      "api_key": "${OPENAI_API_KEY}",
      "models": { "gpt-5.2": { "model": "gpt-5.2" } }
    }
  }
}
JSON
```

## Electron E2E Driving

- Launch Electron in development with a CDP port. `9444` avoids common conflicts with stale child processes.

```bash
export XDG_CONFIG_HOME=/tmp/omni-desktop-e2e-xdg
export OMNI_DEBUG_PORT=9444
export OPENAI_BASE_URL="$SANDBOX_OPENAI_BASE_URL"
export OPENAI_API_KEY="$SANDBOX_OPENAI_API_KEY"
npm run dev
```

- Set `DISPLAY=:0` when a human should see the window (for example over VNC). Use `xvfb-run -a npm run dev` for hidden runs.
- Run long verification as a background job instead of blocking the turn.
- Before launch, stop stale `electron-vite`, Electron, and `omni serve` processes. After launch, confirm the CDP port belongs to Electron: `ss -ltnp | rg '9444|5173'` should show `9444` owned by `electron`, not `python`.
- Verify CDP with `curl http://127.0.0.1:9444/json/version` and list pages with `curl http://127.0.0.1:9444/json/list`.
- Drive the renderer with Playwright over CDP:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:9444')
    page = next(
        page
        for context in browser.contexts
        for page in context.pages
        if 'localhost:' in page.url
    )
    page.wait_for_function('() => document.body && document.body.innerText.includes("Settings")')
    print(page.locator('body').inner_text())
    browser.close()
```

- The built-in app-control system is also CDP-backed: `src/main/app-control-cdp.ts`, `src/main/app-control-manager.ts`, `src/shared/app-control-types.ts`.
- CDP refs/snapshots are invalidated after navigation; re-snapshot before clicking again.
- Gather UI evidence in this order: accessibility snapshot or rendered text, console logs, network logs, screenshots, then video/tracing for harder issues.

## Browser / Server E2E Driving

- Server mode keeps its launcher store at `$HOME/.config/Omni Code/config.json` (not under `XDG_CONFIG_HOME`), while models still come from the omni-code config dir. Use an isolated home and seed both files as above, with `/tmp/omni-desktop-server-home/.config` in place of `/tmp/omni-desktop-e2e-xdg`.
- Build server/browser bundles once with `npm run build:server`, then start the server in a background job:

```bash
export HOME=/tmp/omni-desktop-server-home
export XDG_CONFIG_HOME=/tmp/omni-desktop-server-home/.config
export OPENAI_BASE_URL="$SANDBOX_OPENAI_BASE_URL"
export OPENAI_API_KEY="$SANDBOX_OPENAI_API_KEY"
export HOST=127.0.0.1
export PORT=3001
npm run start:server
```

- Wait for `Server listening on http://127.0.0.1:3001`, then drive `http://127.0.0.1:3001/` with Playwright or `playwright-cli`.
- A successful chat proof shows the submitted prompt and the assistant response in the page body.

## Permanent Playwright E2E Suite

- Committed E2E tests use native Playwright Test; see `tests/e2e/README.md`.
- Add user-story coverage under `tests/e2e/specs/` and shared helpers under `tests/e2e/fixtures/` or `tests/e2e/support/`.
- Prefer `getByRole`, `getByLabel`, and visible product language. Add `data-testid` only when no stable accessible selector exists.
- Keep mode-specific launch details in fixtures; specs should cover shared stories across `server-local` and `electron-local` where possible.
- Commands: `npm run test:e2e:server`, `npm run test:e2e:electron`, `npm run test:e2e`, `npm run test:e2e:headed`, `npm run test:e2e:ui`.
- Visual proof: `npm run test:e2e:proof`, `npm run test:e2e:proof:server`, `npm run test:e2e:proof:electron`. These run the same specs with `VISUAL_PROOF=1`, keeping screenshots, traces, and 1080p videos. `VISUAL_PROOF_SLOW_MO_MS=<ms>` adjusts playback speed.
- Reports and traces live under `artifacts/playwright-report/` and `artifacts/playwright-results/`; proof runs use `artifacts/playwright-proof-report/` and `artifacts/playwright-proof-results/`.
- For user-facing changes, generate proof artifacts and cite the exact command and report paths in the handoff.

## Debugging Workflow

- Start with the narrowest proof: dependency install, app launch, CDP availability, then one user-visible assertion.
- Check logs before patching. Electron dev logs show the renderer URL, the CDP line, runtime install progress, and the `omni serve` readiness JSON.
- If the UI appears blank, query the CDP page title and body text before assuming React failed.
- If the app reloads repeatedly, make sure generated config/cache directories are outside the repo.
- If CDP is unreachable or hangs, check port ownership with `ss -ltnp`; a stale `omni serve` process can hold the debug port.
- If model calls fail with `Request URL is missing an 'http://' or 'https://' protocol`, inspect the stored `envVars`; a literal `${SANDBOX_OPENAI_BASE_URL}` likely clobbered `OPENAI_BASE_URL` in the child process.
- Harmless Linux/Xvfb logs include missing DBus socket, GPU process initialization errors, and `APPIMAGE env is not defined`.
- The first isolated launch may install the Omni runtime with `uv`; wait for `Installation completed successfully` and the `omni serve` readiness JSON before testing agent flows.

## Validation Commands

- Unit tests: `npm run test:no-watch` or targeted `npx vitest run <path>`.
- Type/lint checks: `npm run lint:tsc`, `npm run lint:eslint`, `npm run lint:prettier`, `npm run lint:knip`, `npm run lint:dpdm`.
- Production builds: `npm run build`, `npm run build:server`.
- Use targeted checks first; avoid broad lint/build runs during exploratory UI debugging.

## Git Hygiene

- Do not commit, stage, reset, or clean files unless the user explicitly asks.
- Preserve user changes. Check `git status -sb` before editing.
- Keep `.env`, temporary XDG config directories, runtime caches, screenshots, and job logs out of commits.
