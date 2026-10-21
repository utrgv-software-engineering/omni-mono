import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';

import { ipcMain } from 'electron';

import { AgentHostManager } from '@/main/agent-host-manager';
import {
  type AgentHostConsumerRuntime,
  AgentProcess,
  type AgentProcessSource,
  type AgentProcessStartArg,
  type FetchFn,
} from '@/main/agent-process';
import { ChatRuntimeJournal } from '@/main/chat-runtime-journal';
import { hasDurableHostCodexMutation, isDurableCodexAccountRequest } from '@/main/local-codex-account-owner';
import { hasDurableHostMcpMutation, isMcpManagementMethod } from '@/main/local-mcp-config-owner';
import { gitTokenEnvName, resolveCredentialForUrl } from '@/shared/git-credentials';
import type { IIpcListener } from '@/shared/ipc-listener';
import { assertManagementAdminRequest, type ManagementAdminResult } from '@/shared/management-admin';
import type {
  AgentProcessStartOptions,
  AgentProcessStatus,
  AgentProcessStopOptions,
  AgentProcessStopResult,
  AgentRuntimeConnection,
  GitCredential,
  IpcRendererEvents,
  Project,
  WithTimestamp,
} from '@/shared/types';

export type ProcessManagerStoreData = {
  projects: Project[];
  /** Stored git credential metadata (host-scoped). Tokens are read lazily via
   *  `resolveGitToken`; this list only drives host matching. */
  gitCredentials?: GitCredential[];
};

/**
 * Unified process manager for all agent processes (chat + code tabs).
 *
 * Every agent process is keyed by a string ID:
 *   - `"chat"` for the singleton chat process
 *   - A CodeTabId for code-tab processes
 *
 * Every consumer shares a compatible targetless ``omni serve`` AgentHost and
 * materializes its own environment on omni-code's default profile.
 */
export class ProcessManager {
  /** Consumer id -> compatible long-lived AgentHost connection. */
  private processes = new Map<string, AgentProcess>();
  private agentHosts = new AgentHostManager<AgentProcess>();
  private lastStartArgs = new Map<string, AgentProcessStartOptions>();
  private consumerRuntimes = new Map<string, AgentHostConsumerRuntime>();
  private consumerWorkspaceIds = new Map<string, string>();
  private consumerWorkspaceIdentities = new Map<string, string>();
  private pendingStarts = new Map<string, { identity: string; promise: Promise<void> }>();
  private pendingStops = new Map<string, Promise<AgentProcessStopResult>>();
  // Detach from host selection immediately, but retain the exact cleanup target
  // until shutdown succeeds. A failed stop must remain retryable, and new tabs
  // must never attach to a host whose final consumer is shutting it down.
  private detachedStops = new Map<
    string,
    { proc: AgentProcess; runtime?: AgentHostConsumerRuntime; lastConsumer: boolean }
  >();
  private retiredConsumers = new Set<string>();
  private runtimeJournal?: ChatRuntimeJournal;
  private claimChatRuntime?: (id: string) => void | Promise<void>;
  private runtimeRecovery: Promise<void> = Promise.resolve();
  recoverAbandonedRuntimes = (): Promise<void> => {
    this.runtimeRecovery = this.runtimeRecovery
      .catch(() => undefined)
      .then(() => this.runtimeJournal?.recoverAbandoned());
    return this.runtimeRecovery;
  };
  private pendingManagementConnection: Promise<AgentRuntimeConnection> | null = null;
  /** Monotonic consumer intent; async results may publish only if their epoch still wins. */
  private consumerEpochs = new Map<string, number>();
  private sendToWindow: <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => void;
  private fetchFn: FetchFn;
  private getStoreData: () => ProcessManagerStoreData;
  /** Resolves the env merged into each `omni serve` spawn. Receives the
   *  process id so per-process identity can ride the env. */
  private getExtraEnv?: (processId?: string) => Record<string, string> | Promise<Record<string, string>>;
  /** Read a git token by credential id (from the SecretStore). Absent → no
   *  private-remote auth (tokens never reach this class except through here). */
  private resolveGitToken?: (credentialId: string) => Promise<string | undefined>;
  /** Blocks local AgentHost operations while the runtime venv is being
   * installed or repaired. In particular, the omni-code console script may
   * exist before the development omniagents overlay is fully installed. */
  private waitForRuntimeInstall?: () => Promise<void>;
  /** Trusted topology assertion. True only for the single-user local Electron
   * manager; server managers must remain false even if child env is spoofed. */
  private durableLocalCodexAccountMutations: boolean;
  /** Independent trusted topology gate for canonical host MCP stores. */
  private durableLocalMcpMutations: boolean;
  /** One-time parity/ownership transfer, injected only by Electron main. */
  private prepareLocalMcpOwnership?: (status: Record<string, unknown>) => void | Promise<void>;
  /** Readiness hook for local ownership transfer; runs after the control
   * channel is usable and never gates the legacy settings read. */
  private onManagementReady?: (proc: AgentProcess) => void | Promise<void>;

