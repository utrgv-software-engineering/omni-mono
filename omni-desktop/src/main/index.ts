if (process.env.NODE_ENV === 'development') {
  require('dotenv/config');
}

import { app, dialog, ipcMain, net, protocol, shell } from 'electron';
import { mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { assert } from 'tsafe';
import { pathToFileURL } from 'url';

import { parseEnvVars } from '@/lib/agent-config';
import { winToWslPath } from '@/lib/wsl-path';
import { createAppControlManager } from '@/main/app-control-manager';
import { listRepos as azureListRepos } from '@/main/azure-repos';
import { createBrowserManager } from '@/main/browser-manager';
import { cleanupRemovedChat } from '@/main/chat-removal';
import { createConsoleManager } from '@/main/console-manager';
import { createDownloadsManager } from '@/main/downloads-manager';
import {
  linkWithDeviceFlow as githubLink,
  listOrgs as githubListOrgs,
  searchRepos as githubSearchRepos,
} from '@/main/github-auth';
import { removeRetiredState } from '@/main/legacy-cleanup';
import { LocalCodexAccount } from '@/main/local-codex-account';
import { readLocalModels, writeLocalModels } from '@/main/local-model-config';
import { MainProcessManager } from '@/main/main-process-manager';
import { createOmniInstallManager } from '@/main/omni-install-manager';
import { createPermissionsManager } from '@/main/permissions-manager';
import { createProcessManager } from '@/main/process-manager';
import { refreshProductRuntimeInfo } from '@/main/product-runtime';
import { createProjectManager } from '@/main/project-manager';
import { importLegacyProjectsDb } from '@/main/projects-import';
import { RoutineBridge } from '@/main/routine-bridge';
import { registerScheduledTaskHandlers, ScheduledTaskManager } from '@/main/scheduled-task-manager';
import { LocalSecretStore } from '@/main/secret-store';
import { getStore } from '@/main/store';
import {
  ensureDirectory,
  getDefaultWorkspaceDir,
  getMcpSandboxHtmlPath,
  getOmniConfigDir,
  getProjectsDir,
  isDirectory,
  isFile,
} from '@/main/util';
import { getVoiceService } from '@/main/voice-service';
import { createWslBackendManager } from '@/main/wsl-backend';
import { tokenLast4 } from '@/shared/git-credentials';
import {
  registerConfigHandlers,
  registerGitCredentialHandlers,
  registerSettingsConfigHandlers,
  registerSkillsHandlers,
  registerUtilHandlers,
} from '@/shared/ipc-handlers';
import type { GitCredential, GithubOwner, GithubRepoQuery, GithubStatus, RemoteRepo } from '@/shared/types';

// Process-level crash visibility. Log only — do not exit. Killing the
// Electron main process from an unhandled rejection would take the whole
// UI down with it, which is worse than letting the rejection slip through.
// The goal here is leaving a stderr breadcrumb so we can debug instead of
// silently losing the failure.
process.on('unhandledRejection', (reason) => {
  console.error('[fatal] unhandledRejection:', reason);
});
process.on('uncaughtException', (err) => {
  console.error('[fatal] uncaughtException:', err);
});

protocol.registerSchemesAsPrivileged([
  // MCP Apps sandbox proxy origin. Registered as a separate, opaque
  // origin so the AppFrame iframe (mcp-ui) is cross-origin isolated from
  // the renderer. ``bypassCSP`` is intentionally not set — the handler
  // sets a strict CSP that lets the proxy script run but blocks anything
  // it loads (apart from the inner iframe written via document.write).
  {
    scheme: 'mcp-sandbox',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
    },
  },
]);

// Configure Chrome/Electron flags for better memory management

// Windows-specific, disables some fancy desktop window effects that can use a lot of memory
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');

// Prevent memory spikes from throttling when the app is in the background and moves to foreground
app.commandLine.appendSwitch('disable-background-timer-throttling');

// Keep renderer active when minimized to avoid memory spikes when restoring
app.commandLine.appendSwitch('disable-renderer-backgrounding');

