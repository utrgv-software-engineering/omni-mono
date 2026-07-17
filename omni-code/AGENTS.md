# AGENTS.md — Omni Code AI-Agent Guidance

## Purpose
- Provide clear, repo-aware guardrails so automated edits land cleanly and keep the suite green.
- Capture the implicit expectations maintainers have for code, tooling, docs, and approval safety nets.

## Scope
- This root policy covers the whole repository unless a nearer directory ships its own `AGENTS.md`.
- Only add another `AGENTS.md` when a sub-package genuinely needs different rules; avoid copy-paste redundancy.
- When changes touch setup, release, or CLI behavior, cross-check the instructions in `README.md` and `docs/`.

## Precedence
1. System instructions
2. Developer instructions
3. User instructions
4. Nearest `AGENTS.md` (this file unless a closer one exists)
5. Other repo docs (README, `docs/`, code comments)
6. Inferred conventions from existing implementations and tests

If directives conflict, prefer the one earlier in the list. When editing files inside a directory with its own `AGENTS.md`, that nearer file overrides this root guidance.

## Code Style & Quality
- Python targets 3.10+. Follow `snake_case`, descriptive names, and mirror nearby formatting and import order.
- Prefer type hints and structured responses. Tool modules should emit `RichToolOutput` metadata consistent with peers.
- Keep edits tightly scoped to the requested task; no opportunistic refactors or wholesale formatter passes unless instructed.
- Use docstrings and precise naming to convey intent. Add inline comments only when behavior is non-obvious.
- Maintain ordering expectations—several tests assert deterministic file listings, metadata keys, and error strings.

## Tooling Expectations
- Operate through the provided workspace helpers: `list_directory`, `glob_files`, `grep_files`, `read_file`, `apply_patch`, `write_file`, etc. Invoke `execute_bash` only when a request explicitly calls for shell execution.
- Apply edits with the custom patch format handled by `tools.file_tools.apply_patch`:
  - Wrap changes with `*** Begin Patch` / `*** End Patch`.
  - Use `*** Update File: path` for modifications, `*** Add File:` when creating files, and `*** Delete File:` for removals.
- Keep dependency versions aligned with `pyproject.toml`. Published requirements currently include `openai==1.99.9`, `omniagents[all]==0.3.16`, `markitdown[all,docx,pdf]>=0.1.1`, `requests>=2.31.0`, `beautifulsoup4>=4.12.0`, and `pygit2>=1.13.0`.
- External prerequisites: Python 3.10+, Go 1.20+ on `PATH`, and `libgit2` (required by file tools). Assume credentials arrive via `.env`/`python-dotenv`; never hard-code or commit keys (`OPENAI_API_KEY`, `AZURE_OPENAI_*`, `SERPAPI_API_KEY`, etc.).

## Testing & Validation
- Install for development with `pip install -e .[dev] --extra-index-url https://pypi.fury.io/ericmichael/` while keeping PyPI as the default index.
- Primary entrypoints are the `omni` console script (`main:cli_main`) and `python main.py`.
- Run validations with `pytest -q`. Tests exercise tool metadata, deterministic ordering (especially in `tools/file_tools`), hidden-file defaults (`ignore_hidden=False`, `respect_gitignore=True`), and approval metadata wiring.
- When extending functionality, add or update tests under `tests/` to cover new behavior, including UI metadata and error cases. Keep fixtures deterministic.

## Web Mode E2E
- Agents are allowed to install dependencies needed to prove web mode. If `omni` is unavailable, run `python -m pip install -e '.[dev]' --extra-index-url https://pypi.fury.io/ericmichael/` from the repo root. The current package may warn that the `dev` extra is not provided; that warning is acceptable if the install succeeds.
- Use an isolated config directory for E2E so tests do not mutate the user's real `~/.config/omni_code` state.
- The agent compute environment may provide OpenAI-compatible credentials as `SANDBOX_OPENAI_BASE_URL` and `SANDBOX_OPENAI_API_KEY`. Export those as `OPENAI_BASE_URL` and `OPENAI_API_KEY` for runtime model calls; never write literal keys to tracked files.
- Seed `models.json` for `gpt-5.2` like this:

```bash
rm -rf /tmp/omni-code-web-e2e
mkdir -p /tmp/omni-code-web-e2e/omni_code
cat > /tmp/omni-code-web-e2e/omni_code/models.json <<'JSON'
{
  "version": 3,
  "default": "sandbox/gpt-5.2",
  "voice_default": null,
  "providers": {
    "sandbox": {
      "type": "openai-compatible",
      "base_url": "${OPENAI_BASE_URL}",
      "api_key": "${OPENAI_API_KEY}",
      "models": {
        "gpt-5.2": { "model": "gpt-5.2" }
      }
    }
  }
}
JSON
```

- Verify config resolution before launching UI:

```bash
XDG_CONFIG_HOME=/tmp/omni-code-web-e2e \
OPENAI_BASE_URL="$SANDBOX_OPENAI_BASE_URL" \
OPENAI_API_KEY="$SANDBOX_OPENAI_API_KEY" \
omni model list --json
```

