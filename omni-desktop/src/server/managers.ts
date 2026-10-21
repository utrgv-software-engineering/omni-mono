import { readFileSync, writeFileSync } from 'fs';
import { migrateFromJson } from 'omni-projects-db';
import { join } from 'path';

import { emptyMcpConfig, emptyModelsConfig, emptyNetworkConfig } from '@/lib/agent-config';
import { pullRequestEventDetail, pullRequestSystemLine } from '@/lib/pull-request-watch';
import { parseResidentPrincipal } from '@/lib/resident-agent';
import { AutomationManager, registerAutomationHandlers } from '@/main/automation-manager';
import { listRepos as azureListRepos } from '@/main/azure-repos';
import { buildBrowserContext, registerBrowserHandlers } from '@/main/browser-manager';
import { ChatCleanupRunner, cleanupRemovedChat, completeChatRemoval } from '@/main/chat-removal';
import { assertCleanupOwner, runtimeOwner } from '@/main/chat-runtime-journal';
import { registerChatHandlers } from '@/main/chat-service';
import {
  getStatus as codexStatus,
  loginWithDeviceFlow as codexDeviceLogin,
  logout as codexLogout,
} from '@/main/codex-auth';
import { migrateAgentConfigFromFiles } from '@/main/config-files-migration';
import { materializeAgentConfig } from '@/main/config-materializer';
import { createConsoleManager } from '@/main/console-manager';
import { PROJECT_KEYS } from '@/main/db-store-bridge';
import { ExtensionManager, registerExtensionHandlers } from '@/main/extension-manager';
import {
  linkWithDeviceFlow as githubLink,
  listOrgs as githubListOrgs,
  searchRepos as githubSearchRepos,
} from '@/main/github-auth';
import { registerInboxHandlers } from '@/main/inbox-handlers';
import { readLocalModels, writeLocalModels } from '@/main/local-model-config';
import { getMcpBinPath } from '@/main/mcp-config-manager';
import { registerMigrationHandlers } from '@/main/migration-handlers';
import { registerMilestoneHandlers } from '@/main/milestone-handlers';
import { createOmniInstallManager } from '@/main/omni-install-manager';
import { registerPageHandlers } from '@/main/page-handlers';
import { migrateLegacyPagesToConfigDir } from '@/main/pages-relocation-migration';
import { ProcessManager, registerProcessHandlers } from '@/main/process-manager';
import { registerProfileCatalogHandlers } from '@/main/profile-catalog';
import { getBundledProfilesDir } from '@/main/profile-resolver';
import { backfillProjectConfigs } from '@/main/project-config-backfill';
import { closeProjectDb, getDb, openProjectDb } from '@/main/project-db';
import { registerProjectHandlers } from '@/main/project-handlers';
import { ProjectManager } from '@/main/project-manager';
import { PullRequestWatcher } from '@/main/pull-request-watcher';
import { registerResidentHandlers, ResidentAgentManager } from '@/main/resident-agent-manager';
import { RoutineBridge } from '@/main/routine-bridge';
import {
  defaultSandboxInventoryDeps,
  processOwnersFromState,
  registerSandboxInventoryHandlers,
  suspendedSessionOwners,
} from '@/main/sandbox-inventory';
import { registerScheduledTaskHandlers, ScheduledTaskManager } from '@/main/scheduled-task-manager';
import { deleteSnapshot, protectedSnapshotsFromTabs, registerSnapshotHandlers } from '@/main/snapshot-manager';
import { registerSupervisorHandlers } from '@/main/supervisor-handlers';
import { getOmniConfigDir } from '@/main/util';
import { ServerIpcAdapter } from '@/server/ipc-adapter';
import { ServerSecretStore } from '@/server/secret-store';
import type { ServerStore } from '@/server/store';
import type { HandlerContext, WsHandler } from '@/server/ws-handler';
import { applyChatCommand } from '@/shared/chat-commands';
import { tokenLast4 } from '@/shared/git-credentials';
import {
  registerConfigHandlers,
  registerGitCredentialHandlers,
  registerSettingsConfigHandlers,
  registerSkillsHandlers,
  registerUtilHandlers,
  type SettingsConfigStore,
} from '@/shared/ipc-handlers';
import { buildStdioMcpEntry } from '@/shared/mcp-entry';
import type {
  GitCredential,
  GithubOwner,
  GithubRepoQuery,
  GithubStatus,
  IpcRendererEvents,
  StoreData,
} from '@/shared/types';