// Remove limits on number of backing stores, which are per-window/tab. Theoretically, the launcher should only have two
// windows open at a time so this should have no effect. But just in case, we disable the limit.
app.commandLine.appendSwitch('disable-backing-store-limit');

// Chromium caps a renderer at 6 sockets per host. The shared AgentHost puts
// EVERY column's chat WS, terminal WS, realtime WS, and ticket fetch on one
// loopback origin, so the 7th connection — typically a reconnect's
// /auth/ws-ticket fetch — queues in the socket pool forever and the column
// hangs at "Connecting…" with zero packets on the wire. Lift the cap for
// loopback only; remote origins keep Chromium's default limits.
app.commandLine.appendSwitch('ignore-connections-limit', '127.0.0.1,localhost');

// Expose a Chrome DevTools Protocol endpoint in development so external tools
// (chrome://inspect, puppeteer, `curl http://localhost:9222/json`) can attach
// to the running renderer without restarting. Opt-in via OMNI_DEBUG_PORT.
if (process.env.NODE_ENV === 'development' || process.env.OMNI_DEBUG_PORT) {
  const port = process.env.OMNI_DEBUG_PORT ?? '9222';
  app.commandLine.appendSwitch('remote-debugging-port', port);
  // Chromium 111+ requires this to allow non-browser clients to connect.
  app.commandLine.appendSwitch('remote-allow-origins', '*');
  console.log(`[debug] Chrome DevTools Protocol listening on http://localhost:${port}`);
}

const OMNI_CONFIG_DIR = getOmniConfigDir();
const store = getStore();
const secretStore = new LocalSecretStore();
importLegacyProjectsDb(store);
void removeRetiredState(OMNI_CONFIG_DIR).catch((error) => {
  console.warn('[legacy-cleanup] failed:', error);
});

/**
 * Write the user's Settings → Environment vars to `<config>/.env`, which omni
 * serve reads. Runs at startup and after every `settings:*` write. MCP servers
 * and models live in the runtime's own config files.
 */
function writeDesktopEnvFile(): void {
  try {
    // First launch runs before the runtime has created its config dir.
    mkdirSync(OMNI_CONFIG_DIR, { recursive: true });
    writeFileSync(join(OMNI_CONFIG_DIR, '.env'), store.get('envVars') ?? '', 'utf-8');
  } catch (err) {
    console.error('[config] writing .env failed:', err);
  }
}
writeDesktopEnvFile();

const main = new MainProcessManager({ store });
let isShuttingDown = false;

// Forward-reference for the BrowserManager — created further down, but
// AppControlManager needs its popup callback at construction time so
// `setWindowOpenHandler` can route `window.open` into `BrowserManager.createTab`.
let browserManagerRef: ReturnType<typeof createBrowserManager>[0] | null = null;
const [, cleanupAppControl] = createAppControlManager({
  ipc: main.ipc,
  onBrowserPopup: (tabsetId, url, disposition) => {
    if (!browserManagerRef) {
      return;
    }
    // `background-tab` maps to Cmd/Ctrl+click: open without stealing focus.
    // Everything else (`foreground-tab`, `new-window`, `default`) activates.
    const activate = disposition !== 'background-tab';
    try {
      browserManagerRef.createTab(tabsetId, { url, activate });
    } catch {
      // Tabset may not exist yet (race on first mount) — ignore.
    }
  },
});
const [omniInstall, cleanupOmniInstall] = createOmniInstallManager({
  ipc: main.ipc,
  sendToWindow: main.sendToWindow,
});
const [processManager, cleanupProcessManager] = createProcessManager({
  ipc: main.ipc,
  sendToWindow: main.sendToWindow,
  fetchFn: (input, init) => net.fetch(input as string, init),
  getStoreData: () => ({
    projects: store.get('projects') ?? [],
    gitCredentials: store.get('gitCredentials') ?? [],
  }),
  resolveGitToken: (id) => secretStore.getGitToken(id),
  waitForRuntimeInstall: () => omniInstall.waitForInstallCompletion(),
  // Inject the user's Settings → Environment vars into the `omni serve` process
  // (the agent/model loop), mirroring server mode's getExtraEnv. The sandbox
  // *container* gets these separately via the materialized `<config>/.env`,
  // which omni serve folds into manifest.environment (`_inject_user_env`).
  getExtraEnv: () => parseEnvVars(store.get('envVars') ?? ''),
});
const localCodexAccount = new LocalCodexAccount({
  runtime: {
    status: () => processManager.getManagementAccountStatus(),
    mutate: (request) => processManager.mutateManagement(request),
  },
});
const routineBridge = new RoutineBridge(main.sendToWindow);
const scheduledTaskManager = new ScheduledTaskManager({
  store,
  bridge: routineBridge,
  sendToWindow: main.sendToWindow,
  // Composed snapshot for broadcasts — read at call time; getStoreSnapshot
  // is assigned after ProjectManager boots.
  getSnapshot: () => store.store,
});
const scheduledTaskChannels = [
  ...registerScheduledTaskHandlers(main.ipc, () => scheduledTaskManager),
  ...routineBridge.registerIpc(main.ipc),
];
scheduledTaskManager.start();