- Launch web mode as a background job on a fixed port:

```bash
export XDG_CONFIG_HOME=/tmp/omni-code-web-e2e
export OPENAI_BASE_URL="$SANDBOX_OPENAI_BASE_URL"
export OPENAI_API_KEY="$SANDBOX_OPENAI_API_KEY"
export OMNI_WEB_AUTO_OPEN=false
omni --mode web --host 127.0.0.1 --port 8091 --output json
```

- Wait for `http://127.0.0.1:8091/` to return HTML and confirm port ownership with `ss -ltnp | rg '8091'`.
- For visible VNC proof, run headed Playwright on `DISPLAY=:0`. Preserve the shell environment so `PLAYWRIGHT_BROWSERS_PATH` remains available; if `chrome-binary` cannot find Chromium, use `/opt/ms-playwright/chromium-1223/chrome-linux64/chrome` directly.

```bash
DISPLAY=:0 python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=False,
        executable_path='/opt/ms-playwright/chromium-1223/chrome-linux64/chrome',
        args=['--no-sandbox'],
    )
    page = browser.new_page()
    page.goto('http://127.0.0.1:8091/', wait_until='domcontentloaded')
    page.wait_for_function('() => document.body && document.body.innerText.includes("Omni")')
    textarea = page.locator('textarea[placeholder="Type a message…"]').first
    textarea.fill('Reply with exactly: omni code web e2e ok')
    page.keyboard.press('Enter')
    page.wait_for_function('() => document.body.innerText.toLowerCase().includes("omni code web e2e ok")')
    print(page.locator('body').inner_text())
    browser.close()
PY
```

- The main chat textarea placeholder is `Type a message…`. Do not accidentally target the terminal textarea (`aria-label="Terminal input"`).
- A clean proof shows the submitted prompt and assistant response in the page body and the terminal panel status changing from `disconnected` to `connected` when terminal services are ready.

## Ink Mode E2E
- Ink mode must be driven through a real PTY. Do not use plain subprocess pipes for user-perspective testing; raw-mode terminal rendering and Enter handling can break or become misleading.
- Preferred primitive: `tmux`. If `tmux` is missing in the current image and cannot be installed with apt, use Python `pexpect` as a fallback and add `tmux` to the Dockerfile for future runs.
- Use the same isolated model config pattern as web mode, but a separate directory is helpful for clean session state:

```bash
rm -rf /tmp/omni-code-ink-e2e
mkdir -p /tmp/omni-code-ink-e2e/omni_code
cp /tmp/omni-code-web-e2e/omni_code/models.json /tmp/omni-code-ink-e2e/omni_code/models.json
```

- Launch with tmux when available:

```bash
tmux kill-session -t omni-ink-e2e 2>/dev/null || true
tmux new-session -d -s omni-ink-e2e \
  'cd /workspace/omni-code-2 && \
   XDG_CONFIG_HOME=/tmp/omni-code-ink-e2e \
   OPENAI_BASE_URL="$SANDBOX_OPENAI_BASE_URL" \
   OPENAI_API_KEY="$SANDBOX_OPENAI_API_KEY" \
   OMNI_INK_AUTO_BUILD=1 \
   TERM=xterm-256color \
   omni --mode ink'
tmux send-keys -t omni-ink-e2e 'Reply with exactly: omni code ink e2e ok' Enter
tmux capture-pane -t omni-ink-e2e -p -J
tmux send-keys -t omni-ink-e2e C-c C-c
tmux kill-session -t omni-ink-e2e
```

- If using `pexpect`, send carriage return (`'\r'`) for Enter. `sendline()` may only render text without submitting in Ink's input box.

```python
import os, time, pexpect

env = os.environ.copy()
env['XDG_CONFIG_HOME'] = '/tmp/omni-code-ink-e2e'
env['OPENAI_BASE_URL'] = env['SANDBOX_OPENAI_BASE_URL']
env['OPENAI_API_KEY'] = env['SANDBOX_OPENAI_API_KEY']
env['OMNI_INK_AUTO_BUILD'] = '1'
env['TERM'] = 'xterm-256color'

child = pexpect.spawn('omni --mode ink', cwd='/workspace/omni-code-2', env=env, encoding='utf-8', dimensions=(40, 140), timeout=240)
child.expect('Welcome to Omni Code')
child.send('Reply with exactly: omni code ink e2e ok')
child.send('\r')
child.expect('omni code ink e2e ok')
time.sleep(20)
child.sendcontrol('c')
child.sendcontrol('c')
```

- A clean proof shows the user prompt, `Thinking...`, and then an assistant line such as `● omni code ink e2e ok`, with the footer showing the active model, e.g. `gpt-5.2`.
- If Ink only shows the typed prompt and never enters `Thinking...`, the Enter key was not submitted correctly; use `tmux send-keys ... Enter` or `pexpect.send('\r')`.