/* eslint-disable @typescript-eslint/no-explicit-any */

type SendToWindow = <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => void;
type HandleFn = (
  channel: string,
  handler: (ctx: HandlerContext, ...args: unknown[]) => unknown | Promise<unknown>
) => void;

/**
 * Wire up global (shared) IPC handlers — store, util, config, project, process, main-process.
 * These are stateless or shared, safe for all clients to use.
 *
 * ProcessManager is global so that containers/processes survive WebSocket
 * reconnections and React re-renders. Each WS session reattaching to the same server
 * gets the existing running container status instead of spawning duplicates.
 */
export const wireGlobalHandlers = async (arg: { wsHandler: WsHandler; store: ServerStore }) => {
  const { wsHandler, store } = arg;
  // Handlers receive the per-invoke HandlerContext in the Electron `event` slot.
  const ipc = new ServerIpcAdapter(wsHandler.handleCtx.bind(wsHandler));
  const sendToAll: SendToWindow = wsHandler.sendToAll.bind(wsHandler);

  // Both this server and any agent-spawned MCP stdio subprocesses read/write
  // the same projects.db via WAL.
  const { repo: syncRepo, asyncRepo } = openProjectDb();

  try {
    const migrated = migrateFromJson(syncRepo, getDb(), {
      projects: store.get('projects', []) as import('@/shared/types').Project[],
      tickets: store.get('tickets', []) as import('@/shared/types').Ticket[],
      milestones: store.get('milestones', []) as import('@/shared/types').Milestone[],
      pages: store.get('pages', []) as import('@/shared/types').Page[],
      inboxItems: store.get('inboxItems', []) as import('@/shared/types').InboxItem[],
      tasks: store.get('tasks', []) as import('@/shared/types').Task[],
    });
    if (migrated > 0) {
      console.log(`[ProjectDb] Migrated ${migrated} projects from server-store to SQLite`);
    }
  } catch (err) {
    console.error('[ProjectDb] Failed to migrate from server-store:', err);
  }

  try {
    const backfilled = backfillProjectConfigs(syncRepo);
    if (backfilled > 0) {
      console.log(`[ProjectDb] Backfilled config for ${backfilled} projects`);
    }
  } catch (err) {
    console.error('[ProjectDb] Failed to backfill project configs:', err);
  }

  try {
    const summary = migrateLegacyPagesToConfigDir(syncRepo);
    const total = summary.perProjectPagesCopied + summary.rootPagesFromContextMd + summary.mcpPagesCopied;
    if (total > 0) {
      console.log(
        `[ProjectDb] Pages migration copied ${total} files ` +
          `(per-project: ${summary.perProjectPagesCopied}, ` +
          `context.md → root: ${summary.rootPagesFromContextMd}, ` +
          `MCP: ${summary.mcpPagesCopied}, ` +
          `skipped existing: ${summary.skippedAlreadyMigrated})`
      );
    }
    const existing = store.get('pagesMigration');
    if (!existing && summary.legacyPaths.length > 0) {
      store.set('pagesMigration', {
        summary: {
          perProjectPagesCopied: summary.perProjectPagesCopied,
          rootPagesFromContextMd: summary.rootPagesFromContextMd,
          mcpPagesCopied: summary.mcpPagesCopied,
          skippedAlreadyMigrated: summary.skippedAlreadyMigrated,
        },
        legacyPaths: summary.legacyPaths,
        acknowledged: false,
      });
    }
  } catch (err) {
    console.error('[ProjectDb] Failed to migrate legacy pages:', err);
  }

  ProjectManager.migrateToSupervisor(store as any);

  const configDir = getOmniConfigDir();
  const secretStore = new ServerSecretStore();

  const [omniInstall, cleanupOmniInstall] = createOmniInstallManager({
    ipc,
    sendToWindow: sendToAll,
  });

  // Snapshot-aware broadcasts: `store:changed` always carries the full
  // projection (project keys from SQL + settings), never the raw settings.
  const getStoreSnapshot = (): StoreData => projectManager.getStoreSnapshot();
  const sendSnapshot = (): void => wsHandler.sendToAll('store:changed', getStoreSnapshot());
  const snapshotSend: SendToWindow = (channel, ...args) => {
    if (channel === 'store:changed') {
      sendSnapshot();
      return;
    }
    sendToAll(channel, ...args);
  };

  const processManager = new ProcessManager({
    runtimeJournalDir: join(configDir, 'chat-runtime-journal'),
    claimChatRuntime: async (id) => {
      const tabs = store.get('codeTabs') ?? [];
      if (!tabs.some((tab) => tab.id === id)) {
        throw new Error('This chat tab has been closed');
      }
      assertCleanupOwner(tabs.find((tab) => tab.id === id)?.runtimeOwner);
      store.set(
        'codeTabs',
        tabs.map((tab) => (tab.id === id ? { ...tab, runtimeOwner } : tab))
      );
    },
    sendToWindow: sendToAll,
    fetchFn: globalThis.fetch,
    getStoreData: () => ({
      defaultProfileName: store.get('defaultProfileName') ?? 'host',
      projects: projectManager.getStoreSnapshot().projects ?? [],
      gitCredentials: store.get('gitCredentials') ?? [],
    }),
    resolveGitToken: (id) => secretStore.getGitToken(id),
    waitForRuntimeInstall: () => omniInstall.waitForInstallCompletion(),
  });
  const routineBridge = new RoutineBridge(sendToAll);
  const scheduledTaskManager = new ScheduledTaskManager({
    store: store as any,
    bridge: routineBridge,
    sendToWindow: snapshotSend,
  });
  scheduledTaskManager.start();
  const residentAgentManager = new ResidentAgentManager({
    store: store as any,
    repo: asyncRepo,
    processManager,
    getSnapshot: getStoreSnapshot,
    sendToWindow: snapshotSend,
  });
  residentAgentManager.start();
  const projectManager: ProjectManager = new ProjectManager({
    store: store as any,
    sendToWindow: sendToAll,
    processManager,
    repo: asyncRepo,
    changeSeqRepo: syncRepo,
    // Resident durable keys ride every snapshot/broadcast this manager makes.
    snapshotExtras: () => residentAgentManager.getDurableSnapshot(),
    skillsDir: join(configDir, 'skills'),
    // Assignment fan-out: residents get an assignment wakeup.
    onAssign: (assignee: string, ticket: import('@/shared/types').Ticket) => {
      const residentId = parseResidentPrincipal(assignee);
      if (!residentId) {
        return;
      }
      const project = projectManager.getStoreSnapshot().projects.find((p) => p.id === ticket.projectId);
      residentAgentManager.deliverAssignment(residentId, {
        id: ticket.id,
        title: ticket.title,
        ...(project ? { projectLabel: project.label } : {}),
      });
    },
  });
  // Automations: user-defined event rules (PR event / channel message /
  // schedule) that wake a resident with a standing instruction.
  const automationManager = new AutomationManager({
    store: store as any,
    deliver: (agentId, detail) => residentAgentManager.deliverAutomation(agentId, detail),
    subscribeChatEvents: residentAgentManager.subscribeChatEvents,
    sendToWindow: snapshotSend,
  });
  automationManager.start();
  // Watched-PR poller: state flips persist through the links' owners; review
  // verdicts / merges on ticket-linked PRs wake the assigned resident.
  const pullRequestWatcher = new PullRequestWatcher({
    getSnapshot: getStoreSnapshot,
    resolveGitToken: (id) => secretStore.getGitToken(id),
    fetchFn: globalThis.fetch,
    updateTicket: (ticketId, patch) => projectManager.updateTicket(ticketId, patch),
    setGlobalLinks: (links) => store.set('pullRequestLinks', links),
    onEvent: (ev) => {
      // Every PR event lands in #system regardless of whether anyone wakes —
      // the Activity feed is the complete record.
      residentAgentManager.postSystemNotice(pullRequestSystemLine(ev));
      const residentId = ev.assignee ? parseResidentPrincipal(ev.assignee) : null;
      if (residentId) {
        residentAgentManager.deliverPullRequestEvent(residentId, pullRequestEventDetail(ev));
      }
      // User-defined rules see every PR event, ticket-linked or not.
      automationManager.onPullRequestEvent(ev);
    },
  });
  pullRequestWatcher.start();
  const extension = new ExtensionManager({ store: store as any, sendToWindow: sendToAll });
  const browser = buildBrowserContext(store as any, sendToAll);
  const chatCleanup = new ChatCleanupRunner({
    read: async () => {
      await processManager.recoverAbandonedRuntimes();
      return store.get('chatCleanupJobs') ?? [];
    },
    cleanup: async (tab) => {
      assertCleanupOwner(tab.runtimeOwner);
      await cleanupRemovedChat(tab, {
        stop: (id) => processManager.retire(id),
        deleteSnapshot,
        isSnapshotProtected: (ref) =>
          protectedSnapshotsFromTabs(store.get('codeTabs') ?? []).some((claim) => claim.snapshotRef === ref),
      });
    },
    acknowledge: async (id) => {
      store.set(
        'chatCleanupJobs',
        (store.get('chatCleanupJobs') ?? []).filter((job) => job.id !== id)
      );
    },
  });
  chatCleanup.start();

  /**
   * Write the agent config (mcp/network) to the shared config dir —
   * the same dir omni serve's get_config_dir() resolves — plus a real `.env`
   * (omni serve reads it into the sandbox container's manifest.environment).
   */
  const materializeConfig = (): void => {
    try {
      materializeAgentConfig({
        configDir,
        mcp: store.get('mcpConfig') ?? emptyMcpConfig(),
        network: store.get('networkConfig') ?? emptyNetworkConfig(),
        managedMcpEntry: buildStdioMcpEntry(getMcpBinPath()),
      });
      writeFileSync(join(configDir, '.env'), store.get('envVars') ?? '', 'utf-8');
    } catch (err) {
      console.error('[config-materializer] failed:', err);
    }
  };

  registerProjectHandlers(ipc, () => projectManager);
  registerSupervisorHandlers(ipc, () => projectManager.supervisors);
  registerMilestoneHandlers(ipc, () => projectManager.milestones);
  registerPageHandlers(
    ipc,
    () => projectManager.pages,
    (_e, projectId) => projectManager.getProjectDir(projectId)
  );
  registerInboxHandlers(ipc, () => projectManager.inbox);
  registerProcessHandlers(ipc, () => processManager);
  registerScheduledTaskHandlers(ipc, () => scheduledTaskManager);
  registerAutomationHandlers(ipc, () => automationManager);
  registerResidentHandlers(ipc, () => residentAgentManager);
  registerChatHandlers(ipc, () => residentAgentManager);
  registerExtensionHandlers(ipc, () => extension);
  registerBrowserHandlers(ipc, () => browser);
  projectManager.bridge.registerIpc(ipc, () => projectManager.bridge);
  routineBridge.registerIpc(ipc, () => routineBridge);

  // Import any pre-v23 on-disk config files into the store once.
  migrateAgentConfigFromFiles(store as unknown as SettingsConfigStore, configDir);
  await projectManager.whenReady;
  // Write the agent's on-disk config so the next omni-serve spawn reads
  // fresh model/mcp/network files.
  materializeConfig();

  // Downloads are an Electron-only feature (Chromium session.will-download).
  // In server mode we register stubs so the renderer's tray UI quietly shows
  // "no downloads" rather than erroring out on first invoke.
  ipc.handle('browser:downloads-list', () => []);
  ipc.handle('browser:downloads-clear', () => 0);
  ipc.handle('browser:downloads-remove', () => {});
  ipc.handle('browser:downloads-open-file', () => '');
  ipc.handle('browser:downloads-show-in-folder', () => {});
  ipc.handle('browser:downloads-watch-partition', () => {});
  ipc.handle('browser:permissions-list', () => []);
  ipc.handle('browser:permissions-decide', () => {});
  ipc.handle('browser:permissions-watch-partition', () => {});

  // Global status getters
  ipc.handle('omni-install-process:get-status', () => omniInstall.getStatus());

  // Store handlers — snapshot-aware: project keys come from the SQL projection;
  // every other (settings) key reads/writes the settings store. SQLite-backed
  // project changes are broadcast by ProjectManager/DbChangeWatcher; settings
  // writes broadcast explicitly below.
  ipc.handle('store:get-key', (_ctx, key) => {
    const k = key as keyof StoreData;
    if (PROJECT_KEYS.has(k)) {
      return getStoreSnapshot()[k];
    }
    return store.get(k);
  });
  ipc.handle('store:chat-command', async (_ctx, command) => {
    const { patch, result } = applyChatCommand(getStoreSnapshot(), command);
    store.set(patch);
    sendSnapshot();
    await completeChatRemoval(command, result, (tab) => chatCleanup.runOne(tab));
    return result;
  });
  ipc.handle('store:set-key', (_ctx, key, value) => {
    if (key === 'modelsConfig') {
      throw new Error('Use AI Settings to change model configuration.');
    }
    if (key === 'codeTabs' || key === 'chatConversations' || key === 'activeCodeTabId' || key === 'chatCleanupJobs') {
      throw new Error('Use store:chat-command for chat state mutations');
    }
    const k = key as keyof StoreData;
    if (PROJECT_KEYS.has(k)) {
      throw new Error(
        `store:set-key for project key "${String(k)}" is not allowed when SQLite is active. Use ProjectManager APIs.`
      );
    }
    store.set(k, value as never);
    sendSnapshot();
  });
  ipc.handle('store:get', () => getStoreSnapshot());
  ipc.handle('store:set', (_ctx, data) => {
    if (Object.hasOwn(data, 'modelsConfig')) {
      throw new Error('Use AI Settings to change model configuration.');
    }
    if ('codeTabs' in data || 'chatConversations' in data || 'activeCodeTabId' in data || 'chatCleanupJobs' in data) {
      throw new Error('Use store:chat-command for chat state mutations');
    }
    const conflicts = [...PROJECT_KEYS].filter((k) => k in data);
    if (conflicts.length > 0) {
      throw new Error(`store:set with project keys [${conflicts.join(', ')}] is not allowed when SQLite is active.`);
    }
    store.store = data;
    sendSnapshot();
  });
  ipc.handle('store:reset', () => {
    store.clear();
    sendSnapshot();
  });

  // Main process status (simplified for server)
  const mainStatus = { type: 'idle' as const, timestamp: Date.now() };
  ipc.handle('main-process:get-status', () => mainStatus);
  ipc.handle('main-process:exit', () => {});

  // Read version from package.json at startup
  let launcherVersion = '0.0.0';
  try {
    const pkg = JSON.parse(readFileSync(join(__dirname, '../../package.json'), 'utf-8'));
    launcherVersion = (pkg as { version?: string }).version ?? '0.0.0';
  } catch {
    // fallback
  }

  // Shared IPC handlers (config:*, util:*, skills:*) — identical across Electron and server.
  registerConfigHandlers(ipc, configDir);
  registerUtilHandlers(ipc, { fetchFn: globalThis.fetch, launcherVersion });
  registerSkillsHandlers(
    ipc,
    () => configDir,
    () => store as never
  );
  registerSettingsConfigHandlers(
    ipc,
    () => store as unknown as SettingsConfigStore,
    () => {
      materializeConfig();
      sendSnapshot();
    },
    {
      readModels: () => readLocalModels(configDir, store.get('modelsConfig') ?? emptyModelsConfig()),
      saveModels: (_event, config) => writeLocalModels(configDir, config),
    }
  );
  registerGitCredentialHandlers(
    ipc,
    () => store as unknown as SettingsConfigStore,
    () => secretStore,
    () => sendSnapshot()
  );
  // Cascade-delete chat/code-tab snapshots when the renderer closes a tab,
  // plus the Sandboxes-tab snapshot browser.
  registerSnapshotHandlers(ipc, {
    getProtectedSnapshots: () => protectedSnapshotsFromTabs(store.get('codeTabs') ?? []),
  });

  // Sandboxes tab (docs/sandboxes-tab-plan.md Phase 2). Profile discovery
  // uses the same dirs as AgentHost profile registration: the launcher's
  // bundled assets/profiles (resolved relative to out/server → repo checkout,
  // exactly like agent-process's resolveProfile) plus the shared
  // <config>/sandbox dir of user-editable profiles. The
  // availableSandboxProfiles filter is resolved from the same snapshot the
  // picker reads, so both surfaces always agree.
  registerProfileCatalogHandlers(ipc, {
    bundledDir: getBundledProfilesDir(),
    userDir: join(configDir, 'sandbox'),
    getAvailableProfileNames: () => getStoreSnapshot().availableSandboxProfiles,
  });
  registerSandboxInventoryHandlers(ipc, {
    ...defaultSandboxInventoryDeps(),
    getProcessOwners: () => [
      ...processOwnersFromState(
        processManager.getContainerOwners(),
        store.get('codeTabs') ?? [],
        residentAgentManager.getDurableSnapshot().residentAgents
      ),
      // Durable container sessions with no live serve process.
      ...suspendedSessionOwners(store.get('codeTabs') ?? []),
    ],
  });

  // GitHub / Azure DevOps discovery + GitHub status/unlink. All resolve their
  // token from the SecretStore (matching the Git-credential and GitHub OAuth
  // contract used by the Electron handlers).
  // Stable id used by Electron when storing the OAuth-linked github.com token.
  const GITHUB_CRED_ID = 'github-oauth';
  const requireGithubToken = async (): Promise<string> => {
    const token = await secretStore.getGitToken(GITHUB_CRED_ID);
    if (!token) {
      throw new Error('No GitHub account linked');
    }
    return token;
  };

  ipc.handle('github:status', (): GithubStatus => {
    const account = store.get('githubAccount');
    return account ? { connected: true, account } : { connected: false };
  });

  // Device-flow sign-in for server/browser mode. The Electron handler in
  // main/index.ts does the same thing; the only difference is openUrl can't
  // auto-open a tab from the server, so we no-op it and rely on the renderer
  // rendering verification_uri as a clickable link.
  ipc.handle('github:link', async (): Promise<GithubStatus> => {
    const { token, account } = await githubLink({
      fetchFn: globalThis.fetch,
      openUrl: () => {
        /* server mode: the renderer shows the link; we never auto-open */
      },
      onCode: (code) => sendToAll('github:device-code', code),
    });
    await secretStore.setGitToken(GITHUB_CRED_ID, token);
    const creds = (store.get('gitCredentials') ?? []).filter(
      (cred: GitCredential) => cred.id !== GITHUB_CRED_ID && cred.host !== account.host
    );
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
    sendSnapshot();
    return { connected: true, account };
  });

  ipc.handle('github:unlink', async () => {
    await secretStore.deleteGitToken(GITHUB_CRED_ID);
    store.set(
      'gitCredentials',
      (store.get('gitCredentials') ?? []).filter((cred: GitCredential) => cred.id !== GITHUB_CRED_ID)
    );
    store.delete('githubAccount');
    sendSnapshot();
  });

  ipc.handle('github:list-owners', async (): Promise<GithubOwner[]> => {
    const token = await requireGithubToken();
    const account = store.get('githubAccount');
    // The linked user is always the first owner; their orgs follow.
    const self: GithubOwner[] = account
      ? [{ login: account.login, kind: 'user', ...(account.avatarUrl ? { avatarUrl: account.avatarUrl } : {}) }]
      : [];
    return [...self, ...(await githubListOrgs(globalThis.fetch, token))];
  });

  ipc.handle('github:search-repos', async (_e: unknown, query: GithubRepoQuery) => {
    return githubSearchRepos(globalThis.fetch, await requireGithubToken(), query);
  });

  // Codex device-flow sign-in: tokens go to `<omni-config>/codex.json`, the
  // same file the runtime reads.
  ipc.handle('codex:status', () => codexStatus());
  ipc.handle('codex:logout', () => codexLogout());
  ipc.handle('codex:link', () => codexDeviceLogin({ onCode: (code) => sendToAll('codex:device-code', code) }));

  ipc.handle('azure:list-repos', async (_e: unknown, input: { org: string; query: string }) => {
    const cred = (store.get('gitCredentials') ?? []).find((c: GitCredential) => c.host === 'dev.azure.com');
    const token = cred ? await secretStore.getGitToken(cred.id) : undefined;
    if (!token) {
      throw new Error('No Azure DevOps token — add a dev.azure.com credential first');
    }
    return azureListRepos(globalThis.fetch, token, input.org, input.query);
  });

  registerMigrationHandlers(ipc, () => ({
    get: () => store.get('pagesMigration') ?? null,
    set: (value) => {
      if (value === null) {
        store.delete('pagesMigration');
      } else {
        store.set('pagesMigration', value);
      }
      sendSnapshot();
    },
  }));

  // Desktop-only handlers — stubbed for browser mode
  ipc.handle('util:select-directory', () => null);
  ipc.handle('util:select-file', () => null);
  ipc.handle('util:open-directory', () => '');
  ipc.handle('util:open-external', () => {});

  const cleanupGlobalManagers = async () => {
    const results = await Promise.allSettled([
      chatCleanup.dispose(),
      Promise.resolve(scheduledTaskManager.stop()),
      Promise.resolve(automationManager.stop()),
      Promise.resolve(pullRequestWatcher.stop()),
      residentAgentManager.cleanup(),
      Promise.resolve(routineBridge.disposeAll()),
      projectManager.exit(),
      processManager.cleanup(),
      extension.cleanup(),
      cleanupOmniInstall(),
    ]);
    closeProjectDb();
    const errors = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected').map((r) => r.reason);
    if (errors.length > 0) {
      console.error('Error cleaning up global managers:', errors);
    }
  };

  return { cleanupGlobalManagers, processManager, residentAgentManager };
};