// Create ConsoleManager — proxies terminal:* IPC into omni serve's
// WebSocket. Constructed after ProcessManager because it needs the
// agent process status to find the right WS URL per tab.
const [consoleManager, cleanupConsole] = createConsoleManager({
  ipc: main.ipc,
  sendToWindow: main.sendToWindow,
  processManager,
});

main.cleanupRemovedChat = (tab) =>
  cleanupRemovedChat(tab, {
    disposeTerminals: (id) => consoleManager.disposeAllForTab(id),
    stop: (id) => processManager.retire(id),
  });

const [, cleanupProject] = createProjectManager({
  ipc: main.ipc,
  store,
});
const [browserManager, cleanupBrowser] = createBrowserManager({
  ipc: main.ipc,
  sendToWindow: main.sendToWindow,
  store,
});
browserManagerRef = browserManager;
const [, cleanupDownloads] = createDownloadsManager({
  ipc: main.ipc,
  sendToWindow: main.sendToWindow,
});
const [, cleanupPermissions] = createPermissionsManager({
  ipc: main.ipc,
  sendToWindow: main.sendToWindow,
});
// WSL backend daemon lifecycle (Windows only; docs/windows-wsl-backend-plan.md).
// Created unconditionally so `wsl:detect` works pre-link; the daemon itself
// only boots when `store.remoteBackend.kind === 'wsl'` (see the app-ready
// sequencing below). Non-Windows platforms no-op inside the manager.
// Persistent daemon mode signs tokens with a durable secret kept in the
// LocalSecretStore under a stable id, so a daemon that outlived the previous
// app session can be adopted on the next boot.
const WSL_BACKEND_SECRET_ID = 'wsl-backend-secret';
const [wslBackend, cleanupWslBackend] = createWslBackendManager({
  store,
  launcherVersion: app.getVersion(),
  secrets: {
    getSecret: async () => (await secretStore.getGitToken(WSL_BACKEND_SECRET_ID)) ?? null,
    setSecret: (value) => secretStore.setGitToken(WSL_BACKEND_SECRET_ID, value),
    deleteSecret: () => secretStore.deleteGitToken(WSL_BACKEND_SECRET_ID),
  },
});
main.ipc.handle('main-process:get-status', () => main.getStatus());

main.ipc.handle('omni-install-process:get-status', () => omniInstall.getStatus());

