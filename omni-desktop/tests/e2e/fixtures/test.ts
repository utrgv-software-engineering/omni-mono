import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import {
  _electron as electron,
  type Browser,
  type BrowserContext,
  type ElectronApplication,
  expect,
  type Page,
  test as base,
  type TestInfo,
} from '@playwright/test';
import electronExecutablePath from 'electron';
import { startDeterministicModelServer } from 'tests/e2e/support/model-server';
import type { ManagedProcess } from 'tests/e2e/support/process';
import {
  commandLine,
  crashProcessTree,
  killTcpPort,
  processTree,
  runtimePidsUnder,
  startProcess,
  waitForHttpOk,
} from 'tests/e2e/support/process';
import { attachProofVideo, visualProofEnabled } from 'tests/e2e/support/proof';
import {
  createE2eState,
  type E2eCodexCredentialState,
  type E2eMcpConfigState,
  type E2eState,
  inspectElectronCodexCredential,
  inspectElectronMcpConfig,
  seedElectronState,
  seedServerState,
  type SeedState,
} from 'tests/e2e/support/state';

import type { CodeTab } from '@/shared/types';

export type LaunchMode = 'server-local' | 'electron-local';

export type E2eOptions = {
  launchMode: LaunchMode;
  seedState: SeedState;
};

type E2eFixtures = E2eOptions & {
  app: E2eApp;
  appPage: Page;
  mode: LaunchMode;
};

type LaunchedApp = {
  page: Page;
  /** The launcher process whose descendants include its `omni serve` hosts. */
  rootPid: () => number | undefined;
  releaseBackground?: () => void;
  resizeWindow?: (width: number, height: number) => Promise<void>;
  setMinimized?: (minimized: boolean) => Promise<void>;
  captureScreenshot: () => Promise<Buffer>;
  close: (options?: { crash?: boolean; backendOnly?: boolean }) => Promise<void>;
  restartBackend?: () => Promise<void>;
  inspectModelRequests?: () => { marker: string; model: string }[];
};

type E2eApp = {
  setProviderFromCli: (model: string) => void;
  readonly page: Page;
  releaseBackground: () => void;
  resizeWindow: (width: number, height: number) => Promise<void>;
  setMinimized: (minimized: boolean) => Promise<void>;
  readonly workspaceDir: string;
  captureScreenshot: () => Promise<Buffer>;
  inspectCodexCredential: () => E2eCodexCredentialState;
  inspectMcpConfig: () => E2eMcpConfigState;
  inspectModelRequests: () => { marker: string; model: string }[];
  restart: (options?: { crash?: boolean; backendOnly?: boolean }) => Promise<Page>;
  /** Chat tabs as persisted in the launcher store. */
  storedTabs: () => CodeTab[];
  /** Live `omni serve` hosts spawned by the current launcher. Linux only. */
  runtimePids: () => number[];
  /** Process-level totals across the live hosts. Linux only. */
  inspectRuntimeResources: () => {
    hosts: number;
    rssBytes: number;
    fileDescriptors: number;
    childProcesses: number;
    workerPids: number[];
  };
};

const serverUrl = process.env.E2E_SERVER_URL ?? 'http://127.0.0.1:3001/';
const proofViewport = { width: 1920, height: 1080 };

function videoOptions(testInfo: TestInfo, launchIndex: number) {
  return visualProofEnabled
    ? {
        dir: testInfo.outputPath(`videos-${launchIndex}`),
        size: proofViewport,
        showActions: { duration: 900, position: 'bottom-right' as const, fontSize: 18 },
      }
    : undefined;
}

function viewportOptions() {
  return visualProofEnabled ? { viewport: proofViewport } : {};
}

async function attachPageVideo(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  const video = page.video();
  const path = video ? await video.path().catch(() => null) : null;
  await attachProofVideo(testInfo, name, path);
}