  constructor(arg: {
    sendToWindow: ProcessManager['sendToWindow'];
    fetchFn?: FetchFn;
    getStoreData?: () => ProcessManagerStoreData;
    /** Extra env for spawned `omni serve`. Receives the process id. */
    getExtraEnv?: (processId?: string) => Record<string, string> | Promise<Record<string, string>>;
    /** Read a git token by credential id, for private-remote auth at clone time. */
    resolveGitToken?: (credentialId: string) => Promise<string | undefined>;
    /** Wait for any in-flight local runtime install/repair to settle. */
    waitForRuntimeInstall?: () => Promise<void>;
    /** Permit canonical Codex mutations only in local single-user Electron. */
    durableLocalCodexAccountMutations?: boolean;
    /** Permit canonical MCP CRUD/auth only in local single-user Electron. */
    durableLocalMcpMutations?: boolean;
    /** Verify legacy/canonical parity before the first canonical mutation. */
    prepareLocalMcpOwnership?: (status: Record<string, unknown>) => void | Promise<void>;
    /** Run local ownership transfer after the management host is ready. */
    onManagementReady?: (proc: AgentProcess) => void | Promise<void>;
    runtimeJournalDir?: string;
    claimChatRuntime?: (id: string) => void | Promise<void>;
  }) {
    this.sendToWindow = arg.sendToWindow;
    if (arg.runtimeJournalDir) {
      this.runtimeJournal = new ChatRuntimeJournal(arg.runtimeJournalDir);
      this.runtimeRecovery = this.runtimeJournal.recoverAbandoned();
    }
    this.claimChatRuntime = arg.claimChatRuntime;
    this.fetchFn = arg.fetchFn ?? globalThis.fetch;
    this.getStoreData =
      arg.getStoreData ??
      (() => ({
        projects: [],
      }));
    this.getExtraEnv = arg.getExtraEnv;
    this.resolveGitToken = arg.resolveGitToken;
    this.waitForRuntimeInstall = arg.waitForRuntimeInstall;
    this.durableLocalCodexAccountMutations = arg.durableLocalCodexAccountMutations === true;
    this.durableLocalMcpMutations = arg.durableLocalMcpMutations === true;
    this.prepareLocalMcpOwnership = arg.prepareLocalMcpOwnership;
    this.onManagementReady = arg.onManagementReady;
  }

  /** Try to resolve the git remote URL and current branch from a workspace directory. */
  private resolveGitRemote(workspaceDir: string): { url: string; branch?: string } | undefined {
    try {
      const url = execFileSync('git', ['remote', 'get-url', 'origin'], {
        cwd: workspaceDir,
        timeout: 3000,
        encoding: 'utf-8',
      }).trim();
      if (!url) {
        return undefined;
      }

      let branch: string | undefined;
      try {
        branch = execFileSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], {
          cwd: workspaceDir,
          timeout: 3000,
          encoding: 'utf-8',
        }).trim();
        if (branch === 'HEAD') {
          branch = undefined;
        } // detached HEAD
      } catch {
        /* ignore */
      }