// Local voice (Option A): launcher-side STT/TTS via the ONNX sidecar. Works in
// every Electron mode (compute runs wherever, but the mic + STT/TTS + the
// `speak` client tool all execute here on the user's machine).
const voice = getVoiceService();
main.ipc.handle('voice:get-status', () => voice.getStatus());
main.ipc.handle('voice:start', async () => {
  await voice.start();
  return voice.getStatus();
});
main.ipc.handle('voice:transcribe', (_e, pcmBase64, sampleRate) => voice.transcribe(pcmBase64, sampleRate));
main.ipc.handle('voice:speak', async (_e, streamId, text, voiceName) => {
  await voice.speak(
    text,
    (pcm, sampleRate) => main.sendToWindow('voice:audio', { streamId, pcm, sampleRate }),
    voiceName
  );
  main.sendToWindow('voice:audio-end', { streamId });
});
main.ipc.handle('voice:import-sample', (_e, personaId, filename, dataBase64) =>
  voice.importSample(personaId, filename, dataBase64)
);

//#region App lifecycle

/**
 * Cleans up any running processes.
 */
async function cleanup() {
  if (isShuttingDown) {
    return;
  }
  isShuttingDown = true;

  // Non-persistent mode: kills the wsl.exe child, terminating the in-distro
  // daemon. Persistent mode: only stops timers/loops — the daemon keeps
  // running and is adopted on the next boot.
  cleanupWslBackend();
  const results = await Promise.allSettled([
    cleanupConsole(),
    cleanupAppControl(),
    cleanupOmniInstall(),
    (async () => {
      scheduledTaskManager.stop();
      routineBridge.disposeAll();
      for (const channel of scheduledTaskChannels) {
        ipcMain.removeHandler(channel);
      }
    })(),
    cleanupProcessManager(),
    cleanupProject(),
    cleanupBrowser(),
    cleanupDownloads(),
    cleanupPermissions(),
  ]);
  const errors = results
    .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
    .map((result) => result.reason);

  if (errors.length > 0) {
    console.error('Error cleaning up processes:', errors);
  } else {
    console.debug('Successfully cleaned up all processes');
  }
  main.cleanup();
}

/**
 * This method will be called when Electron has finished initialization and is ready to create browser windows.
 * Some APIs can only be used after this event occurs.
 */
app.on('ready', () => {
  // Introspect the installed product (`<prog> describe --json`) so identity
  // and config-dir consumers use the product-reported values. Best-effort:
  // convention-based fallbacks cover the not-yet-installed case.
  void refreshProductRuntimeInfo();

  // MCP Apps sandbox proxy. Serves the vendored mcp-ui ``index.html``
  // (assets/mcp-sandbox/) at ``mcp-sandbox://app/index.html``. The
  // AppFrame iframe loads this URL to host a cross-origin sandbox for
  // MCP-Apps tool UIs. CSP allows inline script (the proxy itself is a
  // small inline script) but blocks network loads — guest HTML is
  // delivered to the proxy via postMessage and written into a nested
  // iframe via document.write, where it runs without script privileges
  // unless the inner iframe's sandbox attribute permits it.
  protocol.handle('mcp-sandbox', async (request) => {
    try {
      const url = new URL(request.url);
      // Only one resource is served — any path returns the same HTML.
      // The query string is preserved by the browser and read by the
      // proxy script (contentType=rawhtml or ?url=...).
      void url;
      const htmlPath = getMcpSandboxHtmlPath();
      const upstream = await net.fetch(pathToFileURL(htmlPath).toString());
      const headers = new Headers(upstream.headers);
      headers.set('Content-Type', 'text/html; charset=utf-8');
      headers.set(
        'Content-Security-Policy',
        // ``script-src 'unsafe-inline' https:`` lets renderer-HTML import
        // its component runtime from a CDN (Prefab's renderer loads from
        // ``cdn.jsdelivr.net``, generative-ui-style apps may pull from
        // other origins). ``style-src`` mirrors so stylesheets load.
        // ``font-src`` + ``img-src`` permit referenced assets;
        // ``connect-src`` permits any fetch/XHR/WebSocket the renderer
        // makes back to its own backend. ``frame-src https: http:`` keeps
        // MCP-Apps ``externalUrl`` (text/uri-list) embedding working.
        [
          "default-src 'none'",
          "script-src 'unsafe-inline' https:",
          "style-src 'unsafe-inline' https:",
          'font-src https: data:',
          'img-src https: data: blob:',
          'connect-src https: wss: ws: data: blob:',
          'frame-src about: data: blob: https: http:',
        ].join('; ')
      );
      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers,
      });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });

  // WSL backend boot sequencing: provision → reap → spawn → health-wait
  // BEFORE the window exists, because the renderer's bootstrap URL
  // (main-process-manager.ts) derives from the port boot() persists into
  // `store.remoteBackend`. boot() never throws and caps its wait (120s when
  // it provisioned this boot, else 30s) — on timeout or daemon failure the
  // window is created anyway; the WS transport auto-reconnects and the
  // settings card surfaces the error status.
  void (async () => {
    const backend = store.get('remoteBackend');
    if (backend?.kind === 'wsl') {
      await wslBackend.boot(backend);
    }
    main.createWindow();
  })();

  if (process.env.OMNI_CI_AUTOINSTALL) {
    void (async () => {
      console.log('[OMNI_CI] starting auto-install');
      try {
        await omniInstall.startInstall();
      } catch (err) {
        console.error('[OMNI_CI] startInstall threw:', err);
      }
      const status = omniInstall.getStatus();
      console.log(`[OMNI_CI] final status: ${status.type}`);
      app.exit(status.type === 'completed' ? 0 : 1);
    })();
  }

  // Ensure workspace and projects directories exist on startup
  void ensureDirectory(getDefaultWorkspaceDir())
    .then(() => ensureDirectory(getProjectsDir()))
    .catch((err) => console.warn('Failed to create workspace directories:', err));
});

