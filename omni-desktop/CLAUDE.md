# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Omni Code Launcher — an Electron + React desktop app for installing the Omni Code runtime and running agent chats on the host (omni-code's default profile; the desktop has no sandbox/profile support of its own). Also ships a browser/server build (Fastify + WebSocket) that runs the same renderer without Electron.

## Commands

```bash
npm i                      # Install the monorepo's npm workspace (root lockfile);
                           # postinstall runs electron-rebuild (node-pty)
                           # and downloads uv
npm run dev                # Electron dev with hot reload (electron-vite)
npm run build              # Build main + preload + renderer into out/
npm run package            # Clean + download assets + build + electron-builder (→ dist/)
npm run publish            # Same as package, but publishes to GitHub Releases

npm run lint               # Runs ESLint, Prettier, tsc, knip, dpdm concurrently
npm run fix                # eslint --fix + prettier --write

npm test                   # vitest (rebuilds node-pty first)
npm run test:no-watch      # One-shot vitest run
npx vitest run src/lib/result.test.ts   # Single test file

# Browser/server mode (no Electron)
npm run dev:server         # Concurrent: server watch build + browser Vite dev on :5173
npm run build:server       # Build server + browser SPA
npm run start:server       # Run built server from out/server/index.mjs (default :3001)

npm run build:wsl-payload  # Assemble the WSL daemon tarball (Linux/WSL only; run
                           # build:server first). Required before `wsl:link` works
                           # in dev — the Windows shell provisions this tarball
                           # into the distro.
```

`npm test` re-runs `electron-rebuild` against node-pty every time — needed because the binary is compiled against Electron's Node ABI, which differs from system Node.

## Architecture

### Two runtime targets

The same renderer ships in two shells:

1. **Electron** (`src/main/index.ts`) — full desktop app, electron-store for persistence, direct IPC via `@electron-toolkit/typed-ipc`.
2. **Server mode** (`src/server/index.ts`) — Fastify + `ws`, same managers behind a WebSocket RPC. `vite.server.config.ts` aliases `electron`, `electron-store`, `electron-context-menu`, `electron-updater`, and `@electron-toolkit/typed-ipc/main` to shims in `src/server/` so main-process managers run unchanged.

The browser build (`vite.browser.config.ts`) produces a standalone SPA that proxies `/api`, `/ws`, and `/proxy` to the Fastify server and shims Electron-only renderer imports to empty stubs.

### Transport abstraction

IPC goes through `TransportEmitter` / `TransportListener` interfaces (`src/shared/transport.ts`). Two implementations in `src/renderer/transport/`:

- `ElectronTransportEmitter/Listener` — wraps typed-ipc
- `WsTransportEmitter/Listener` — JSON-RPC-ish over a single WebSocket, with reconnect + message queue; fetches an auth token from `/api/ws-token` (loopback-only) before dialing `/ws`

`src/renderer/services/ipc.ts` picks the transport at startup. All channel signatures live in `src/shared/types.ts` (`IpcEvents` renderer→main, `IpcRendererEvents` main→renderer).

Server-side, `WsHandler` keeps a `persistentSessions` map keyed by `sessionId` from the client so managers and agent runtimes **survive WS reconnections and React remounts** — if you're adding a manager that holds long-lived state, wire it into `wireGlobalHandlers` (shared across clients) or `wireClientManagers` (per-session) in `src/server/managers.ts`.

### Shared IPC handlers

`src/shared/ipc-handlers.ts` registers channels that behave identically under Electron and server mode (`config:*`, `util:*`, `skills:*`). Both entry points pass in an `IIpcListener` adapter plus a `fetchFn` and `launcherVersion` so the module has no Electron imports. Electron-specific handlers (dialogs, shell, window) stay in `src/main/index.ts`.

### Main-process managers

Lifecycle pattern: each manager is a factory returning `[instance, cleanup]`. Registered from `src/main/index.ts` (Electron) and `src/server/managers.ts` (server). Key ones:

- `MainProcessManager` (`main-process-manager.ts`) — Electron window + store IPC
- `ProcessManager` (`process-manager.ts`) — all agent processes (chat + code tabs) keyed by string ID, driven by `AgentProcess` (`agent-process.ts`)
- `ProjectManager` (`project-manager.ts`) — projects (named sets of sources plus their chats), persisted as `StoreData.projects`; seeds the Personal project. `projects-import.ts` is the one-time import from the retired `projects.db`.
- `OmniInstallManager` — Python + venv via bundled `uv` binary
- `ConsoleManager` — proxies `terminal:*` IPC into `omni serve`'s WebSocket so terminals run in the agent's environment via `omniagents.rpc.terminal_backends.SessionPtyBackend`. The shell lives in the agent process (one PTY per `terminal_id`); main is a thin transport. See `src/main/terminal-proxy.ts` for the WS plumbing and `omniagents/builtin/server_functions/terminal.py` (in the omniagents package) for the server-side entry points (`terminal.create`, `terminal.close`).
- `AppControlManager`