      return branch ? { url, branch } : { url };
    } catch {
      return undefined;
    }
  }

  private getOrCreate(processId: string, compatibilityKey: string): { proc: AgentProcess; created: boolean } {
    let proc!: AgentProcess;
    const attachment = this.agentHosts.attach(processId, compatibilityKey, () => {
      proc = new AgentProcess({
        recordConsumer: this.runtimeJournal
          ? async (claim) => {
              this.runtimeJournal!.record(claim);
              await this.claimChatRuntime?.(claim.consumerId);
            }
          : undefined,
        ipcRawOutput: (data) => {
          for (const consumerId of this.agentHosts.consumersForHost(proc)) {
            this.sendToWindow('agent-process:raw-output', consumerId, data);
          }
        },
        onStatusChange: (status) => {
          for (const consumerId of this.agentHosts.consumersForHost(proc)) {
            const tailored = this.statusForConsumer(consumerId, status);
            if (tailored) {
              this.sendToWindow('agent-process:status', consumerId, tailored);
            }
          }
        },
        fetchFn: this.fetchFn,
        getExtraEnv: async () => (await this.getExtraEnv?.(processId)) ?? {},
      });
      return proc;
    });
    this.processes.set(processId, attachment.host);
    if (attachment.released) {
      void attachment.released.exit();
    }
    return { proc: attachment.host, created: attachment.created };
  }

  private agentHostCompatibilityKey(processId: string, arg: AgentProcessStartArg): string {
    const credentialScope =
      (arg.credentials?.length ?? 0) > 0 || Object.keys(arg.gitTokenEnv ?? {}).length > 0 ? processId : 'shared';
    return JSON.stringify({ credentialScope });
  }

  private statusForConsumer(
    consumerId: string,
    status: WithTimestamp<AgentProcessStatus>
  ): WithTimestamp<AgentProcessStatus> | null {
    if (status.type !== 'running' && status.type !== 'connecting') {
      return status;
    }
    const runtime = this.consumerRuntimes.get(consumerId);
    if (!runtime) {
      return null;
    }
    return {
      ...status,
      data: {
        ...status.data,
        workspaceId: runtime.workspaceId,
        environmentId: runtime.environmentId,
        environmentGeneration: runtime.environmentGeneration,
        workspaceRoot: runtime.workspaceRoot,
        ...(runtime.defaultCwd ? { defaultCwd: runtime.defaultCwd } : {}),
        ...(runtime.mounts ? { mounts: runtime.mounts } : {}),
        ...(runtime.environmentCapabilities ? { environmentCapabilities: runtime.environmentCapabilities } : {}),
        services: runtime.services,
      },
    };
  }

  private async buildStartArg(opts: AgentProcessStartOptions): Promise<AgentProcessStartArg> {
    // The project path additionally synthesizes a source from the bare
    // workspaceDir (chat scratch dirs, managed project dirs).
    const sources = this.resolveProjectSources(opts.workspaceDir, opts.projectId, opts.sourceOverrideDir);
    const startArg: AgentProcessStartArg = {
      sources,
      workspaceDir: opts.workspaceDir,
    };
    if (opts.projectId) {
      startArg.projectId = opts.projectId;
    }
    if (opts.sessionId) {
      startArg.sessionId = opts.sessionId;
    }
    if (opts.snapshotRef) {
      startArg.snapshotRef = opts.snapshotRef;
    }
    // Resolve a stored credential for each private git-remote source, attach the
    // auth hint to the descriptor, and collect the token env (value off-disk).
    const gitAuth = await this.resolveGitAuth(sources);
    if (gitAuth) {
      startArg.gitTokenEnv = gitAuth.env;
      if (gitAuth.credentials.length > 0) {
        startArg.credentials = gitAuth.credentials;
      }
    }
    return startArg;
  }

  /**
   * Resolve git credentials for the sandbox, scoped to the hosts the project's
   * sources actually reference. For each source we determine its remote URL —
   * a ``git-remote`` carries it directly; a ``local-git`` checkout's is read from
   * its own ``origin`` — match a stored credential by host, and read the token.
   *
   * Returns the `{ tokenEnvName: token }` env map (tokens never touch disk/argv;
   * only the env-var *name* travels) plus a deduped ``credentials`` bundle
   * (`{ url, username, tokenEnv }`) the launcher forwards as ``--credential`` so
   * ``omni serve`` configures git + the host's CLI (`gh` / `az devops`) at boot
   * for every linked host — covering local-git checkouts, not just clones.
   *
   * ``git-remote`` sources additionally get the in-place ``auth`` hint they need
   * for clone-time auth (the boot pass runs after ``create()``, too late to help
   * the clone itself).
   */
  private async resolveGitAuth(sources: AgentProcessSource[]): Promise<
    | {
        env: Record<string, string>;
        credentials: Array<{ url: string; username: string; tokenEnv: string }>;
      }
    | undefined
  > {
    const resolve = this.resolveGitToken;
    if (!resolve) {
      return undefined;
    }
    const { gitCredentials } = this.getStoreData();
    if (!gitCredentials?.length) {
      return undefined;
    }
    const env: Record<string, string> = {};
    const credentials: Array<{ url: string; username: string; tokenEnv: string }> = [];
    const seen = new Set<string>();
    for (const source of sources) {
      let url: string | undefined;
      if (source.kind === 'git-remote') {
        url = source.repoUrl;
      } else if (source.kind === 'local-git') {
        url = this.resolveGitRemote(source.workspaceDir)?.url;
      }
      if (!url) {
        continue;
      }
      const cred = resolveCredentialForUrl(gitCredentials, url);
      if (!cred) {
        continue;
      }
      const token = await resolve(cred.id);
      if (!token) {
        // The credential metadata matched the source's host, but no token could
        // be read from this runtime's secret store. Surface it — otherwise the
        // private clone proceeds unauthenticated and fails with an opaque error.
        console.warn(
          `[process-manager] credential for ${cred.host} (${cred.id}) is linked but its token ` +
            `could not be read here; re-link it in Settings → Git`
        );
        continue;
      }
      const tokenEnv = gitTokenEnvName(cred.id);
      env[tokenEnv] = token;
      if (source.kind === 'git-remote') {
        source.auth = { tokenEnv, username: cred.username };
      }
      const key = `${tokenEnv}|${url}`;
      if (!seen.has(key)) {
        seen.add(key);
        credentials.push({ url, username: cred.username, tokenEnv });
      }
    }
    return Object.keys(env).length > 0 ? { env, credentials } : undefined;
  }

  /**
   * Translate the project's stored ``ProjectSource`` (or, when missing,
   * the bare workspaceDir) into the ``AgentProcessSource`` ``omni serve``
   * needs. Distinguishes ``local`` from ``local-git`` by probing for a
   * ``.git`` entry, since the launcher's stored ``gitDetected`` flag
   * isn't guaranteed to be fresh for every code path that lands here.
   */
  private resolveProjectSources(
    workspaceDir: string,
    projectId: string | undefined,
    sourceOverrideDir?: string
  ): AgentProcessSource[] {
    // Translate each Project.source (which carries id + mountName) into
    // the AgentProcessSource shape ``omni serve`` understands. Per-source
    // git-detection happens here so the wire format already commits to
    // ``local-git`` vs ``local``.
    if (projectId) {
      const { projects } = this.getStoreData();
      const project = projects.find((p) => p.id === projectId);
      // A project with no attached sources (context-only / managed-dir
      // project) falls through to the synthesized-source path below so its
      // managed directory still seeds the workspace and mirrors back.
      if (project && project.sources.length > 0) {
        let overrideApplied = false;
        return project.sources.map((source) => {
          if (!overrideApplied && source.kind === 'local' && sourceOverrideDir) {
            overrideApplied = true;
            return this.mapDeclaredSource(source, sourceOverrideDir);
          }
          return this.mapDeclaredSource(source);
        });
      }
    }
    // No attached sources (chat scratch dir / managed project dir / Personal
    // root): synthesize one source from the workspaceDir we were given.
    // A solo folder mounts AT the workspace root (mountName '.'), so every
    // profile agrees on the layout: root == the folder, cwd == root, and no
    // surface ever shows a wrapper directory. Before this, containers
    // mounted it at `/workspace/<basename>` — for chat scratch dirs that
    // basename is the session id, which leaked into every path display.
    if (!workspaceDir) {
      return [];
    }
    this.ensureWorkspaceDir(workspaceDir);
    const kind = this.directoryHasGit(workspaceDir) ? 'local-git' : 'local';
    return [
      {
        mountName: '.',
        kind,
        workspaceDir,
        writable: true,
        ...(kind === 'local-git' ? this.resolveGitMetadata(workspaceDir) : {}),
      },
    ];
  }

  /** Translate one declared ``ProjectSource`` into the wire shape. */
  private mapDeclaredSource(source: Project['sources'][number], localOverride?: string): AgentProcessSource {
    if (source.kind === 'git-remote') {
      const result: AgentProcessSource = {
        id: source.id,
        mountName: source.mountName,
        kind: 'git-remote',
        repoUrl: source.repoUrl,
        writable: !source.readOnly,
      };
      if (source.defaultBranch) {
        result.ref = source.defaultBranch;
      }
      return result;
    }
    const workspaceDir = localOverride ?? source.workspaceDir;
    this.ensureWorkspaceDir(workspaceDir);
    // User-attached folders are never launcherOwned — applying
    // container changes to them stays an explicit user action.
    const kind = this.directoryHasGit(workspaceDir) ? 'local-git' : 'local';
    const result: AgentProcessSource = {
      id: source.id,
      mountName: source.mountName,
      kind,
      workspaceDir,
      writable: !source.readOnly,
    };
    if (result.kind === 'local-git') {
      Object.assign(result, this.resolveGitMetadata(workspaceDir));
    }
    return result;
  }

  private resolveGitMetadata(workspaceDir: string): { gitDir?: string; gitCommonDir?: string } {
    try {
      const output = execFileSync(
        'git',
        ['rev-parse', '--path-format=absolute', '--absolute-git-dir', '--git-common-dir'],
        { cwd: workspaceDir, timeout: 3000, encoding: 'utf-8' }
      );
      // git prints forward-slash paths even on Windows; normalize to the
      // platform form every other source path uses.
      const [gitDir, gitCommonDir] = output
        .split(/\r?\n/)
        .map((value) => value.trim())
        .filter(Boolean)
        .map((value) => path.resolve(value));
      return {
        ...(gitDir ? { gitDir } : {}),
        ...(gitCommonDir ? { gitCommonDir } : {}),
      };
    } catch {
      return {};
    }
  }

  private directoryHasGit(workspaceDir: string): boolean {
    if (!workspaceDir) {
      return false;
    }
    try {
      return existsSync(path.join(workspaceDir, '.git'));
    } catch {
      return false;
    }
  }

  /**
   * A `local` source's directory must exist or the agent's pre-flight check
   * rejects it ("Workspace directory not found"). On a fresh host — notably
   * the cloud container, where a project's default `~/Omni/Workspace` doesn't
   * exist yet — create it. An empty workspace is a valid starting point.
   *
   * Failures are logged at WARN: the previous silent-catch made the
   * downstream "Workspace directory not found" error impossible to debug
   * because the actual mkdir failure (permission denied / EROFS / ENOENT on
   * the parent / etc.) never surfaced anywhere. The caller still treats
   * mkdir-failed as a normal "missing dir" so it can fall through to the
   * agent's own existence check; this log just lets us SEE why.
   */
  private ensureWorkspaceDir(workspaceDir: string): void {
    if (!workspaceDir) {
      return;
    }
    try {
      mkdirSync(workspaceDir, { recursive: true });
    } catch (err) {
      console.warn(`[process-manager] ensureWorkspaceDir failed for "${workspaceDir}": ${(err as Error).message}`);
    }
  }

  /**
   * The fields that decide whether two `start` calls mean "the same sandbox".
   */
  private static launchIdentity(opts: AgentProcessStartOptions): string {
    return JSON.stringify([
      opts.workspaceDir,
      opts.sourceOverrideDir ?? null,
      opts.projectId ?? null,
      opts.sessionId ?? null,
      opts.snapshotRef ?? null,
    ]);
  }

  /**
   * Definition fields owned by a framework Workspace registration. A changed
   * snapshot reference, source set, or materialization directory is a new
   * Workspace; reusing its id would ask AgentHost to mutate an existing
   * catalog entry and is correctly rejected by the framework.
   */
  private static workspaceIdentity(arg: AgentProcessStartArg): string {
    return JSON.stringify({
      workspaceDir: arg.workspaceDir ?? null,
      snapshotRef: arg.snapshotRef ?? null,
      sources: arg.sources,
    });
  }

  private advanceConsumerEpoch(processId: string): number {
    const epoch = (this.consumerEpochs.get(processId) ?? 0) + 1;
    this.consumerEpochs.set(processId, epoch);
    return epoch;
  }

  private isCurrentConsumerEpoch(processId: string, epoch: number): boolean {
    return this.consumerEpochs.get(processId) === epoch;
  }

  start = (processId: string, opts: AgentProcessStartOptions): Promise<void> => {
    if (this.retiredConsumers.has(processId)) {
      return Promise.reject(new Error('This chat tab has been closed'));
    }
    if (this.pendingStops.has(processId) || this.detachedStops.has(processId)) {
      return Promise.reject(new Error('This chat runtime is still stopping; retry stopping it before starting'));
    }
    const identity = ProcessManager.launchIdentity(opts);
    const pending = this.pendingStarts.get(processId);
    if (pending) {
      if (pending.identity === identity) {
        return pending.promise;
      }
      // A tab can change its desired binding while its first launch is still
      // provisioning (picker changes, HMR, renderer reconnection). Preserve a
      // single mutator for that consumer and apply the newer intent only after
      // the current transaction settles.
      const epoch = this.advanceConsumerEpoch(processId);
      const operation = pending.promise
        .catch(() => undefined)
        .then(() =>
          this.isCurrentConsumerEpoch(processId, epoch) ? this.startConsumer(processId, opts, epoch) : undefined
        );
      this.pendingStarts.set(processId, { identity, promise: operation });
      const clear = (): void => {
        if (this.pendingStarts.get(processId)?.promise === operation) {
          this.pendingStarts.delete(processId);
        }
      };
      void operation.then(clear, clear);
      return operation;
    }

    const epoch = this.advanceConsumerEpoch(processId);
    const operation = this.startConsumer(processId, opts, epoch);
    this.pendingStarts.set(processId, { identity, promise: operation });
    const clear = (): void => {
      if (this.pendingStarts.get(processId)?.promise === operation) {
        this.pendingStarts.delete(processId);
      }
    };
    void operation.then(clear, clear);
    return operation;
  };

  /**
   * Ensure a targetless, product-scoped management attachment exists.
   *
   * No Workspace/Profile/Environment is materialized for this synthetic
   * consumer. It shares a compatible interactive AgentHost when one exists,
   * otherwise it starts the host early so Settings and onboarding can read
   * management state without opening a code column. Only the ordinary
   * renderer credential is returned; the AgentHost control token remains
   * encapsulated by AgentProcess and its main-process control client.
   */
  ensureManagementConnection = (): Promise<AgentRuntimeConnection> => {
    if (this.pendingManagementConnection) {
      return this.pendingManagementConnection;
    }
    const operation = this.ensureManagementConnectionImpl();
    this.pendingManagementConnection = operation;
    const clear = (): void => {
      if (this.pendingManagementConnection === operation) {
        this.pendingManagementConnection = null;
      }
    };
    void operation.then(clear, clear);
    return operation;
  };

  private ensureManagementConnectionImpl = async (): Promise<AgentRuntimeConnection> => {
    const processId = '__product_management__';
    // No environment is materialized for the management connection.
    const startArg = await this.buildStartArg({ workspaceDir: '' });
    await this.waitForRuntimeInstall?.();
    const compatibilityKey = this.agentHostCompatibilityKey(processId, startArg);
    const { proc, created } = this.getOrCreate(processId, compatibilityKey);
    try {
      const statusType = proc.getStatus().type;
      if (created || !['starting', 'connecting', 'running'].includes(statusType)) {
        await proc.start(startArg);
      }
      const connection = await proc.getRuntimeConnection();
      // Host MCP ownership transfer is a readiness side effect. It must not
      // make the legacy settings read wait for a cold runtime to boot.
      await this.onManagementReady?.(proc);
      return connection;
    } catch (error) {
      this.processes.delete(processId);
      const detached = this.agentHosts.detach(processId);
      if (detached?.lastConsumer) {
        await proc.stop().catch(() => {});
      }
      throw error;
    }
  };

  mutateManagement = async (input: unknown): Promise<ManagementAdminResult> => {
    const request = assertManagementAdminRequest(input);
    await this.ensureManagementConnection();
    const proc = this.processes.get('__product_management__');
    if (!proc) {
      throw new Error('Product management AgentHost is unavailable');
    }
    if (request.method.startsWith('account_')) {
      if (!isDurableCodexAccountRequest(request)) {
        throw new Error('Only durable ChatGPT OAuth account mutations are available through this broker');
      }
      if (!this.durableLocalCodexAccountMutations) {
        throw new Error('Account mutations are unavailable outside local single-user Electron');
      }
      const status = await proc.getManagementAccountStatus();
      if (!hasDurableHostCodexMutation(status)) {
        throw new Error('AgentHost did not attest durable host-scoped Codex account mutations');
      }
    }
    if (isMcpManagementMethod(request.method)) {
      if (!this.durableLocalMcpMutations) {
        throw new Error('MCP mutations are unavailable outside local single-user Electron');
      }
      const status = await proc.getManagementMcpStatus();
      if (!hasDurableHostMcpMutation(status)) {
        throw new Error('AgentHost did not attest durable host-scoped MCP configuration and OAuth stores');
      }
      await this.prepareLocalMcpOwnership?.(status);

      const result = await proc.callManagementAdmin(request.method, request.params as never);
      // mcp_auth_cancel only discards process-local pending state. Every other
      // allowed MCP mutation can change config, credentials, or live instances.
      if (request.method !== 'mcp_auth_cancel') {
        await this.invalidateLocalMcpAgentHosts(request.method === 'mcp_reload_server' ? proc : undefined);
      }
      return result;
    }
    return proc.callManagementAdmin(request.method, request.params as never);
  };

  private evictStaleMcpHost = async (proc: AgentProcess, alreadyStopped = false): Promise<string[]> => {
    const consumers = this.agentHosts.consumersForHost(proc);
    for (const processId of consumers) {
      this.advanceConsumerEpoch(processId);
      this.processes.delete(processId);
      this.consumerRuntimes.delete(processId);
      this.consumerWorkspaceIds.delete(processId);
      this.consumerWorkspaceIdentities.delete(processId);
      this.lastStartArgs.delete(processId);
      this.agentHosts.detach(processId);
      if (processId === '__product_management__') {
        this.pendingManagementConnection = null;
      }
    }
    if (!alreadyStopped) {
      await proc.stop().catch(() => undefined);
    }
    return consumers;
  };

  /** Reload every distinct live local AgentHost after the shared file/token
   * store changes. A peer that cannot reload is evicted before returning an
   * actionable partial-invalidation error, so it can never serve stale MCP. */
  private invalidateLocalMcpAgentHosts = async (alreadyReloaded?: AgentProcess): Promise<void> => {
    const hosts = this.agentHosts
      .hosts()
      .filter((host) => host !== alreadyReloaded && host.getStatus().type === 'running');
    const failures: string[] = [];
    await Promise.all(
      hosts.map(async (host) => {
        try {
          await host.callManagementAdmin('mcp_reload_server', {});
        } catch (error) {
          const consumers = await this.evictStaleMcpHost(host);
          failures.push(`${consumers.join(', ') || 'unattached host'}: ${(error as Error).message}`);
        }
      })
    );
    if (failures.length > 0) {
      throw new Error(
        `MCP mutation committed, but stale AgentHost invalidation failed; affected hosts were stopped: ${failures.join('; ')}`
      );
    }
  };

  /** Main-only account migration read. No IPC handler exposes this method. */
  getManagementAccountStatus = async (): Promise<Record<string, unknown>> => {
    await this.ensureManagementConnection();
    const proc = this.processes.get('__product_management__');
    if (!proc) {
      throw new Error('Product management AgentHost is unavailable');
    }
    return proc.getManagementAccountStatus();
  };

  /** Main-only MCP ownership migration read. No generic control RPC is exposed. */
  getManagementMcpStatus = async (): Promise<Record<string, unknown>> => {
    await this.ensureManagementConnection();
    const proc = this.processes.get('__product_management__');
    if (!proc) {
      throw new Error('Product management AgentHost is unavailable');
    }
    return proc.getManagementMcpStatus();
  };

  private startConsumer = async (processId: string, opts: AgentProcessStartOptions, epoch: number): Promise<void> => {
    await this.runtimeRecovery;
    await this.claimChatRuntime?.(processId);
    if (!this.processes.has(processId)) {
      await this.runtimeJournal?.retire(processId);
    }
    if (!this.isCurrentConsumerEpoch(processId, epoch)) {
      return;
    }
    // Adopt an already-live process rather than restarting it. A renderer that
    // just (re)connected has an empty status map — its auto-launch guard reads
    // that map *before* `watchProcessStatus` seeds it — so a browser reload or
    // WS reconnect re-issues `start` for a processId that is already serving.
    // Without this check `AgentProcess.start` SIGTERMs the live `omni serve`
    // and the conversation dies mid-flight. Deliberate restarts go through
    // `stop()` (drops the process) or `rebuild()`, so they never land here.
    const live = this.processes.get(processId);
    if (live) {
      const type = live.getStatus().type;
      const previous = this.lastStartArgs.get(processId);
      const sameLaunch =
        previous !== undefined && ProcessManager.launchIdentity(previous) === ProcessManager.launchIdentity(opts);
      if (sameLaunch && (type === 'starting' || type === 'connecting' || type === 'running')) {
        // Re-broadcast so the reconnected renderer, which cleared its local
        // status before calling start, leaves `starting` on its own.
        if (this.isCurrentConsumerEpoch(processId, epoch)) {
          this.sendToWindow('agent-process:status', processId, this.getStatus(processId));
        }
        return;
      }
    }
    const previousRuntime = this.consumerRuntimes.get(processId);
    const previousWorkspaceId = this.consumerWorkspaceIds.get(processId);
    const previousWorkspaceIdentity = this.consumerWorkspaceIdentities.get(processId);
    const startArg = await this.buildStartArg(opts);
    if (!this.isCurrentConsumerEpoch(processId, epoch)) {
      return;
    }
    await this.waitForRuntimeInstall?.();
    if (!this.isCurrentConsumerEpoch(processId, epoch)) {
      return;
    }
    const compatibilityKey = this.agentHostCompatibilityKey(processId, startArg);
    const { proc, created } = this.getOrCreate(processId, compatibilityKey);
    const preservedAttachment = live === proc;
    try {
      const statusType = proc.getStatus().type;
      if (created || !['starting', 'connecting', 'running'].includes(statusType)) {
        await proc.start(startArg);
      }
      if (!this.isCurrentConsumerEpoch(processId, epoch)) {
        await this.retireStaleStart(processId, proc, previousRuntime);
        return;
      }
      const workspaceIdentity = ProcessManager.workspaceIdentity(startArg);
      const workspaceId =
        previousWorkspaceId && previousWorkspaceIdentity === workspaceIdentity
          ? previousWorkspaceId
          : `workspace_${randomUUID().replaceAll('-', '')}`;
      const runtime = await proc.configureConsumer(processId, workspaceId, startArg);
      if (!this.isCurrentConsumerEpoch(processId, epoch)) {
        const isPriorRuntime =
          previousRuntime?.environmentId === runtime.environmentId &&
          previousRuntime.environmentGeneration === runtime.environmentGeneration;
        if (!isPriorRuntime) {
          await proc.stopConsumerEnvironment(runtime).catch((error) => {
            console.warn(`[process-manager] stale environment cleanup failed: ${(error as Error).message}`);
          });
        }
        await this.retireStaleStart(processId, proc, previousRuntime);
        return;
      }
      this.consumerWorkspaceIds.set(processId, workspaceId);
      this.consumerWorkspaceIdentities.set(processId, workspaceIdentity);
      this.consumerRuntimes.set(processId, runtime);
      if (previousRuntime && previousRuntime.environmentId !== runtime.environmentId) {
        await proc.stopConsumerEnvironment(previousRuntime).catch((error) => {
          console.warn(`[process-manager] old environment cleanup failed: ${(error as Error).message}`);
        });
      }
      this.lastStartArgs.set(processId, opts);
    } catch (error) {
      if (preservedAttachment) {
        // Rebinding is transactional from the consumer's perspective. The
        // AgentHost cleans a newly materialized environment when binding
        // fails; keep the prior Workspace/runtime and launch identity live.
        throw error;
      }
      this.consumerRuntimes.delete(processId);
      this.consumerWorkspaceIds.delete(processId);
      this.consumerWorkspaceIdentities.delete(processId);
      this.lastStartArgs.delete(processId);
      this.processes.delete(processId);
      const detached = this.agentHosts.detach(processId);
      if (detached?.lastConsumer) {
        await proc.stop().catch(() => {});
      }
      throw error;
    }
    // A newly attached consumer needs its tailored live host state even though
    // no child process was started for it.
    this.sendToWindow('agent-process:status', processId, this.getStatus(processId));
  };

  private retireStaleStart = async (
    processId: string,
    proc: AgentProcess,
    previousRuntime: AgentHostConsumerRuntime | undefined
  ): Promise<void> => {
    // A rebind keeps the previous runtime/attachment authoritative until the
    // newer intent takes over. A cancelled first start owns no useful state.
    if (previousRuntime || this.processes.get(processId) !== proc) {
      return;
    }
    this.processes.delete(processId);
    const detached = this.agentHosts.detach(processId);
    if (detached?.lastConsumer) {
      await proc.stop().catch(() => {});
    }
  };

  stop = async (processId: string, opts?: AgentProcessStopOptions): Promise<AgentProcessStopResult> => {
    const pending = this.pendingStops.get(processId);
    if (pending) {
      return pending;
    }
    const epoch = this.advanceConsumerEpoch(processId);
    const pendingStart = this.pendingStarts.get(processId)?.promise;
    const operation = (async () => {
      await pendingStart?.catch(() => undefined);
      if (this.isCurrentConsumerEpoch(processId, epoch)) {
        return this.stopConsumer(processId, opts);
      }
      return this.emptyStopResult();
    })().finally(() => {
      this.pendingStops.delete(processId);
    });
    this.pendingStops.set(processId, operation);
    return operation;
  };

  /** Closing a tab is permanent for its unique consumer ID. A delayed start
   * from another renderer must not recreate an invisible runtime. */
  retire = async (processId: string): Promise<AgentProcessStopResult> => {
    this.retiredConsumers.add(processId);
    try {
      const result = await this.stop(processId, { discardSnapshot: true });
      await this.runtimeJournal?.retire(processId);
      return result;
    } finally {
      this.lastStartArgs.delete(processId);
    }
  };

  private emptyStopResult = (): AgentProcessStopResult => ({
    scope: 'none',
    shutdown: 'not-applicable',
  });

  private stopConsumer = async (processId: string, opts?: AgentProcessStopOptions): Promise<AgentProcessStopResult> => {
    let target = this.detachedStops.get(processId);
    if (!target) {
      const proc = this.processes.get(processId);
      if (!proc) {
        return this.emptyStopResult();
      }
      const detached = this.agentHosts.detach(processId);
      target = { proc, runtime: this.consumerRuntimes.get(processId), lastConsumer: detached?.lastConsumer ?? false };
      this.detachedStops.set(processId, target);
    }
    const { proc, runtime, lastConsumer } = target;
    if (!lastConsumer) {
      // A neighboring consumer may have started closing the entire host while
      // this environment's stop was failing. Its control client disappears at
      // the start of host shutdown, not at confirmed process exit. Join that
      // shutdown (or retry its failure) before acknowledging this environment.
      const hostStop = [...this.detachedStops].find(
        ([id, other]) => id !== processId && other.proc === proc && other.lastConsumer
      );
      if (hostStop) {
        await this.stop(hostStop[0]);
      }
    }
    if (opts?.discardSnapshot && runtime) {
      await proc.discardConsumerSnapshot(runtime).catch(() => {});
    }
    const result = lastConsumer
      ? await proc.stop()
      : runtime
        ? await proc.stopConsumerEnvironment(runtime)
        : this.emptyStopResult();
    // No finally: failed shutdown retains both the runtime and its cleanup
    // target. A second stop must not turn an earlier failure into a no-op.
    this.detachedStops.delete(processId);
    this.consumerRuntimes.delete(processId);
    this.consumerWorkspaceIds.delete(processId);
    this.consumerWorkspaceIdentities.delete(processId);
    this.processes.delete(processId);
    return result;
  };

  /** All materializing operations share the same per-consumer lane as start.
   * Stop waits for this lane before retiring its final environment. */
  private mutateConsumer = <R>(processId: string, mutate: () => Promise<R>): Promise<R> => {
    if (this.retiredConsumers.has(processId)) {
      return Promise.reject(new Error('This chat tab has been closed'));
    }
    if (this.pendingStops.has(processId) || this.detachedStops.has(processId)) {
      return Promise.reject(new Error('This chat runtime is still stopping'));
    }
    const previous = this.pendingStarts.get(processId)?.promise;
    const epoch = this.advanceConsumerEpoch(processId);
    const task = Promise.resolve(previous)
      .catch(() => undefined)
      .then(async () => {
        if (!this.isCurrentConsumerEpoch(processId, epoch)) {
          throw new Error('Runtime operation was superseded');
        }
        await this.claimChatRuntime?.(processId);
        return mutate();
      });
    const promise = task.then(() => undefined);
    this.pendingStarts.set(processId, { identity: `mutation:${epoch}`, promise });
    const clear = () => {
      if (this.pendingStarts.get(processId)?.promise === promise) {
        this.pendingStarts.delete(processId);
      }
    };
    void promise.then(clear, clear);
    return task;
  };

  rebuild = (processId: string, opts: AgentProcessStartOptions): Promise<void> =>
    this.mutateConsumer(processId, () => this.rebuildConsumer(processId, opts));

  private rebuildConsumer = async (processId: string, opts: AgentProcessStartOptions): Promise<void> => {
    const epoch = this.consumerEpochs.get(processId)!;
    if (this.retiredConsumers.has(processId)) {
      throw new Error('This chat tab has been closed');
    }
    if (this.pendingStops.has(processId) || this.detachedStops.has(processId)) {
      throw new Error('This chat runtime is still stopping; retry stopping it before rebuilding');
    }
    const lastOpts = this.lastStartArgs.get(processId);
    const merged: AgentProcessStartOptions = {
      workspaceDir: opts.workspaceDir || lastOpts?.workspaceDir || '',
      ...((opts.projectId ?? lastOpts?.projectId)
        ? { projectId: (opts.projectId ?? lastOpts?.projectId) as string }
        : {}),
      ...((opts.sessionId ?? lastOpts?.sessionId)
        ? { sessionId: (opts.sessionId ?? lastOpts?.sessionId) as string }
        : {}),
      ...((opts.snapshotRef ?? lastOpts?.snapshotRef)
        ? { snapshotRef: (opts.snapshotRef ?? lastOpts?.snapshotRef) as string }
        : {}),
    };
    const startArg = await this.buildStartArg(merged);
    await this.waitForRuntimeInstall?.();
    const previous = this.processes.get(processId);
    const compatibilityKey = this.agentHostCompatibilityKey(processId, startArg);
    if (previous && lastOpts) {
      const previousStartArg = await this.buildStartArg(lastOpts);
      const previousKey = this.agentHostCompatibilityKey(processId, previousStartArg);
      const currentWorkspaceId = this.consumerWorkspaceIds.get(processId);
      if (compatibilityKey === previousKey && currentWorkspaceId) {
        const workspaceIdentity = ProcessManager.workspaceIdentity(startArg);
        const workspaceId =
          this.consumerWorkspaceIdentities.get(processId) === workspaceIdentity
            ? currentWorkspaceId
            : `workspace_${randomUUID().replaceAll('-', '')}`;
        const currentRuntime = this.consumerRuntimes.get(processId);
        // Automatic environments are intentionally reused for the same
        // Workspace/Profile pair while they remain ready. Rebuild must first
        // retire the current environment or materialization is a no-op.
        // Withdrawing it from the consumer map also prevents stale runtime
        // metadata from being reported during the replacement window.
        if (currentRuntime) {
          await previous.stopConsumerEnvironment(currentRuntime);
        }
        this.consumerRuntimes.delete(processId);
        this.sendToWindow('agent-process:status', processId, this.getStatus(processId));
        const runtime = await previous.configureConsumer(processId, workspaceId, startArg);
        this.consumerRuntimes.set(processId, runtime);
        this.consumerWorkspaceIds.set(processId, workspaceId);
        this.consumerWorkspaceIdentities.set(processId, workspaceIdentity);
        this.lastStartArgs.set(processId, merged);
        this.sendToWindow('agent-process:status', processId, this.getStatus(processId));
        return;
      }
    }
    // A replacement host is targetless until the consumer is configured. Use
    // the full awaited start transaction, not a fire-and-forget host spawn.
    await this.stopConsumer(processId);
    if (!this.isCurrentConsumerEpoch(processId, epoch)) {
      return;
    }
    await this.startConsumer(processId, merged, epoch);
  };

  getStatus = (processId: string): WithTimestamp<AgentProcessStatus> => {
    const proc = this.processes.get(processId);
    const baseStatus = proc ? proc.getStatus() : { type: 'uninitialized' as const, timestamp: Date.now() };
    const tailored = proc ? this.statusForConsumer(processId, baseStatus) : baseStatus;
    // A compatible consumer is attached to the shared host before its own
    // environment finishes materializing. Never expose the shared host's
    // running metadata during that window: the consumer is still starting.
    return tailored ?? { type: 'starting' as const, timestamp: Date.now() };
  };

  resizePty = (processId: string, cols: number, rows: number): void => {
    this.processes.get(processId)?.resizePty(cols, rows);
  };

  /**
   * The host workspace dir the given process was last started against (e.g.
   * the chat session's per-conversation scratch dir). Authoritative for
   * mount-name derivation — the store's `workspaceDir` is the workspace
   * *root*, not the live session's mount source.
   */
  getProcessWorkspaceDir(processId: string): string | null {
    return this.lastStartArgs.get(processId)?.workspaceDir || null;
  }

  cleanup = async (): Promise<void> => {
    await this.runtimeRecovery.catch(() => undefined);
    // Invalidate every materialization before awaiting it. Late results see a
    // stale epoch, retire their environment, and cannot repopulate the maps.
    for (const processId of this.pendingStarts.keys()) {
      this.advanceConsumerEpoch(processId);
    }
    await Promise.allSettled([...this.pendingStarts.values()].map((entry) => entry.promise));
    await Promise.allSettled([...this.pendingStops.values()]);
    const hosts = new Set([...this.agentHosts.clear(), ...[...this.detachedStops.values()].map((t) => t.proc)]);
    const exits = [...hosts].map((p) => p.exit());
    await Promise.allSettled(exits);
    this.processes.clear();
    this.lastStartArgs.clear();
    this.consumerRuntimes.clear();
    this.consumerWorkspaceIds.clear();
    this.consumerWorkspaceIdentities.clear();
    this.pendingStarts.clear();
    this.pendingStops.clear();
    this.detachedStops.clear();
    this.pendingManagementConnection = null;
    this.consumerEpochs.clear();
  };
}