/**
 * Quit when all windows are closed.
 */
app.on('window-all-closed', () => {
  if (!isShuttingDown) {
    app.quit();
  }
});

/**
 * Run cleanup with a hard timeout, then exit. Shared by before-quit and the
 * POSIX signal handlers below.
 */
function shutdownWithTimeout() {
  // Hard timeout: if cleanup hangs, force-exit the process
  const forceExitTimer = setTimeout(() => {
    console.error('Cleanup timed out after 15s, forcing exit');
    app.exit(1);
  }, 15_000);

  cleanup().finally(() => {
    clearTimeout(forceExitTimer);
    app.exit(0);
  });
}

/**
 * When the launcher quits, cleanup any running processes.
 * We prevent the default quit, await all cleanup (which uses SIGTERM → SIGKILL internally),
 * then force-exit the app. A hard 15s timeout ensures the app never hangs indefinitely.
 */
app.on('before-quit', (event) => {
  if (isShuttingDown) {
    return;
  }

  event.preventDefault();
  shutdownWithTimeout();
});

// Ctrl+C in a dev terminal (`npm run dev`) or a plain `kill`: route through
// the same cleanup as before-quit. Chromium's own signal handling tears the
// process down without reliably emitting the quit events, so handle the
// signals in Node land. A second signal while cleanup is in flight forces an
// immediate exit — the serve children were signalled too (same foreground
// process group) and finish their own teardown regardless.
for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    if (isShuttingDown) {
      app.exit(1);
      return;
    }
    console.log(`Received ${sig}, cleaning up...`);
    shutdownWithTimeout();
  });
}

//#endregion

//#region Shared IPC handlers (config:*, util:*, skills:*)

registerConfigHandlers(main.ipc, OMNI_CONFIG_DIR);
registerUtilHandlers(main.ipc, {
  fetchFn: ((input, init) => net.fetch(input as string, init)) as typeof globalThis.fetch,
  launcherVersion: app.getVersion(),
});
registerSkillsHandlers(
  main.ipc,
  () => OMNI_CONFIG_DIR,
  () => store
);
registerSettingsConfigHandlers(
  main.ipc,
  () => store,
  () => {
    writeDesktopEnvFile();
    main.sendToWindow('store:changed', store.store);
  },
  {
    readModels: () => readLocalModels(OMNI_CONFIG_DIR),
    saveModels: (_event, config) => writeLocalModels(OMNI_CONFIG_DIR, config),
  }
);
registerGitCredentialHandlers(
  main.ipc,
  () => store,
  () => secretStore,
  () => {
    main.sendToWindow('store:changed', store.store);
  }
);

