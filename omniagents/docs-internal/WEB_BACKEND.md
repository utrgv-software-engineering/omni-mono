# OmniAgents Web Backend (React + Tailwind)

A complete, self-contained plan to implement a new backend launched via `omniagents run --mode web` that serves an aesthetic React (Tailwind) chat UI. This document includes required file paths, references, environment details, and a step-by-step checklist so any AI agent can begin immediately with zero external context.

## Objectives

- Implement `--mode web` to start a local FastAPI agent server and serve a React + Tailwind UI in a browser.
- Reuse the existing JSON‑RPC over WebSocket protocol used by other backends (Ink/BubbleTea/Server).
- Provide an aesthetic chat experience with:
  - Streaming assistant messages
  - Session resume/selection
  - SafeAgent tool approvals (human-in-the-loop)
  - Token usage and model metadata display
  - Basic slash commands to invoke server functions
- Package the built UI in wheels; support auto-build for source/editable installs.

## High-Level Design

- Python backend (`WebBackend`) mirrors `InkBackend` behavior:
  - Start FastAPI app using `omniagents.backends.server.app.build_app(...)`.
  - Find a free localhost port; expose WebSocket at `ws://127.0.0.1:<port>/ws`.
  - Serve a static React build (`dist/`) at `http://127.0.0.1:<port>/` and open the default browser.
  - If `dist/` not present and auto-build enabled, build UI in a per-user cache with `npm ci && npm run build`.
- Frontend React app connects to `/ws` using the OmniAgents JSON‑RPC 2.0 protocol and renders the chat UI.

## Protocol Compatibility (No Server Changes Required)

- Use `omniagents/rpc/protocol.md` as reference.
- Client RPC methods:
  - `start_run(prompt, session_id?, variables?)`
  - `stop_run(run_id)`
  - `get_session_history(session_id)`
  - `get_user_history(limit?, include_archived?)`
  - `list_sessions()`
  - `archive_session(session_id)`
  - `client_functions(version, functions[])` (optional capability handshake)
  - `client_response(request_id, ok, result?, error?)` (for client_request replies)
  - `list_server_functions()` and `server_call(function, args?, session_id?)`
- Server notifications to handle:
  - `tool_called`, `tool_result`, `message_output`, `run_started`, `run_end`, `token`, `run_status`
  - `client_request` for approvals (`function: "ui.request_tool_approval"`)

## Files to Modify/Add (Paths & Responsibilities)

1) CLI wiring
- Edit: `omniagents/cli.py`
  - Add `'web'` to `run_parser.add_argument("--mode")` choices and help.
  - Add `'web'` to `agent_run.add_argument("--mode")` choices and help.
  - Ensure args pass-through: `--session-id`, `--resume`, `--debug`, `--auth-token`, initial prompt.

2) Runtime loader
- Edit: `omniagents/core/runtime/loader.py`
  - Add mapping in `backend_modules`:
    - `'web': 'omniagents.backends.web:WebBackend'`
  - Optional: support entry points group `omniagents.backends` for `'web'`.

3) Python backend implementation
- New: `omniagents/backends/web/__init__.py`
  - Implement `class WebBackend` with `run(self, spec: AgentSpec, **kwargs) -> None`:
    - Call `build_app(config_path=..., spec=..., auth_token=...)` (from `omniagents/backends/server/app.py`).
    - Bind to `127.0.0.1:<free_port>`.
    - Wait for server readiness (socket connect retry loop).
    - Resolve UI `dist/` folder (see Auto-build section) and mount via `StaticFiles` at `/` (root) with index.
    - Open default browser to `http://127.0.0.1:<port>/`.
    - Apply CLI overrides: `apply_cli_overrides(spec, args)` so SafeAgent flags work.
    - Accept `session_id`, `resume`, `initial_prompt`, `debug`, `auth_token` in kwargs.
  - Environment variables:
    - `OMNI_WEB_AUTO_BUILD`: "0"/"false" disables auto-build (default: enabled).
    - `OMNI_WEB_UI_PATH`: absolute path to a prebuilt `dist/` folder to skip build.
    - `OMNI_DEBUG`: enable backend debug prints (also from `--debug`).
  - Cache directory (platform-specific):
    - Linux: `~/.cache/omniagents/web/ui`
    - macOS: `~/Library/Caches/OmniAgents/web/ui`
    - Windows: `%LOCALAPPDATA%/OmniAgents/web/ui`
  - Stamp/hash file: `omni-web-ui.hash` (to avoid redundant cache sync/build).

4) Frontend scaffold (React + Tailwind)
- New folder: `omniagents/backends/web/ui/`
  - `package.json`: Vite + React + TypeScript + Tailwind (scripts: `dev`, `build`, `preview`, `lint`).
  - `index.html`: root template.
  - `tailwind.config.js`, `postcss.config.js`, `src/styles/index.css`.
  - `src/main.tsx`: bootstraps React to `#root`.
  - `src/App.tsx`: chat application root.
  - `src/rpc/client.ts`: browser WebSocket JSON‑RPC client (adapted from `omniagents/backends/ink/tui/src/rpc/client.ts`).
  - `src/components/`:
    - `Header.tsx`: agent name, optional welcome.
    - `MessageList.tsx`: renders chat history using markdown component.
    - `Input.tsx`: textarea with history traversal and submit.
    - `StatusIndicator.tsx`: connection status and transient `run_status` messages.
    - `ApprovalDialog.tsx`: tool approval UI.
    - `SessionList.tsx`: resume flow UI.
    - `Usage.tsx`: per-turn and aggregate usage from `token`/`run_end`.
  - Reuse Studio markdown renderer:
    - Copy/import: `omniagents/studio/server/web/src/shared/components/MarkdownMessage.tsx` (and its CSS deps) for consistent styling.