/**
 * Wire up per-client managers — only truly session-scoped resources (PTY console).
 *
 * ProcessManager and OmniInstallManager are ALL global (created in
 * wireGlobalHandlers) so that containers/processes survive WebSocket reconnections
 * and React re-renders. Per-session handlers would shadow the global ones and get
 * destroyed on WS disconnect, killing running containers.
 *
 * Returns a cleanup function for when the client disconnects.
 */
export const wireClientManagers = (arg: {
  handle: HandleFn;
  sendToWindow: SendToWindow;
  store: ServerStore;
  processManager: ProcessManager;
}): (() => Promise<void>) => {
  const { handle, sendToWindow, processManager } = arg;
  const ipc = new ServerIpcAdapter(handle);

  // Console proxies terminal:* IPC through omni serve's WS so the shell
  // runs inside the sandbox. Per-session so output routes to the right
  // WebSocket client; the underlying PTYs live in `omni serve` and survive
  // a WS reconnect (the renderer can reissue terminal:create on a fresh
  // process).
  const [, cleanupConsole] = createConsoleManager({
    ipc,
    sendToWindow,
    processManager,
  });

  // Cleanup function — only per-session resources (console PTY)
  const cleanup = async () => {
    const results = await Promise.allSettled([cleanupConsole()]);
    const errors = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected').map((r) => r.reason);
    if (errors.length > 0) {
      console.error('Error cleaning up client session processes:', errors);
    }
  };

  return cleanup;
};