//#endregion

//#region Electron-only util handlers (dialog, shell)

/**
 * WSL-backend path translation, at this single boundary only (plan Phase 4):
 * native dialogs return Windows paths, but in WSL mode the renderer and the
 * daemon only ever see Linux paths. A pick that doesn't translate (e.g. a
 * non-WSL UNC share) maps to `null` — the same shape as a cancelled dialog.
 */
const toBackendPath = (path: string | null): string | null => {
  if (path === null || store.get('remoteBackend')?.kind !== 'wsl') {
    return path;
  }
  return winToWslPath(path);
};

main.ipc.handle('util:select-directory', async (_, path) => {
  const mainWindow = main.getWindow();
  assert(mainWindow !== null, 'Main window is not initialized');

  const defaultPath = path && (await isDirectory(path)) ? path : app.getPath('home');

  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'createDirectory'],
    defaultPath,
  });

  return toBackendPath(result.filePaths[0] ?? null);
});
main.ipc.handle('util:select-file', async (_, path, filters) => {
  const mainWindow = main.getWindow();
  assert(mainWindow !== null, 'Main window is not initialized');

  const defaultPath = path && (await isFile(path)) ? path : app.getPath('home');

  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    defaultPath,
    filters: filters ?? undefined,
  });

  return toBackendPath(result.filePaths[0] ?? null);
});
main.ipc.handle('util:open-directory', (_, path) => shell.openPath(path));
main.ipc.handle('util:open-external', (_, url) => shell.openExternal(url));

//#endregion

//#region Codex (ChatGPT OAuth) handlers

main.ipc.handle('codex:link', () => localCodexAccount.link((code) => main.sendToWindow('codex:device-code', code)));
main.ipc.handle('codex:logout', () => localCodexAccount.logout());

//#endregion

//#region Remote backend link

// Hoisted from the GitHub block below so both regions can fan store changes
// out to the renderer.
const broadcastStore = (): void => main.sendToWindow('store:changed', store.store);

/** Restart the Electron app on a short delay so the IPC reply (the new
 *  ``undefined``) flushes to the renderer first. The transport choice is
 *  baked into the BrowserWindow at creation via ``additionalArguments``, so
 *  flipping the remote backend requires a fresh process — not just a
 *  webContents reload. 200ms is enough for the response handshake without
 *  making the UI feel laggy. */
const restartAfterRemoteBackendChange = (): void => {
  setTimeout(() => {
    app.relaunch();
    app.exit(0);
  }, 200);
};

// Shared disconnect path for every remote-backend kind (wsl, server): clears
// the flag and restarts back into standalone-local mode.
main.ipc.handle('remote-backend:unlink', async () => {
  const prev = store.get('remoteBackend');
  // A WSL daemon — persistent or not — must not outlive the link: reap it and
  // drop the durable secret so nothing keeps running (or listening) orphaned.
  if (prev?.kind === 'wsl') {
    await wslBackend.unlink(prev.distro);
  }
  store.set('remoteBackend', null);
  broadcastStore();
  // The live renderer is still configured to use the (now broken) backend
  // transport — restart so it falls back to local Electron IPC.
  restartAfterRemoteBackendChange();
});

//#endregion

//#region WSL backend (Windows-only daemon lifecycle — docs/windows-wsl-backend-plan.md)

main.ipc.handle('wsl:detect', () => wslBackend.detect());

main.ipc.handle('wsl:status', () => wslBackend.getStatus());

// Signed locally with the daemon's per-boot shared secret — no HTTP fetch
// (Decision 4: shared secret, not network trust).
main.ipc.handle('wsl:get-ws-token', () => wslBackend.getWsToken());