async function launchServerLocal(
  browser: Browser,
  state: E2eState,
  testInfo: TestInfo,
  launchIndex: number,
  seedState: SeedState
): Promise<LaunchedApp> {
  let serverProcess: ManagedProcess | null = null;
  let context: BrowserContext | null = null;
  const responseText = seedState === 'lazy-host-first-message' ? 'HOST_FIRST_MESSAGE_READY' : null;
  const modelServer =
    responseText && !process.env.E2E_REAL_MODELS_FILE && !process.env.E2E_SERVER_URL
      ? await startDeterministicModelServer(responseText)
      : null;

  // The teardown frees the port with `fuser -k`; never let it hit a server
  // this fixture did not start.
  if (!process.env.E2E_SERVER_URL) {
    const occupied = await fetch(serverUrl, { signal: AbortSignal.timeout(2_000) }).then(
      () => true,
      () => false
    );
    if (occupied) {
      throw new Error(`${serverUrl} is already serving; stop it or set E2E_SERVER_URL to reuse it`);
    }
  }

  const startBackend = () => {
    if (process.env.E2E_SERVER_URL) {
      return;
    }
    const parsed = new URL(serverUrl);
    serverProcess = startProcess({
      command: 'npm',
      args: ['run', 'start:server'],
      cwd: process.cwd(),
      env: {
        HOME: state.homeDir,
        XDG_CONFIG_HOME: state.xdgConfigHome,
        HOST: parsed.hostname,
        PORT: parsed.port || '3001',
        ...(process.env.OMNI_PROXY_DYNAMIC_RUNTIME_SHIMS
          ? { OMNI_PROXY_DYNAMIC_RUNTIME_SHIMS: process.env.OMNI_PROXY_DYNAMIC_RUNTIME_SHIMS }
          : {}),
        ...(process.env.OMNI_PROXY_RUNTIME_SHIMS
          ? { OMNI_PROXY_RUNTIME_SHIMS: process.env.OMNI_PROXY_RUNTIME_SHIMS }
          : {}),
        OPENAI_BASE_URL:
          modelServer?.baseUrl ??
          process.env.OPENAI_BASE_URL ??
          process.env.SANDBOX_OPENAI_BASE_URL ??
          'http://127.0.0.1:9/v1',
        OPENAI_API_KEY: process.env.OPENAI_API_KEY ?? process.env.SANDBOX_OPENAI_API_KEY ?? 'test-key',
      },
      forwardOutput: process.env.E2E_FORWARD_SERVER_LOGS === '1',
    });
  };
  startBackend();

  // Kill only the launcher's node server; its `omni serve` hosts must notice
  // stdin EOF and exit on their own.
  const crashBackend = () => {
    const server = serverProcess;
    const backendPid = server?.pid
      ? processTree(server.pid).find((pid) => commandLine(pid).some((arg) => arg.endsWith('out/server/index.mjs')))
      : undefined;
    if (!server || !backendPid) {
      throw new Error('Test launcher backend process not found');
    }
    server.crashDescendant(backendPid);
  };

  await waitForHttpOk(serverUrl, 90_000);
  context = await browser.newContext({ ...viewportOptions(), recordVideo: videoOptions(testInfo, launchIndex) });
  const page = await context.newPage();
  await page.goto(serverUrl, { waitUntil: 'domcontentloaded' });

  return {
    page,
    rootPid: () => (serverProcess as ManagedProcess | null)?.pid,
    captureScreenshot: () => page.screenshot({ animations: 'disabled' }),
    inspectModelRequests: () => [...(modelServer?.observations ?? [])],
    releaseBackground: () => modelServer?.releaseBackground(),
    restartBackend: async () => {
      if (process.env.E2E_SERVER_URL) {
        throw new Error('Cannot restart an externally managed server');
      }
      // Keep the browser context (including IndexedDB drafts/uploads) and
      // deterministic model endpoint alive. A backend crash is not a fresh
      // browser profile, and must exercise the real renderer reconnect path.
      crashBackend();
      startBackend();
      await waitForHttpOk(serverUrl, 90_000);
    },
    close: async (options) => {
      await context?.close().catch(() => undefined);
      await attachPageVideo(page, testInfo, `server-local video ${launchIndex}`);
      if (options?.backendOnly) {
        crashBackend();
      } else {
        await serverProcess?.stop(options?.crash ? 'SIGKILL' : 'SIGTERM');
      }
      await modelServer?.close();
      if (serverProcess) {
        await killTcpPort(new URL(serverUrl).port || '3001');
      }
    },
  };
}