/**
 * Register the `agent-process:*` IPC handlers. `resolve(event)` picks the
 * ProcessManager to act on (`() => mgr` in both shells). Returns the channel
 * names for cleanup.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
export function registerProcessHandlers(ipc: IIpcListener, resolve: (event: unknown) => ProcessManager): string[] {
  const channels: string[] = [];
  const h = (ch: string, fn: (pm: ProcessManager, ...args: any[]) => unknown): void => {
    ipc.handle(ch, (event: unknown, ...args: any[]) => fn(resolve(event), ...args));
    channels.push(ch);
  };

  h('agent-process:start', (pm, processId, startArg) => pm.start(processId, startArg));
  h('agent-process:stop', (pm, processId, opts) => pm.stop(processId, opts));
  h('agent-process:rebuild', (pm, processId, rebuildArg) => pm.rebuild(processId, rebuildArg));
  h('agent-process:resize', (pm, processId, cols, rows) => pm.resizePty(processId, cols, rows));
  h('agent-process:get-status', (pm, processId) => pm.getStatus(processId));
  h('management-runtime:ensure', (pm) => pm.ensureManagementConnection());
  h('management-runtime:mutate', (pm, request) => pm.mutateManagement(request));

  return channels;
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export const createProcessManager = (arg: {
  ipc: IIpcListener;
  sendToWindow: <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => void;
  fetchFn?: FetchFn;
  getStoreData?: () => ProcessManagerStoreData;
  resolveGitToken?: (credentialId: string) => Promise<string | undefined>;
  getExtraEnv?: (processId?: string) => Record<string, string> | Promise<Record<string, string>>;
  waitForRuntimeInstall?: () => Promise<void>;
  durableLocalCodexAccountMutations?: boolean;
  durableLocalMcpMutations?: boolean;
  prepareLocalMcpOwnership?: (status: Record<string, unknown>) => void | Promise<void>;
  onManagementReady?: (proc: AgentProcess) => void | Promise<void>;
  runtimeJournalDir?: string;
  claimChatRuntime?: (id: string) => void | Promise<void>;
}) => {
  const {
    ipc,
    sendToWindow,
    fetchFn,
    getStoreData,
    resolveGitToken,
    getExtraEnv,
    waitForRuntimeInstall,
    durableLocalCodexAccountMutations,
    durableLocalMcpMutations,
    prepareLocalMcpOwnership,
    onManagementReady,
  } = arg;

  const processManager = new ProcessManager({
    runtimeJournalDir: arg.runtimeJournalDir,
    claimChatRuntime: arg.claimChatRuntime,
    sendToWindow,
    fetchFn,
    getStoreData,
    resolveGitToken,
    getExtraEnv,
    waitForRuntimeInstall,
    durableLocalCodexAccountMutations,
    durableLocalMcpMutations,
    prepareLocalMcpOwnership,
    onManagementReady,
  });
  const channels = registerProcessHandlers(ipc, () => processManager);

  const cleanup = async () => {
    await processManager.cleanup();
    for (const ch of channels) {
      ipcMain.removeHandler(ch);
    }
  };

  return [processManager, cleanup] as const;
};
