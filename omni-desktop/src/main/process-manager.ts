import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';

import { ipcMain } from 'electron';

import { mirrorContainerChangesToHost } from '@/lib/container-sync';
import { isResidentProcessId } from '@/lib/resident-agent';
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
import {
  hasDurableHostMcpMutation,
  isMcpManagementMethod,
  isProtectedManagedMcpRequest,
} from '@/main/local-mcp-config-owner';
import { getDefaultWorkspaceDir } from '@/main/util';
import { gitTokenEnvName, resolveCredentialForUrl } from '@/shared/git-credentials';
import type { IIpcListener } from '@/shared/ipc-listener';
import { assertManagementAdminRequest, type ManagementAdminResult } from '@/shared/management-admin';
import type {
  AgentProcessStartOptions,
  AgentProcessStatus,
  AgentProcessStopOptions,
  AgentProcessStopResult,
  GitCredential,
  IpcRendererEvents,
  ManagementRuntimeConnection,
  Project,
  SandboxPauseResult,
  SandboxSwitchResult,
  WithTimestamp,
} from '@/shared/types';

export type ProcessManagerStoreData = {
  defaultProfileName: string;
  projects: Project[];
  /** Stored git credential metadata (host-scoped). Tokens are read lazily via
   *  `resolveGitToken`; this list only drives host matching. */
  gitCredentials?: GitCredential[];
};

/**
 * True for host directories the launcher itself manages: a per-conversation
 * scratch dir (`<workspace root>/Sessions/<sessionId>` — the root is
 * user-configurable, so the convention is the `Sessions` parent) or anything
 * under the default workspace tree (`~/Omni/Workspace`, which holds the
 * Personal root and managed `Projects/<slug>/` dirs). Container changes
 * auto-mirror into these without confirmation — the launcher created them, so
 * there is no foreign user data to clobber. This is only consulted for
 * synthesized sources; user-attached project sources never auto-mirror
 * regardless of where they live.
 */
export const isLauncherOwnedDir = (dir: string): boolean => {
  const resolved = path.resolve(dir);
  if (path.basename(path.dirname(resolved)) === 'Sessions') {
    return true;
  }
  const defaultRoot = path.resolve(getDefaultWorkspaceDir());
  return resolved === defaultRoot || resolved.startsWith(defaultRoot + path.sep);
};

/** How often the auto-mirror sweep checks running launcher-owned sandboxes. */
const AUTO_MIRROR_INTERVAL_MS = 15_000;