5) Packaging
- Ensure `omniagents/backends/web/ui/dist/` is included in release wheels.
- Backend prefers packaged assets; falls back to cache auto-build for editable installs.

## References (Existing Code to Follow)

- CLI orchestration: `omniagents/cli.py`
- Backend loader: `omniagents/core/runtime/loader.py`
- Server app & WS routes: `omniagents/backends/server/app.py`
- Protocol: `omniagents/rpc/protocol.md`
- Ink Backend (auto-build/server boot): `omniagents/backends/ink/__init__.py`
- Ink TUI RPC client: `omniagents/backends/ink/tui/src/rpc/client.ts`
- Studio React components & Tailwind setup:
  - Markdown: `omniagents/studio/server/web/src/shared/components/MarkdownMessage.tsx`
  - Tailwind config: `omniagents/studio/server/web/tailwind.config.js`
  - Playground flows: `omniagents/studio/server/web/src/features/playground/PlaygroundView.tsx`

## Auto-Build Details (Frontend)

- Source dir: `omniagents/backends/web/ui/`
- Hash computation over `src/**/*.ts|tsx`, `package.json`, `tsconfig.json`, Tailwind configs.
- Cache sync: copy source to cache dir when hash changes.
- Install: prefer `npm ci` if `package-lock.json` exists; otherwise `npm install`.
- Build: `npm run build` → `dist/`.
- Dist resolution order:
  1. `OMNI_WEB_UI_PATH` if set (`<path>/dist` or direct `dist` folder)
  2. User cache `dist/`
  3. Packaged `omniagents/backends/web/ui/dist/`

## Web UI Behavior (MVP)

- Connect to `ws://127.0.0.1:<port>/ws` (append `?token=...` if `--auth-token` set).
- Initial state:
  - If `--resume` and no `--session-id`: fetch `list_sessions()` and show `SessionList`.
  - Else: go directly to Chat.
- Chat:
  - Submitting input calls `start_run(prompt, session_id?)`.
  - Stream events: append `message_output`, show `tool_called`/`tool_result`, update `StatusIndicator` with `run_status`.
  - On `token`: update `Usage` (delta + totals, model metadata).
  - On `run_end`: finalize totals, show error or success state.
- Approvals:
  - On `client_request` with `function: "ui.request_tool_approval"`, show `ApprovalDialog`.
  - Respond via `client_response(request_id, ok=true, result={ approved, always_approve })`.
- History:
  - `get_session_history(session_id)` when resuming; show prior messages.
  - `get_user_history(limit=200, include_archived=true)` to power input history.

## Acceptance Criteria

- `omniagents run -P project.yml --mode web` opens browser and loads UI.
- UI connects to `/ws` and can send a prompt, show streaming output, and end state.
- SafeAgent approvals appear and work (Yes/Always/No) with server behavior respected.
- Usage updates (`token`) and final totals (`run_end`) are displayed.
- Resume works: `--resume` shows session list; `--session-id` resumes directly.
- Node/npm not required when installing from a wheel with packaged `dist/`.

## Testing Strategy

- Unit: backend `WebBackend` port binding, server readiness checks, UI dist resolution logic.
- Integration: launch backend, verify `/` serves index and `/ws` accepts; mock RPC to simulate chat run.
- Manual: develop frontend via `npm run dev` (future enhancement), build and run end-to-end.

## Environment & Commands

- Frontend build (development):
  - `cd omniagents/backends/web/ui`
  - `npm install`
  - `npm run build`
- Run web backend:
  - `omniagents run -P project.yml --mode web`
  - Options: `--resume`, `--session-id <id>`, `--debug`, `--auth-token <token>`, `--ui-minimal`
- Env vars:
  - `OMNI_WEB_AUTO_BUILD=0` to disable auto-build
  - `OMNI_WEB_UI_PATH=/abs/path/to/ui/dist` to use prebuilt assets
  - `OMNI_DEBUG=1` for verbose backend logs
  - `OMNI_WEB_AUTO_OPEN=0` to skip launching the default browser (headless mode)
  - `OMNI_WEB_MINIMAL=1` to always launch the UI without the sidebar (equivalent to passing `minimal=true` in the iframe URL)

## Implementation Checklist

- [ ] Add `'web'` mode to `omniagents/cli.py` run and agent-run parsers.
- [ ] Add loader mapping in `omniagents/core/runtime/loader.py`.
- [ ] Create `omniagents/backends/web/__init__.py` with `WebBackend.run(...)`.
- [ ] Implement server boot, port selection, readiness loop, debug logging.
- [ ] Implement static serving of UI `dist/` and open default browser.
- [ ] Implement auto-build: cache setup, hash/stamp, `npm ci`/`npm run build`.
- [ ] Scaffold React app at `omniagents/backends/web/ui/` (Vite + Tailwind + TS).
- [ ] Implement browser RPC client and notification handlers (`src/rpc/client.ts`).
- [ ] Build chat components: Header, MessageList, Input, StatusIndicator.
- [ ] Integrate `MarkdownMessage.tsx` from Studio for rendering.
- [ ] Implement approvals flow (`client_request` → `client_response`).
- [ ] Implement usage display (`token` deltas/totals; `run_end` summary).
- [ ] Implement session list (`list_sessions`) and resume path.
- [ ] Package `dist/` into wheels and prefer packaged assets.
- [ ] Add smoke tests for server start and `/ws` availability.
- [ ] Update CLI help and README sections as needed.