async function launchElectronLocal(
  state: E2eState,
  testInfo: TestInfo,
  launchIndex: number,
  seedState: SeedState
): Promise<LaunchedApp> {
  // When the suite is launched from another Electron host (for example Codex
  // Desktop), do not inherit that host's renderer URL. electron-vite uses this
  // variable to select a dev renderer, which would make the child Electron
  // window display the host application instead of this built test app.
  const { ELECTRON_RENDERER_URL: _hostRendererUrl, ...electronEnv } = process.env;
  const firstMessageResponse = seedState === 'lazy-host-first-message' ? 'HOST_FIRST_MESSAGE_READY' : null;
  const modelServer =
    firstMessageResponse && !process.env.E2E_REAL_MODELS_FILE
      ? await startDeterministicModelServer(firstMessageResponse)
      : null;
  const electronApp: ElectronApplication = await electron.launch({
    // Under Node the `electron` package exports its binary path, not the API.
    executablePath: electronExecutablePath as unknown as string,
    // E2E_ELECTRON_OZONE=wayland targets a headless compositor (set
    // WAYLAND_DISPLAY/XDG_RUNTIME_DIR to it) instead of the live desktop.
    args: [
      ...(process.env.E2E_ELECTRON_X11 === '1' ? ['--ozone-platform=x11'] : []),
      ...(process.env.E2E_ELECTRON_OZONE ? [`--ozone-platform=${process.env.E2E_ELECTRON_OZONE}`] : []),
      '.',
    ],
    cwd: process.cwd(),
    env: {
      ...electronEnv,
      XDG_CONFIG_HOME: state.xdgConfigHome,
      OMNIAGENTS_HOME: path.join(state.rootDir, 'omniagents'),
      OPENAI_BASE_URL:
        modelServer?.baseUrl ??
        process.env.OPENAI_BASE_URL ??
        process.env.SANDBOX_OPENAI_BASE_URL ??
        'http://127.0.0.1:9/v1',
      OPENAI_API_KEY: process.env.OPENAI_API_KEY ?? process.env.SANDBOX_OPENAI_API_KEY ?? 'test-key',
      ...(process.env.DISPLAY ? { DISPLAY: process.env.DISPLAY } : {}),
    },
    recordVideo: videoOptions(testInfo, launchIndex),
  });
  if (process.env.E2E_FORWARD_ELECTRON_LOGS === '1') {
    electronApp.process().stdout?.pipe(process.stdout);
    electronApp.process().stderr?.pipe(process.stderr);
  }
  const page = await electronApp.firstWindow({ timeout: 120_000 });
  // The first BrowserWindow begins on the lightweight splash document and
  // then navigates to the renderer. Wait for stable product UI before taking
  // a BrowserWindow handle; otherwise the navigation can destroy its JS
  // execution context while proof-mode sizing is in flight.
  await page.getByText('New chat', { exact: true }).first().waitFor({ state: 'visible', timeout: 120_000 });
  const browserWindow = await electronApp.browserWindow(page);
  if (visualProofEnabled) {
    await browserWindow.evaluate((window, size) => window.setSize(size.width, size.height), proofViewport);
    await page.setViewportSize(proofViewport);
  }
  await page.waitForLoadState('domcontentloaded');

  return {
    page,
    rootPid: () => electronApp.process().pid,
    resizeWindow: async (width, height) => {
      await browserWindow.evaluate((window, size) => window.setSize(size.width, size.height), { width, height });
      await page.setViewportSize({ width, height });
    },
    releaseBackground: () => modelServer?.releaseBackground(),
    inspectModelRequests: () => [...(modelServer?.observations ?? [])],
    setMinimized: async (minimized) => {
      await browserWindow.evaluate((window, value) => (value ? window.minimize() : window.restore()), minimized);
      await expect.poll(() => browserWindow.evaluate((window) => window.isMinimized())).toBe(minimized);
    },
    // Use the same renderer screenshot path as browser proofs. Native
    // capturePage can return a stale compositor surface (or a host-WM crop)
    // after DOM assertions pass, making the artifact contradict the test.
    captureScreenshot: () => page.screenshot({ animations: 'disabled' }),
    close: async (options) => {
      const process = electronApp.process();
      if (options?.crash && process.pid && process.exitCode === null) {
        crashProcessTree(process.pid);
      }
      await electronApp.close().catch(() => undefined);
      await attachPageVideo(page, testInfo, `electron-local video ${launchIndex}`);
      await modelServer?.close().catch(() => undefined);
    },
  };
}

