# Windows: WSL backend + native frontend

On Windows the desktop can run its backend inside a WSL2 distro while the UI
stays a native Windows Electron app. The backend is the same server build that
server mode uses (`out/server/index.mjs`), so `omni serve`, node-pty terminals
and the agent processes are all Linux-native, and running the whole Electron
app under WSLg (blurry scaling, broken tray/dialogs) is avoided.

Code and CI comments refer to the section labels below (Phase 2, Decision 8,
and so on); they are kept from the original plan.

## Linked backends

`StoreData.remoteBackend` (`src/shared/types.ts`) is `null` for the default
standalone Electron mode, or one of:

- `WslBackend` — `{ kind: 'wsl', distro, port, persistent? }`: the server build
  running as a daemon inside a WSL distro, authenticated with a shared secret
  that Electron main uses to mint WS tokens.
- `ServerBackend` — `{ kind: 'server', url }`: a self-hosted server-mode
  launcher the user runs (homelab, LAN, Tailscale). Main fetches WS tokens from
  that server's `/api/ws-token`, which only trusts loopback plus
  `OMNI_TRUSTED_CIDRS`.

Either way the renderer talks to the backend through `WsTransportEmitter`;
dialogs, shell, window and updater stay on a `localEmitter` to local main. The
preload bootstrap carries a resolved `{ kind: 'wsl', url }` so `serverOrigin()`
works unchanged. Disconnecting either kind clears `remoteBackend` and relaunches.

```
Windows                                        WSL2 distro
┌────────────────────────────┐                ┌─────────────────────────────┐
│ Electron shell             │                │ omni daemon                 │
│  renderer (native UI)      │   WS + HTTP    │  out/server/index.mjs       │
│  emitter ──────────────────┼───────────────▶│  Fastify :<port>            │
│  localEmitter → local main │ localhost:port │  managers, node-pty,        │
│                            │                │  omni serve                 │
│ main:                      │                │                             │
│  WslBackendManager ────────┼── wsl.exe ────▶│                             │
│   mints WS tokens (HMAC)   │  shared secret │  verifies same secret       │
└────────────────────────────┘                └─────────────────────────────┘
```

## IPC

All resolved in local main (`src/main/index.ts`), invoked via `localEmitter`:

- `wsl:detect` — `wsl.exe` presence and distro list.
- `wsl:install` — `wsl --install` from the app: `platform` runs an elevated
  `wsl.exe --install --no-launch`; `distro` registers Ubuntu.
- `wsl:link` — provisions the payload into a distro, persists `remoteBackend`,
  relaunches.
- `wsl:status`, `wsl:get-ws-token`, `wsl:set-persistent`.
- `server:link` / `server:get-ws-token` for the self-hosted kind.

## Daemon lifecycle (Phase 2)

`WslBackendManager` (`src/main/wsl-backend.ts`) is a manager factory like the
others, created unconditionally so `wsl:detect` works before linking; it no-ops
off Windows. When `remoteBackend.kind === 'wsl'`, main runs
provision → spawn → health check **before** creating the BrowserWindow.

- **Every in-distro command uses `wsl.exe -d <distro> --exec sh -c <script>`.**
  The plain `--` form re-joins argv and hands it to the user's default shell,
  which destroys quoting.
- **`wsl.exe` output is UTF-16LE with NULs.** `decodeWslOutput` handles it;
  distro parsing is fixture-tested.
- **Provisioning** streams the payload tarball into `~/.omni/launcher` through
  stdin, staged in a temp dir and swapped in, only when the in-distro `VERSION`
  differs from the launcher version. That is also the whole update story:
  electron-updater updates the Windows app and the next boot re-provisions.
- **Spawn** picks a free port, reaps any stale daemon via
  `~/.omni/launcher/daemon.pid`, and runs the bundled Node with the per-boot
  secret in `OMNI_WS_TOKEN_SECRET`. The daemon is a child of `wsl.exe` and dies
  with the app.
- **Health** polls `GET /api/health` (`{ ok, version }`), restarting with capped
  backoff; after repeated failures status turns `error` with the stderr tail.
- **Persistent mode** (`WslBackend.persistent`, toggled in Settings): the daemon
  is started detached with `nohup`, signs with a durable secret kept in the
  local secret store, survives app quit, and is adopted on the next boot if it
  is healthy and version-matched. A supervision health poll replaces the exit
  event for failure detection.

## Path translation (Phase 4)

Native dialogs return Windows paths; the daemon needs Linux paths. Translation
happens at one boundary only: `toBackendPath` in `src/main/index.ts`, using
`winToWslPath` from `src/lib/wsl-path.ts` (`C:\x` → `/mnt/c/x`,
`\\wsl$\<distro>\home\me` → `/home/me`). A path that cannot be translated maps
to `null`, like a cancelled dialog. The renderer and daemon only ever see Linux
paths; do not add consumer-side fallbacks.

## Payload and packaging (Phase 5, Decision 5)

The payload ships inside the Windows installer so provisioning works offline
and the daemon is always the same version as the renderer.
`scripts/build-wsl-payload.mjs` (`npm run build:wsl-payload`, Linux only, after
`npm run build:server`) produces `omni-wsl-payload.tar.gz` containing
`server/`, `browser/`, linux-x64 `node_modules` for the server-bundle externals
(node-pty has no prebuilds, so it is compiled here, never in the user's
distro), a pinned Node runtime under `node/`, and `VERSION`. CI builds it in the
`build-wsl-payload` job; `electron-builder.config.ts` adds it via
`extraResources` (from `OMNI_WSL_PAYLOAD` or `out/wsl-payload/`).

## Data roots (Decision 8)

Switching to WSL starts a fresh backend data root inside the distro. Existing
Windows-local data stays on Windows and is not migrated; the Settings card and
onboarding step say so. Daemon data (`~/.config/Omni Code/config.json`, the
omni-code config dir) lives outside `~/.omni/launcher`, so re-provisioning never
touches it.

## UI

- `RemoteBackendCard` (Settings) — link/unlink WSL or a self-hosted server,
  daemon status, persistent-mode switch, install actions.
- `OnboardingWslStep` — Windows-only, skippable first-run offer to run the
  backend in WSL before provider setup, so the rest of onboarding writes config
  into the backend that will actually be used.

## Known sharp edges

- WSL2 localhost forwarding can break under VPNs in NAT mode; health-check
  failure surfaces as an error rather than a hang.
- Sleep/resume drops the WS; `WsTransportEmitter` reconnects and
  `WsHandler.persistentSessions` keeps server-side state.
- Needs validation on real Windows: fresh install + onboarding, NAT and mirrored
  networking, app quit (daemon gone unless persistent), daemon crash restart,
  updater re-provisioning.