/**
 * Unified process manager for all agent processes (chat + code tabs).
 *
 * Every agent process is keyed by a string ID:
 *   - `"chat"` for the singleton chat process
 *   - A CodeTabId for code-tab processes
 *
 * Profile resolution: per-project override > user-default. Every profile
 * shares a compatible targetless ``omni serve`` AgentHost and materializes
 * per consumer.
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
  private pendingManagementConnection: Promise<ManagementRuntimeConnection> | null = null;
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

  /** processId → launcher-owned local mounts whose container changes
   *  auto-mirror back to the host (chat scratch dirs, managed project dirs). */
  private mirrorSources = new Map<string, Array<{ mountName: string; workspaceDir: string }>>();
  private mirrorTimer: ReturnType<typeof setInterval> | null = null;
  private mirrorSweepRunning = false;

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
        defaultProfileName: 'host',
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

  private resolveProfileName(projectId: string | undefined, override: string | undefined): string {
    const { defaultProfileName, projects } = this.getStoreData();
    return (
      override ??
      (projectId ? projects.find((p) => p.id === projectId)?.sandboxProfile : undefined) ??
      defaultProfileName
    );
  }

  getExecutionProfile(processId: string): string | undefined {
    const opts = this.lastStartArgs.get(processId);
    return opts ? this.resolveProfileName(opts.projectId, opts.profileNameOverride) : undefined;
  }

  validateProfileSelection(profileName: string): void {
    if (!profileName || this.resolveProfileName(undefined, profileName) !== profileName) {
      throw new Error('This execution environment is not available in this workspace');
    }
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
              if (isResidentProcessId(claim.consumerId)) {
                return;
              }
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
        // Resident agents have distinct principal credentials and therefore
        // never share a compatibility key with interactive tabs or each other.
        getExtraEnv: async () => {
          const base = (await this.getExtraEnv?.(processId)) ?? {};
          return isResidentProcessId(processId) ? { ...base, OMNI_PROJECTS_PRINCIPAL: processId } : base;
        },
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
    const principalScope = isResidentProcessId(processId) ? processId : 'interactive';
    const credentialScope =
      (arg.credentials?.length ?? 0) > 0 || Object.keys(arg.gitTokenEnv ?? {}).length > 0 ? processId : 'shared';
    return JSON.stringify({ principalScope, credentialScope });
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
        containerId: runtime.containerId,
        ...(runtime.paused !== undefined ? { paused: runtime.paused } : {}),
      },
    };
  }

  private async buildStartArg(opts: AgentProcessStartOptions): Promise<AgentProcessStartArg> {
    const profileName = this.resolveProfileName(opts.projectId, opts.profileNameOverride);
    // Multi-project scope mounts the union of declared sources; the
    // single-project path additionally synthesizes a source from the bare
    // workspaceDir (chat scratch dirs, managed project dirs).
    const sources = opts.projectIds?.length
      ? this.resolveMultiProjectSources(opts.projectIds)
      : this.resolveProjectSources(opts.workspaceDir, opts.projectId, opts.sourceOverrideDir);
    this.appendExtraSources(sources, opts.extraSources);
    const startArg: AgentProcessStartArg = {
      profileName,
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
        ...(isLauncherOwnedDir(workspaceDir) ? { launcherOwned: true } : {}),
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

  /**
   * Multi-project scope (resident agents): the union of every scoped
   * project's declared sources. Unknown project ids are skipped;
   * source-less (context-only) projects contribute no mounts — the agent
   * reaches them through the project MCP tools instead. Duplicate
   * directories are mounted once; mount-name collisions across projects
   * get a numeric suffix (first project wins the bare name).
   */
  private resolveMultiProjectSources(projectIds: string[]): AgentProcessSource[] {
    const { projects } = this.getStoreData();
    const sources: AgentProcessSource[] = [];
    const mountNames = new Set<string>();
    const mountedDirs = new Set<string>();
    for (const projectId of projectIds) {
      const project = projects.find((p) => p.id === projectId);
      if (!project) {
        continue;
      }
      for (const declared of project.sources) {
        const mapped = this.mapDeclaredSource(declared);
        if (mapped.kind !== 'git-remote') {
          const resolved = path.resolve(mapped.workspaceDir);
          if (mountedDirs.has(resolved)) {
            continue;
          }
          mountedDirs.add(resolved);
        }
        let mountName = mapped.mountName;
        for (let n = 2; mountNames.has(mountName); n++) {
          mountName = `${mapped.mountName}-${n}`;
        }
        mountNames.add(mountName);
        sources.push({ ...mapped, mountName });
      }
    }
    return sources;
  }

  /**
   * Append caller-supplied extra mounts (e.g. a resident agent's `home`
   * dir next to its assigned project's sources). Deduped by directory —
   * when the extra dir is already among the resolved sources (a
   * context-only project synthesized it as the primary mount), it is
   * skipped rather than mounted twice. Mount-name collisions get a
   * numeric suffix so both mounts survive.
   */
  private appendExtraSources(sources: AgentProcessSource[], extras: AgentProcessStartOptions['extraSources']): void {
    if (!extras?.length) {
      return;
    }
    const mountedDirs = new Set(
      sources.flatMap((s) => (s.kind === 'git-remote' ? [] : [path.resolve(s.workspaceDir)]))
    );
    const mountNames = new Set(sources.map((s) => s.mountName));
    for (const extra of extras) {
      const resolved = path.resolve(extra.workspaceDir);
      if (mountedDirs.has(resolved)) {
        continue;
      }
      let mountName = extra.mountName;
      for (let n = 2; mountNames.has(mountName); n++) {
        mountName = `${extra.mountName}-${n}`;
      }
      this.ensureWorkspaceDir(extra.workspaceDir);
      const kind = this.directoryHasGit(extra.workspaceDir) ? 'local-git' : 'local';
      sources.push({
        mountName,
        kind,
        workspaceDir: extra.workspaceDir,
        writable: true,
        ...(kind === 'local-git' ? this.resolveGitMetadata(extra.workspaceDir) : {}),
        ...(isLauncherOwnedDir(extra.workspaceDir) ? { launcherOwned: true } : {}),
      });
      mountedDirs.add(resolved);
      mountNames.add(mountName);
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
   * `extraSources` follows the project scope already captured by
   * `projectId`/`projectIds`.
   */
  private static launchIdentity(opts: AgentProcessStartOptions): string {
    return JSON.stringify([
      opts.workspaceDir,
      opts.sourceOverrideDir ?? null,
      opts.projectId ?? null,
      opts.projectIds ?? null,
      opts.sessionId ?? null,
      opts.snapshotRef ?? null,
      opts.profileNameOverride ?? null,
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

  private changingModels = false;

  /** Stop requests using the old connection before publishing new settings. */
  changeModels = async (write: () => void): Promise<void> => {
    if (this.changingModels) {
      throw new Error('AI settings are already changing. Please retry.');
    }
    this.changingModels = true;
    try {
      for (const id of this.pendingStarts.keys()) {
        this.advanceConsumerEpoch(id);
      }
      await Promise.allSettled([...this.pendingStarts.values()].map((entry) => entry.promise));
      await Promise.allSettled([...this.pendingStops.values()]);
      await this.pendingManagementConnection?.catch(() => undefined);
      const hosts = new Set([...this.agentHosts.hosts(), ...this.processes.values()]);
      for (const host of hosts) {
        await host.stop(); // A failed stop must prevent the provider change.
        await this.evictStaleMcpHost(host, true);
      }
      this.pendingManagementConnection = null;
      write();
    } finally {
      this.changingModels = false;
    }
  };

  start = (processId: string, opts: AgentProcessStartOptions): Promise<void> => {
    if (this.changingModels) {
      return Promise.reject(new Error('AI settings are changing. Please retry shortly.'));
    }
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
  ensureManagementConnection = (): Promise<ManagementRuntimeConnection> => {
    if (this.changingModels) {
      return Promise.reject(new Error('AI settings are changing. Please retry shortly.'));
    }
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

  private ensureManagementConnectionImpl = async (): Promise<ManagementRuntimeConnection> => {
    const processId = '__product_management__';
    // Profile definitions are consumer-environment concerns; no sandbox is
    // being created here.
    const startArg = await this.buildStartArg({ workspaceDir: '', profileNameOverride: 'host' });
    await this.waitForRuntimeInstall?.();
    const compatibilityKey = this.agentHostCompatibilityKey(processId, startArg);
    const { proc, created } = this.getOrCreate(processId, compatibilityKey);
    try {
      const statusType = proc.getStatus().type;
      if (created || !['starting', 'connecting', 'running'].includes(statusType)) {
        await proc.start(startArg);
      }
      const [connection, mutationCapabilities] = await Promise.all([
        proc.getRuntimeConnection(),
        proc.getManagementMutationCapabilities(),
      ]);
      // Host MCP ownership transfer is a readiness side effect. It must not
      // make the legacy settings read wait for a cold runtime to boot.
      await this.onManagementReady?.(proc);
      return { ...connection, mutationCapabilities };
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
      if (isProtectedManagedMcpRequest(request as { method: string; params: Record<string, unknown> })) {
        throw new Error('The omni-projects MCP server is managed by Omni Desktop and cannot be changed');
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
      this.mirrorSources.delete(processId);
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
    if (!isResidentProcessId(processId)) {
      await this.claimChatRuntime?.(processId);
      if (!this.processes.has(processId)) {
        await this.runtimeJournal?.retire(processId);
      }
      if (!this.isCurrentConsumerEpoch(processId, epoch)) {
        return;
      }
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
      this.trackMirrorSources(processId, startArg.sources);
    } catch (error) {
      if (preservedAttachment) {
        // Rebinding is transactional from the consumer's perspective. The
        // AgentHost cleans a newly materialized environment when binding
        // fails; keep the prior Workspace/runtime and launch identity live.
        throw error;
      }
      this.mirrorSources.delete(processId);
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
    this.mirrorSources.delete(processId);
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
    if (this.changingModels) {
      return Promise.reject(new Error('AI settings are changing. Please retry shortly.'));
    }
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
        if (!isResidentProcessId(processId)) {
          await this.claimChatRuntime?.(processId);
        }
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
      ...((opts.projectIds ?? lastOpts?.projectIds) ? { projectIds: opts.projectIds ?? lastOpts?.projectIds } : {}),
      ...((opts.profileNameOverride ?? lastOpts?.profileNameOverride)
        ? { profileNameOverride: (opts.profileNameOverride ?? lastOpts?.profileNameOverride) as string }
        : {}),
      ...((opts.sessionId ?? lastOpts?.sessionId)
        ? { sessionId: (opts.sessionId ?? lastOpts?.sessionId) as string }
        : {}),
      ...((opts.snapshotRef ?? lastOpts?.snapshotRef)
        ? { snapshotRef: (opts.snapshotRef ?? lastOpts?.snapshotRef) as string }
        : {}),
      ...((opts.extraSources ?? lastOpts?.extraSources)
        ? { extraSources: opts.extraSources ?? lastOpts?.extraSources }
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
        this.trackMirrorSources(processId, startArg.sources);
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

  pause = async (processId: string): Promise<SandboxPauseResult> => {
    const proc = this.processes.get(processId);
    if (!proc) {
      return { ok: false, supported: false, reason: 'process not found' };
    }
    const runtime = this.consumerRuntimes.get(processId);
    const result = await proc.pause(runtime);
    if (runtime && result.ok && result.supported) {
      this.consumerRuntimes.set(processId, { ...runtime, paused: result.paused ?? true });
      this.sendToWindow('agent-process:status', processId, this.getStatus(processId));
    }
    return result;
  };

  unpause = async (processId: string): Promise<SandboxPauseResult> => {
    const proc = this.processes.get(processId);
    if (!proc) {
      return { ok: false, supported: false, reason: 'process not found' };
    }
    const runtime = this.consumerRuntimes.get(processId);
    const result = await proc.unpause(runtime);
    if (runtime && result.ok && result.supported) {
      this.consumerRuntimes.set(processId, { ...runtime, paused: result.paused ?? false });
      this.sendToWindow('agent-process:status', processId, this.getStatus(processId));
    }
    return result;
  };

  /** Bind a conversation in the lifecycle lane so stop/rebuild cannot retire
   * its environment between selecting the target and issuing the bind. */
  bindSession = (processId: string, sessionId: string): Promise<void> =>
    this.mutateConsumer(processId, async () => {
      const proc = this.processes.get(processId);
      const runtime = this.consumerRuntimes.get(processId);
      if (!proc || proc.getStatus().type !== 'running' || !runtime) {
        throw new Error('Resident execution environment is not ready');
      }
      await proc.bindSession(processId, sessionId, runtime);
    });

  switchSandbox = async (processId: string, profileName: string): Promise<SandboxSwitchResult> => {
    try {
      return await this.mutateConsumer(processId, () => this.switchSandboxConsumer(processId, profileName));
    } catch (error) {
      return { ok: false, reason: (error as Error).message };
    }
  };

  private switchSandboxConsumer = async (processId: string, profileName: string): Promise<SandboxSwitchResult> => {
    if (this.retiredConsumers.has(processId) || this.pendingStops.has(processId) || this.detachedStops.has(processId)) {
      return { ok: false, reason: 'This chat runtime is closed or still stopping' };
    }
    const proc = this.processes.get(processId);
    if (!proc) {
      return { ok: false, reason: 'process not found' };
    }
    const previousOpts = this.lastStartArgs.get(processId);
    if (!previousOpts) {
      return { ok: false, fallback: true, reason: 'launch binding not found' };
    }
    const nextOpts = { ...previousOpts, profileNameOverride: profileName };
    const previousArg = await this.buildStartArg(previousOpts);
    const nextArg = await this.buildStartArg(nextOpts);
    const previousKey = this.agentHostCompatibilityKey(processId, previousArg);
    const nextKey = this.agentHostCompatibilityKey(processId, nextArg);
    if (nextKey !== previousKey) {
      return {
        ok: false,
        fallback: true,
        reason: 'profile change crosses an AgentHost security boundary',
      };
    }
    const workspaceId = this.consumerWorkspaceIds.get(processId);
    if (!workspaceId) {
      return { ok: false, fallback: true, reason: 'workspace binding not found' };
    }
    try {
      const currentRuntime = this.consumerRuntimes.get(processId);
      const runtime = await proc.configureConsumer(processId, workspaceId, nextArg);
      this.consumerRuntimes.set(processId, runtime);
      if (currentRuntime && currentRuntime.environmentId !== runtime.environmentId) {
        await proc.stopConsumerEnvironment(currentRuntime).catch((error) => {
          console.warn(`[process-manager] old environment cleanup failed: ${(error as Error).message}`);
        });
      }
      this.lastStartArgs.set(processId, nextOpts);
      this.sendToWindow('agent-process:status', processId, this.getStatus(processId));
      return {
        ok: true,
        profile: profileName,
        containerId: runtime.containerId,
        services: runtime.services,
      };
    } catch (error) {
      return { ok: false, reason: (error as Error).message };
    }
  };

  /**
   * Look up a running process's WebSocket URL for a code tab linked to the given ticketId.
   * Used by ProjectManager to reuse an existing sandbox instead of creating a duplicate.
   */
  getRunningWsUrlForTicket(ticketId: string, codeTabs: Array<{ id: string; ticketId?: string }>): string | null {
    for (const tab of codeTabs) {
      if (tab.ticketId !== ticketId) {
        continue;
      }
      const status = this.getStatus(tab.id);
      if (status.type === 'running' && status.data.wsUrl) {
        return status.data.wsUrl;
      }
    }
    return null;
  }

  /**
   * Find a running docker container id for the given project. Used by
   * ProjectManager to ``docker exec`` against the agent's workspace for
   * PR-diff and merge operations.
   *
   * Returns the first running process's container id (any process for
   * the project is fine — they all share the per-project snapshot).
   *
   * Matches on the single `projectId` only, NEVER `projectIds`: a
   * multi-project resident's container has collision-suffixed mount
   * names, so the PR machinery's `/workspace/<mountName>` paths are not
   * guaranteed to hold there.
   */
  getProjectContainerId(projectId: string): string | null {
    for (const [processId, opts] of this.lastStartArgs.entries()) {
      if (opts.projectId !== projectId) {
        continue;
      }
      const status = this.getStatus(processId);
      // ``running`` is the post-connect state; ``connecting`` already has
      // ``data.containerId`` because the readiness payload arrives before
      // the WS handshake completes. Accept both so PR queries don't have
      // to wait for the agent's WS to be fully open.
      if ((status.type === 'running' || status.type === 'connecting') && status.data.containerId) {
        return status.data.containerId;
      }
    }
    return null;
  }

  getProcessContainerId(processId: string): string | null {
    const status = this.getStatus(processId);
    if ((status.type === 'running' || status.type === 'connecting') && status.data.containerId) {
      return status.data.containerId;
    }
    return null;
  }

  /**
   * Container ids of every live agent process (``connecting`` or
   * ``running``). Feeds the startup orphan sweep's protected set so it
   * never removes a container a session of this launcher instance is
   * already attached to.
   */
  getAllContainerIds(): string[] {
    return this.getContainerOwners().map((o) => o.containerId);
  }

  /**
   * Same live-container set as {@link getAllContainerIds}, but keyed by the
   * owning process id — the Sandboxes inventory joins these to tab/session
   * titles for the "who owns this container" column.
   */
  getContainerOwners(): Array<{ processId: string; containerId: string }> {
    const owners: Array<{ processId: string; containerId: string }> = [];
    for (const processId of this.processes.keys()) {
      const status = this.getStatus(processId);
      if ((status.type === 'running' || status.type === 'connecting') && status.data.containerId) {
        owners.push({ processId, containerId: status.data.containerId });
      }
    }
    return owners;
  }

  /**
   * The host workspace dir the given process was last started against (e.g.
   * the chat session's per-conversation scratch dir). Authoritative for
   * mount-name derivation — the store's `workspaceDir` is the workspace
   * *root*, not the live session's mount source.
   */
  getProcessWorkspaceDir(processId: string): string | null {
    return this.lastStartArgs.get(processId)?.workspaceDir || null;
  }

  /**
   * Remember which of a launch's mounts auto-mirror, and make sure the sweep
   * timer is running when at least one process has any. Only docker-backed
   * sessions ever mirror — the sweep keys off `getProcessContainerId`, which
   * is null for host backends.
   */
  private trackMirrorSources(processId: string, sources: AgentProcessSource[]): void {
    const owned = sources
      .filter(
        (s): s is Extract<AgentProcessSource, { workspaceDir: string }> =>
          (s.kind === 'local' || s.kind === 'local-git') && s.launcherOwned === true
      )
      .map((s) => ({ mountName: s.mountName, workspaceDir: s.workspaceDir }));
    if (owned.length === 0) {
      this.mirrorSources.delete(processId);
      return;
    }
    this.mirrorSources.set(processId, owned);
    if (!this.mirrorTimer) {
      this.mirrorTimer = setInterval(() => {
        void this.sweepMirrors();
      }, AUTO_MIRROR_INTERVAL_MS);
      // Mirroring must never keep the process alive on its own.
      this.mirrorTimer.unref?.();
    }
  }

  /**
   * One auto-mirror pass: for every running launcher-owned mount, mirror the
   * container's changed-vs-seed set onto the host dir. The mirror is
   * idempotent (it copies current files, not patches), so a no-change sweep
   * is a cheap git-diff inside the container. Re-entrancy guarded — slow
   * docker execs must not stack sweeps.
   */
  private sweepMirrors = async (): Promise<void> => {
    if (this.mirrorSweepRunning) {
      return;
    }
    this.mirrorSweepRunning = true;
    try {
      for (const [processId, mounts] of this.mirrorSources.entries()) {
        const containerId = this.getProcessContainerId(processId);
        if (!containerId) {
          continue;
        }
        for (const mount of mounts) {
          try {
            await mirrorContainerChangesToHost(containerId, mount.mountName, mount.workspaceDir);
          } catch {
            // Transient (container mid-restart, docker hiccup) — next sweep retries.
          }
        }
      }
    } finally {
      this.mirrorSweepRunning = false;
    }
  };

  cleanup = async (): Promise<void> => {
    await this.runtimeRecovery.catch(() => undefined);
    if (this.mirrorTimer) {
      clearInterval(this.mirrorTimer);
      this.mirrorTimer = null;
    }
    this.mirrorSources.clear();
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
  h('agent-process:pause', (pm, processId) => pm.pause(processId));
  h('agent-process:unpause', (pm, processId) => pm.unpause(processId));
  h('agent-process:switch-sandbox', (pm, processId, profileName) => pm.switchSandbox(processId, profileName));

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