export const test = base.extend<E2eFixtures>({
  launchMode: ['server-local', { option: true }],
  seedState: ['blank', { option: true }],
  mode: async ({ launchMode }, fixtureUse) => {
    await fixtureUse(launchMode);
  },
  app: async ({ browser, launchMode, seedState }, fixtureUse, testInfo) => {
    const testId = `${launchMode}-${testInfo.workerIndex}-${testInfo.repeatEachIndex}`;
    const state = createE2eState(testId);
    if (launchMode === 'server-local') {
      seedServerState(state, seedState);
    } else {
      seedElectronState(state, seedState);
    }

    let launchIndex = 0;
    const launch = () => {
      launchIndex += 1;
      const currentLaunch = launchIndex;
      return launchMode === 'server-local'
        ? launchServerLocal(browser, state, testInfo, currentLaunch, seedState)
        : launchElectronLocal(state, testInfo, currentLaunch, seedState);
    };

    let launched = await launch();
    const runtimePids = () => {
      const root = launched.rootPid();
      return root ? runtimePidsUnder(root) : [];
    };
    const app: E2eApp = {
      setProviderFromCli: (model) => {
        execFileSync(process.env.OMNI_CLI_PATH ?? 'omni', ['model', 'default', model, '--confirm-provider-change'], {
          env: { ...process.env, HOME: state.homeDir, XDG_CONFIG_HOME: state.xdgConfigHome },
          stdio: 'pipe',
          timeout: 30_000,
        });
      },
      releaseBackground: () => launched.releaseBackground?.(),
      get page() {
        return launched.page;
      },
      resizeWindow: (width, height) =>
        launched.resizeWindow?.(width, height) ?? launched.page.setViewportSize({ width, height }),
      setMinimized: async (minimized) => {
        if (!launched.setMinimized) {
          throw new Error('Native minimization requires electron-local');
        }
        await launched.setMinimized(minimized);
      },
      get workspaceDir() {
        return state.workspaceDir;
      },
      captureScreenshot: () => launched.captureScreenshot(),
      inspectCodexCredential: () => inspectElectronCodexCredential(state),
      inspectMcpConfig: () => inspectElectronMcpConfig(state),
      inspectModelRequests: () => launched.inspectModelRequests?.() ?? [],
      storedTabs: () => {
        const base = launchMode === 'server-local' ? path.join(state.homeDir, '.config') : state.xdgConfigHome;
        const stored = JSON.parse(readFileSync(path.join(base, 'Omni Code', 'config.json'), 'utf8'));
        return stored.codeTabs ?? [];
      },
      runtimePids,
      inspectRuntimeResources: () => {
        const hosts = runtimePids();
        const totals = {
          hosts: hosts.length,
          rssBytes: 0,
          fileDescriptors: 0,
          childProcesses: 0,
          workerPids: [] as number[],
        };
        for (const pid of hosts) {
          totals.rssBytes +=
            Number(readFileSync(`/proc/${pid}/status`, 'utf8').match(/^VmRSS:\s+(\d+)/m)?.[1] ?? 0) * 1024;
          totals.fileDescriptors += readdirSync(`/proc/${pid}/fd`).length;
          const workerPids = readFileSync(`/proc/${pid}/task/${pid}/children`, 'utf8')
            .trim()
            .split(/\s+/)
            .filter(Boolean)
            .map(Number);
          totals.childProcesses += workerPids.length;
          totals.workerPids.push(...workerPids);
        }
        return totals;
      },
      restart: async (options) => {
        if (options?.backendOnly && launched.restartBackend) {
          await launched.restartBackend();
          return launched.page;
        }
        await launched.close(options);
        launched = await launch();
        return launched.page;
      },
    };

    try {
      await fixtureUse(app);
    } finally {
      await launched.close();
      state.cleanup();
    }
  },
  appPage: async ({ app }, fixtureUse) => {
    await fixtureUse(app.page);
  },
});

export { expect };