main.ipc.handle('wsl:link', async (_, distroInput) => {
  const distro = String(distroInput ?? '').trim();
  if (!distro) {
    throw new Error('WSL distro name is required');
  }
  const detected = await wslBackend.detect();
  if (detected.wsl !== 'ok' || !detected.distros.some((d) => d.name === distro)) {
    throw new Error(`WSL distro "${distro}" not found`);
  }
  // Provision inline so failures surface to the caller before any restart.
  await wslBackend.provisionIfNeeded(distro);
  // port 0 is a placeholder — boot() re-picks and persists the real port
  // before window creation on the next launch.
  store.set('remoteBackend', { kind: 'wsl', distro, port: 0 });
  broadcastStore();
  restartAfterRemoteBackendChange();
});

// Mode transition: the manager persists the flag into `store.remoteBackend`
// and restarts the daemon into the new lifecycle (tracked child ↔ detached
// nohup) — the settings card copy warns about the restart.
main.ipc.handle('wsl:set-persistent', async (_, persistent) => {
  await wslBackend.setPersistent(Boolean(persistent));
  broadcastStore();
});

// `wsl --install` driven from the app: 'platform' launches an elevated
// one-shot (UAC; completion unobservable), 'distro' registers Ubuntu without
// elevation. The card re-runs `wsl:detect` afterwards — no store change here.
main.ipc.handle('wsl:install', (_, mode) => wslBackend.install(mode === 'platform' ? 'platform' : 'distro'));

//#endregion

//#region Self-hosted server link (Electron ↔ user-run server-mode launcher)

// The wsl token flow minus wsl.exe: no Entra, no daemon lifecycle — the user
// runs the server themselves; we only validate reachability + token access
// at link time and fetch WS tokens from it thereafter. Both fetches run in
// main (Node fetch, no CORS) because the server gates /api/ws-token by
// caller address (loopback + OMNI_TRUSTED_CIDRS), not by identity.

/** GET ``<url>/api/ws-token`` and return the opaque token. Shared by link
 *  validation and the steady-state `server:get-ws-token` handler so the 403
 *  guidance (OMNI_TRUSTED_CIDRS) is identical in both. */