## JSON-RPC Server Mode E2E
- Server mode exposes the OmniAgents JSON-RPC WebSocket API at `/ws`. It is the right path for protocol-level verification without browser or Ink rendering.
- Use the same isolated `models.json` pattern as web mode. A separate config dir keeps sessions independent:

```bash
rm -rf /tmp/omni-code-server-e2e
mkdir -p /tmp/omni-code-server-e2e/omni_code
cp /tmp/omni-code-web-e2e/omni_code/models.json /tmp/omni-code-server-e2e/omni_code/models.json
```

- Launch server mode as a background job on a fixed port:

```bash
export XDG_CONFIG_HOME=/tmp/omni-code-server-e2e
export OPENAI_BASE_URL="$SANDBOX_OPENAI_BASE_URL"
export OPENAI_API_KEY="$SANDBOX_OPENAI_API_KEY"
omni --mode server --host 127.0.0.1 --port 8092
```

- Wait for `ss -ltnp | rg '8092'` to show an `omni` process listening.
- `start_run` in server mode must include a workspace root. Either call `server_call` for `session.ensure` first, or pass `variables.workspace_root` directly in `start_run`. Without it, the server returns `WorkspaceRootRequiredError` by design.
- Minimal JSON-RPC proof:

```python
import asyncio, json, time
import websockets

async def main():
    seen = []
    async with websockets.connect('ws://127.0.0.1:8092/ws', max_size=None) as ws:
        await ws.send(json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'start_run',
            'params': {
                'prompt': 'Reply with exactly: omni code server e2e ok',
                'variables': {'workspace_root': '/workspace/omni-code-2'},
            },
        }))
        deadline = time.time() + 150
        while time.time() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=deadline - time.time()))
            seen.append(msg)
            if msg.get('method') == 'run_end' and 'omni code server e2e ok' in json.dumps(seen).lower():
                return
        raise RuntimeError('missing expected server response')

asyncio.run(main())
```

- Expected event flow includes `client_request` for `ui.set_session_state`, `run_started`, the `start_run` result, optional `token` usage events, `message_output`, and `run_end` with `end_reason: completed`.
- Use `server_call` when you need explicit session setup:

```json
{"jsonrpc":"2.0","id":1,"method":"server_call","params":{"function":"session.ensure","args":{"context":{"workspace_root":"/workspace/omni-code-2"}}}}
```

## Repo-Specific Conventions
- **Project map:** Everything installable lives inside `omni_code/`: `omni_code/project.yml` (manifest), `omni_code/agents/` (agent specs + instructions + context factories), `omni_code/tools/` (rich tool implementations), `omni_code/capabilities/` (capability-pack overlays), `omni_code/server_functions/` (control-plane RPCs), and `omni_code/overlays/` (project.yml overlays). Repo-only areas: `evaluations/`, `environments/`, `docs/`, `tests/`, plus the top-level `main.py` entrypoint.
- **Tool authoring:** Implement one tool per module in `tools/`. Decorate callable tools with `@rich_function_tool` (or `@function_tool` for slash commands), return `RichToolOutput` with populated `ui_metadata` (`value`, `display_type`, `summary`, `preview`, `truncated`, `metadata`), and add `@approval_metadata` when similar tools require approval. Export new tools via `tools/__init__.py` and ensure packaging metadata (`pyproject.toml`, `main.py`) is updated.
- **File tooling contract:** Preserve semantics in `tools/file_tools.py`—binary detection, modification-time sorting, hidden/gitignore defaults, and error messaging are covered by tests and downstream consumers.
- **Server functions & CLI:** Follow the patterns in `main.py` and `omni_code/server_functions/` when registering terminal helpers. Only add entries to `TERMINAL_SERVER_FUNCTIONS` (and similar registries) when implementations are production-ready.
- **Web/search/document tools:** Modules like `web_fetch.py`, `web_search.py`, `youtube_search.py`, `scholar_search.py`, `download_file.py`, and `conversion_tools.py` must validate inputs, surface actionable errors (especially when API keys are missing), and provide succinct summaries/previews.
- **Docs & instructions:** Keep `README.md`, `docs/`, release procedures (`docs/RELEASE.md`), and `oagents/instructions/*.md` aligned with behavioral changes. Update user-facing narratives when altering CLI flows or configuration paths.
- **Safety & approvals:** High-impact or destructive operations rely on approval metadata. Maintain conservative defaults and document rationale for actions that could alter user workspaces.
- **Secrets & git hygiene:** Never run git commands or manipulate credentials. Respect `.env` usage, avoid logging sensitive data, and do not bake secrets into code or docs.

## When in Doubt
- Mirror the nearest working pattern before inventing new abstractions.
- Re-read the relevant instructions or docs (README, `docs/`, existing agent markdown) before modifying setup, release, or CLI behavior.
- Keep changes minimal, reversible, and directly tied to the explicit request. If uncertainty persists, prefer the safest no-op.