Managers expose a timestamped `getStatus()` and broadcast updates via `sendToWindow` (Electron) / `sendToAll` (server). Store changes fan out on the `store:changed` channel.

### XState machines (shared)

`src/shared/machines/` holds pure XState v5 definitions used by the renderer — `chat-session`, `chat-boot`, `rpc-client`, `file-editor`, `terminal-tab`, `voice-session`, `auto-launch`. They have no React, IPC, or DOM imports, and reuse pure helpers from `src/lib/` (e.g. `session-filter`). When adding chat/session state, extend the machine rather than adding side-channel React state.

### Renderer structure

- `src/renderer/features/` — feature modules; each owns its components + a `state.ts` (nanostores atom bundle). Current features: `Agents`, `AppControl`, `Banner`, `Browser`, `Code`, `CommandPalette`, `Console`, `Files`, `Gallery`, `Git`, `Jobs`, `Omni`, `Onboarding`, `Plugins`, `Projects`, `Review`, `ScheduledTasks`, `SettingsModal`, `Toast`, `Voice`, `XTermLogViewer`.
- `src/renderer/ds/` — design system primitives (Fluent + Radix + custom)
- `src/renderer/services/` — cross-cutting: `ipc`, `store` (mirrors main-process store into an atom), `status` (polls `main-process:get-status`), `agent-process`, `session-control`, `config`, `dev`
- `src/renderer/common/` — shared UI (webview, layouts)
- `src/renderer/transport/` — Electron and WS transport adapters

### WSL backend mode (Windows)

On Windows the Electron shell can run the backend as a daemon inside a WSL2
distro (`docs/windows-wsl-backend-plan.md`): `StoreData.remoteBackend` holds a
`WslBackend | ServerBackend` union, `WslBackendManager` (`src/main/wsl-backend.ts`)
provisions/spawns/health-checks the daemon via `wsl.exe --exec` (never the plain
`--` form — it destroys argv quoting), and auth is a per-boot shared secret with
tokens minted in main. Native file dialogs translate paths at the single
`toBackendPath` boundary in `src/main/index.ts` via `@/lib/wsl-path`.

## Conventions

- **Path aliases**: always `@/…` (tsconfig `baseUrl=.`, `paths: { "@/*": ["src/*"] }`); never relative imports across top-level dirs
- **Store atoms**: `$`-prefixed nanostores (e.g. `$store`, `$mainProcessStatus`)
- **Result type**: `src/lib/result.ts` — prefer returning `Result<T, E>` over throwing across boundaries
- **Types**: strict TS, `noUncheckedIndexedAccess`, `noImplicitAny`, `isolatedModules`
- **Imports**: sorted by `eslint-plugin-simple-import-sort`; `react/jsx-curly-brace-presence` forbids unnecessary braces; `react/jsx-no-bind` allows `.bind` but not arrows in JSX attributes (check the rule — `allowBind: true`)
- **Circular deps**: `dpdm` runs on `main/index.ts`, `renderer/index.ts`, `preload/index.ts` with `--exit-code circular:1` — circular imports will fail lint

## Branches and releases

See the repository root `CLAUDE.md`: work happens on `dev`, `main` holds released code, and a desktop release is cut by pushing an `omni-desktop-v<version>` tag.

## Build / packaging notes

- `electron-builder.config.ts` disables dangerous Electron fuses (`runAsNode: false`, `enableNodeCliInspectArguments: false`, ASAR integrity, etc.) — don't re-enable without a strong reason
- Windows builds need VC++ redist; `scripts/download-vcredist.mjs` pulls it and `scripts/vcredist.nsh` bundles it into the NSIS installer
- Code signing: see `CODESIGN.md`. Local dev should set `CSC_IDENTITY_AUTO_DISCOVERY=false` in `.env` (see `.env.sample`) to stop electron-builder from grabbing a keychain cert

## Testing

- Vitest with jsdom. Tests colocated as `*.test.ts(x)`. Coverage via `@vitest/coverage-v8`.
- `vitest.config.ts` aliases `electron` → `src/server/electron-shim.ts` so main-process modules import cleanly without an Electron runtime.
- Unit tests exercise managers directly through the shim. User-story coverage lives in the Playwright suite under `tests/e2e/` (see `tests/e2e/README.md` and `AGENTS.md`).