const fetchServerWsToken = async (url: string): Promise<string> => {
  let res: Response;
  try {
    res = await net.fetch(`${url}/api/ws-token`);
  } catch (e) {
    throw new Error(`Could not reach ${url}/api/ws-token: ${e instanceof Error ? e.message : String(e)}`);
  }
  if (res.status === 403) {
    throw new Error(
      `${url} refused to mint a token (403). The server only trusts loopback by default — add this machine's network to OMNI_TRUSTED_CIDRS on the server (e.g. 100.64.0.0/10 for Tailscale).`
    );
  }
  if (!res.ok) {
    throw new Error(`Server ws-token fetch failed: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as { token?: string };
  if (!data.token) {
    throw new Error('Server ws-token response missing "token" field');
  }
  return data.token;
};

main.ipc.handle('server:link', async (_, urlInput) => {
  const url = String(urlInput ?? '')
    .trim()
    .replace(/\/+$/, '');
  if (!url) {
    throw new Error('Server URL is required');
  }
  if (!/^https?:\/\//i.test(url)) {
    throw new Error('Server URL must start with http:// or https://');
  }
  // Validate BEFORE persisting — a bad URL must not survive into the store,
  // or the next boot dials a dead backend and the app looks bricked.
  let healthRes: Response;
  try {
    healthRes = await net.fetch(`${url}/api/health`);
  } catch (e) {
    throw new Error(
      `Could not reach ${url}/api/health: ${e instanceof Error ? e.message : String(e)}. Is the server running and reachable from this machine?`
    );
  }
  if (!healthRes.ok) {
    throw new Error(`${url}/api/health answered ${healthRes.status}. Is this a launcher server URL?`);
  }
  const health = (await healthRes.json().catch(() => null)) as { ok?: boolean } | null;
  if (health?.ok !== true) {
    throw new Error(`${url}/api/health did not report ok — is this a launcher server URL?`);
  }
  // Token access is the actual gate — proving it now surfaces the
  // OMNI_TRUSTED_CIDRS misconfiguration at link time instead of as a silent
  // WS auth loop after the relaunch.
  await fetchServerWsToken(url);
  store.set('remoteBackend', { kind: 'server', url });
  broadcastStore();
  restartAfterRemoteBackendChange();
});

main.ipc.handle('server:get-ws-token', async () => {
  const backend = store.get('remoteBackend');
  if (backend?.kind !== 'server') {
    throw new Error('Not connected to a self-hosted server');
  }
  return fetchServerWsToken(backend.url);
});

//#endregion

//#region GitHub account linking (OAuth device flow → github.com credential)

// Stable credential id for the OAuth-linked github.com token, so link / unlink /
// clone-time injection all reference the same SecretStore slot.
const GITHUB_CRED_ID = 'github-oauth';
const githubFetch = ((input, init) => net.fetch(input as string, init)) as typeof globalThis.fetch;

const githubStatus = (): GithubStatus => {
  const account = store.get('githubAccount');
  return account ? { connected: true, account } : { connected: false };
};

main.ipc.handle('github:status', () => githubStatus());

main.ipc.handle('github:link', async () => {
  const { token, account } = await githubLink({
    fetchFn: githubFetch,
    openUrl: (url) => void shell.openExternal(url),
    onCode: (code) => main.sendToWindow('github:device-code', code),
  });
  // The token becomes the host's git credential (replacing any prior entry for
  // that host), so private clone/push works through the same injection path.
  await secretStore.setGitToken(GITHUB_CRED_ID, token);
  const creds = (store.get('gitCredentials') ?? []).filter((c) => c.id !== GITHUB_CRED_ID && c.host !== account.host);
  const cred: GitCredential = {
    id: GITHUB_CRED_ID,
    host: account.host,
    username: 'x-access-token',
    last4: tokenLast4(token),
    label: `@${account.login} (GitHub)`,
    createdAt: Date.now(),
  };
  store.set('gitCredentials', [...creds, cred]);
  store.set('githubAccount', account);
  broadcastStore();
  return githubStatus();
});

main.ipc.handle('github:unlink', async () => {
  await secretStore.deleteGitToken(GITHUB_CRED_ID);
  store.set(
    'gitCredentials',
    (store.get('gitCredentials') ?? []).filter((c) => c.id !== GITHUB_CRED_ID)
  );
  store.delete('githubAccount');
  broadcastStore();
});

const requireGithubToken = async (): Promise<string> => {
  const token = await secretStore.getGitToken(GITHUB_CRED_ID);
  if (!token) {
    throw new Error('No GitHub account linked');
  }
  return token;
};

main.ipc.handle('github:list-owners', async (): Promise<GithubOwner[]> => {
  const token = await requireGithubToken();
  const account = store.get('githubAccount');
  // The linked user is always the first owner; their orgs follow.
  const self: GithubOwner[] = account
    ? [{ login: account.login, kind: 'user', ...(account.avatarUrl ? { avatarUrl: account.avatarUrl } : {}) }]
    : [];
  return [...self, ...(await githubListOrgs(githubFetch, token))];
});

main.ipc.handle('github:search-repos', async (_, query: GithubRepoQuery): Promise<RemoteRepo[]> => {
  return githubSearchRepos(githubFetch, await requireGithubToken(), query);
});

//#endregion

//#region Azure DevOps discovery (authenticated by the stored dev.azure.com PAT)

const requireAzureToken = async (): Promise<string> => {
  const cred = (store.get('gitCredentials') ?? []).find((c) => c.host === 'dev.azure.com');
  const token = cred ? await secretStore.getGitToken(cred.id) : undefined;
  if (!token) {
    throw new Error('No Azure DevOps token — add a dev.azure.com credential first');
  }
  return token;
};

main.ipc.handle('azure:list-repos', async (_, input: { org: string; query: string }): Promise<RemoteRepo[]> => {
  return azureListRepos(githubFetch, await requireAzureToken(), input.org, input.query);
});

//#endregion
