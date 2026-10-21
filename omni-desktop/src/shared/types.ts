import type { Rectangle } from 'electron/main';
import type { Schema } from 'electron-store';

import type { ManagementAdminRequest, ManagementAdminResult } from '@/shared/management-admin';
import type { VoicePersona } from '@/shared/voice-personas';

// Normally we'd use SWR or some query library, but I had some issues with react render cycles and the easiest fix
// was to just move all the fetching outside react. Also SWR doesn't narrow its data field when the request is
// successful, which is suuuuper annoying. This type provides a similar API, but better types. Just have to implement
// the fetching logic yourself.
export type AsyncRequest<T, E> =
  | {
      isUninitialized: true;
      isLoading: false;
      isError: false;
      isSuccess: false;
    }
  | {
      isUninitialized: false;
      isLoading: true;
      isError: false;
      isSuccess: false;
    }
  | {
      isUninitialized: false;
      isLoading: false;
      isError: true;
      isSuccess: false;
      error: E;
    }
  | {
      isUninitialized: false;
      isLoading: false;
      isError: false;
      isSuccess: true;
      data: T;
    };

/**
 * Window size and position properties, used to save and restore window state.
 */
export type WindowProps = {
  bounds: Rectangle;
  isMaximized: boolean;
  isFullScreen: boolean;
};

/**
 * Data stored in the electron store.
 */
export type LayoutMode = 'work' | 'chat' | 'routines' | 'plugins' | 'sandboxes' | 'settings' | 'gallery';
export type OmniTheme =
  | 'omni'
  | 'teams-light'
  | 'teams-dark'
  | 'default'
  | 'tokyo-night'
  | 'vscode-dark'
  | 'vscode-light'
  | 'utrgv';

/**
 * Summary of a sandbox profile available to the launcher. Discovered at
 * runtime by the main process from disk (`<config>/sandbox/*.yml`) plus
 * bundled built-ins. Not persisted to the store — re-derived each boot.
 */
export type ProfileSummary = {
  /** Profile name; matches the YAML filename without extension. */
  name: string;
  /** Human-readable label for UI. */
  label: string;
  /** Sandbox client type from the profile (`unix_local`, `docker`, ...). */
  clientType: string;
  /** True for profiles shipped with the launcher (not user-created). */
  builtin: boolean;
  /** Absolute path of the YAML backing this profile; null for the implicit host profile. */
  path: string | null;
  origin: 'builtin' | 'user-override' | 'implicit';
  /** Parsed highlights for the detail pane; absent fields simply unknown. */
  details?: { image?: string; services?: string[]; runAs?: string; confine?: boolean };
};

/**
 * One Docker container carrying the omni-code label, as shown in the
 * Sandboxes → Running pane. Ownership is joined main-side against live
 * AgentHost consumer environments.
 */
export type SandboxContainerSummary = {
  id: string;
  name: string;
  image: string;
  createdAt: string;
  state: string;
  ownerKind: 'process' | 'orphan';
  /** Human label for the owner (session/tab title); null for orphans. */
  ownerLabel: string | null;
};

/** Result of the Docker substrate probe (Sandboxes → Health pane). */
export type SandboxSubstrateStatus = {
  docker: 'ok' | 'missing' | 'daemon-down';
  dockerVersion?: string;
};

export type AudioSettings = {
  /** mediaDevices deviceId for the input mic. Null = OS default. */
  inputDeviceId: string | null;
  /** mediaDevices deviceId for output (applied via setSinkId on a routed <audio> element). Null = OS default. */
  outputDeviceId: string | null;
  echoCancellation: boolean;
  noiseSuppression: boolean;
  autoGainControl: boolean;
};

export type WslBackend = {
  kind: 'wsl';
  /** WSL distro name as reported by `wsl.exe -l -q`. */
  distro: string;
  /** Port the daemon listens on. Re-picked and persisted by WslBackendManager
   *  before window creation on fresh spawns; kept as-is when a healthy
   *  persistent daemon is adopted. */
  port: number;
  /** Persistent daemon mode: the daemon detaches from the app (nohup + durable
   *  secret), survives app quit, and is adopted on the next boot. Absent/false
   *  = per-boot tracked-child lifecycle. */
  persistent?: boolean;
};

/** A self-hosted server-mode launcher (homelab box, Tailscale node, LAN
 *  server). No identity — auth is the server's own ``/api/ws-token``, gated
 *  to loopback + ``OMNI_TRUSTED_CIDRS``. */
export type ServerBackend = {
  kind: 'server';
  /** Origin of the server (e.g. ``http://my-server:3001``). No trailing slash. */
  url: string;
};

export type RemoteBackend = WslBackend | ServerBackend;

/** Shape injected into the renderer via preload — wsl kind carries the resolved live URL. */
export type RemoteBackendBootstrap = (WslBackend & { url: string }) | ServerBackend;

export type StoreData = {
  workspaceDir?: string;
  /**
   * Name of the default sandbox profile. Resolved at launch time against
   * the launcher's profile chain (built-in → user-default → per-project).
   * Built-in profiles always present: `host` (no isolation, unix_local) and
   * `devbox` (docker with code-server + VNC).
   */
  defaultProfileName: string;
  /**
   * Sandbox profiles the UI picker should offer. When set by the backend,
   * the picker shows exactly these. Unset → the renderer falls back to the
   * built-in list (`host`/`devbox`).
   */
  availableSandboxProfiles?: string[];
  launcherWindowProps?: WindowProps;
  appWindowProps?: WindowProps;
  optInToLauncherPrereleases: boolean;
  previewFeatures: boolean;
  /** System notifications when a backgrounded agent finishes or needs approval. */
  notifyOnAgentAttention: boolean;
  /** "Text size" in percent (90 | 100 | 110 | 125), applied as the CSS root
   *  font-size percentage. */
  textScale: number;
  /** Local voice (Option A): use on-device Parakeet STT + Pocket TTS instead of a hosted voice model. */
  localVoiceEnabled: boolean;
  /**
   * User-created voice personas (local voice only). Built-in personas (Default,
   * Jarvis) live in code (`shared/voice-personas.ts`), never here.
   */
  voicePersonas: VoicePersona[];
  /** Id of the selected voice persona (built-in or custom). Empty/stale → Default. */
  activeVoicePersonaId: string;
  /**
   * Hotkey (react-hotkeys-hook combo, e.g. `alt+v`) that toggles voice recording
   * on the hovered code-deck column or the active chat. Null = unbound.
   */
  voiceToggleHotkey: string | null;

  layoutMode: LayoutMode;
  theme: OmniTheme;
  onboardingComplete: boolean;
  /** User dismissed the "Use Omni from your terminal" card in Spaces. */
  cliCardDismissed: boolean;
  /**
   * Set when the Electron app is linked to a remote launcher backend. When
   * non-null the renderer routes its transport to the backend over WebSocket
   * instead of using local Electron IPC, so chat sessions, projects, tickets
   * etc. live in the backend's store.
   *
   * ``kind: 'wsl'`` — the server build running as a daemon inside a WSL
   * distro on this machine (Windows only), authenticated by a per-boot
   * shared secret minted by WslBackendManager.
   *
   * ``kind: 'server'`` — a self-hosted server-mode launcher the user runs
   * themselves (homelab, Tailscale, LAN). No identity; auth tokens come
   * from the server's own ``/api/ws-token`` (loopback +
   * ``OMNI_TRUSTED_CIDRS``).
   *
   * ``null`` is the standalone-Electron mode (today's default).
   */
  remoteBackend: RemoteBackend | null;
  projects: Project[];
  milestones: Milestone[];
  pages: Page[];
  inboxItems: InboxItem[];
  tasks: Task[];
  tickets: Ticket[];
  /**
   * Autopilot dispatch concurrency: the max tickets auto-dispatch will run
   * at once across all projects. Manual starts are never gated by this.
   * Default: 3.
   */
  wipLimit: number;
  scheduledTasks: ScheduledTask[];
  /**
   * Background-actor outcomes for Home's "While you were away" feed,
   * newest-first. Appended by main-process managers via
   * `appendActivityEvent`; capped and time-pruned on every write.
   */
  activityLog: ActivityEvent[];
  /** Growth hints (Home's one-slot suggestions) the user dismissed, by id. */
  dismissedHomeHints: string[];
  schemaVersion: number;
  codeTabs: CodeTab[];
  /** Authority-owned removal outbox. Never writable through generic renderer settings. */
  chatCleanupJobs?: CodeTab[];
  /**
   * Indexed agent conversations, newest-first. Project identity is retained
   * so the sidebar can group sessions beneath their project. Archived entries
   * remain fully restorable and the index is capped by the pruning helper.
   */
  chatConversations: ChatConversation[];
  activeCodeTabId: CodeTabId | null;
  codeLayoutMode: CodeLayoutMode;
  activeTicketId: TicketId | null;

  /**
   * How each skill was installed. Missing means discovered locally (user
   * created the directory manually). Persists across sessions so the UI
   * can show provenance (e.g. "Installed from pdf.skill").
   *
   * Enabled/disabled state is determined by directory location:
   * `<configDir>/skills/` = active, `<configDir>/skills-disabled/` = disabled.
   */
  skillSources: Record<string, SkillSource>;

  /**
   * Marketplace bundles the user has installed, keyed by `${repo}:${plugin}`.
   * Records the ref + manifest version + skill names that were on disk at
   * install time, so update checks can diff against the live manifest.
   */
  installedBundles: Record<string, InstalledBundle>;

  /**
   * Stored git credentials (metadata only — token bytes live in the
   * `SecretStore`, keyed by `id`). Host-scoped: a `git-remote` source resolves
   * its credential by matching the URL host. Safe to broadcast to the renderer
   * because it carries no secret. See `git-credentials.ts`.
   */
  gitCredentials: GitCredential[];

  /**
   * Pull requests detected for ticket-less scopes (chat conversations,
   * un-ticketed code tabs), scoped by `sessionId`/`codeTabId`. This is the
   * memory that lets a merged PR render the ✓ badge only when this launcher
   * watched it while open — restart-proof, mirroring `Ticket.pullRequests`
   * for tickets. Pruned by `lastSeenAt` to a bounded length on write.
   */
  pullRequestLinks: PullRequestLink[];

  /**
   * Linked GitHub account (display metadata only — the OAuth token lives in the
   * `SecretStore` as the `github.com` credential). Drives the "Connect GitHub"
   * state and repo discovery. Undefined when no account is linked.
   */
  githubAccount?: GithubAccount;

  /**
   * Agent model providers — the source of truth for what the desktop wrote to
   * `models.json`. Materialized to `<configDir>/models.json` at agent launch.
   * See `config-materializer.ts`.
   */
  modelsConfig: ModelsConfig;
  /** Agent MCP servers (`mcp.json`). Same materialization as `modelsConfig`. */
  mcpConfig: McpConfig;
  /** Sandbox network egress policy (`network.json`). Non-secret; materialized verbatim. */
  networkConfig: NetworkConfig;
  /**
   * Raw `.env` contents for the agent, materialized to a real `.env` file.
   */
  envVars: string;
  /**
   * One-shot guard for the v23 migration that imported pre-existing on-disk
   * `models.json`/`mcp.json`/`network.json`/`.env` into the store keys above.
   * Set once the import runs.
   */
  agentConfigMigratedFromFiles?: boolean;

  /**
   * Set only after the local Electron AgentHost proves structural parity with
   * Desktop's materialized mcp.json and attests durable host-scoped MCP config
   * and OAuth stores. Once set, Desktop must never materialize mcp.json again;
   * canonical Omniagents CRUD/auth RPCs are its sole writer.
   *
   * Server deployments ignore this host-local marker.
   */
  mcpConfigOwnership?: 'omniagents';

  /**
   * Set only after the local Electron AgentHost proves that its canonical
   * account RPCs persist Codex OAuth credentials durably in the product's host
   * config directory. Before this marker, the legacy Desktop OAuth handlers
   * remain available as a migration fallback. After it, account mutations fail
   * closed instead of writing/removing `codex.json` behind Omniagents' back.
   *
   * Server deployments never read or write this host-local marker.
   */
  codexAccountOwnership?: 'omniagents';

  /** Voice mode audio device + processing preferences. */
  audioSettings: AudioSettings;

  /** Browser profiles (default + user-created). Always contains at least the built-in default. */
  browserProfiles: BrowserProfile[];
  /**
   * Browser tabsets keyed by id. Conventional ids:
   * - `col:<codeTabId>` — standalone browser column in the code deck.
   * - `dock:<codeTabId>` — per-session browser surface inside the env dock.
   */
  browserTabsets: Record<BrowserTabsetId, BrowserTabset>;
  /** Capped-length visit history, newest first. */
  browserHistory: BrowserHistoryEntry[];
  /** User-curated bookmarks. */
  browserBookmarks: BrowserBookmark[];

  /**
   * One-shot notice surfaced after the v18 pages-relocation migration
   * (Task #18). Present only when (a) the migration found legacy files
   * that still exist on disk and (b) the user hasn't dismissed the
   * notice yet. Cleared when the user clicks dismiss or runs cleanup.
   */
  pagesMigration?: PagesMigrationState;
};

/**
 * Persisted state for the post-migration notice. The summary is captured
 * once on the boot the migration ran and replayed to the renderer until
 * the user acknowledges it.
 */
export type PagesMigrationState = {
  /** Counts of files copied per source bucket. */
  summary: {
    perProjectPagesCopied: number;
    rootPagesFromContextMd: number;
    mcpPagesCopied: number;
    skippedAlreadyMigrated: number;
  };
  /**
   * Legacy directories/files still on disk that the user can safely delete
   * to reclaim space. Absolute paths, sorted.
   */
  legacyPaths: string[];
  /** True once the user dismissed the notice or ran cleanup. */
  acknowledged: boolean;
};

export type ScheduledTaskSchedule =
  | { kind: 'manual' }
  | { kind: 'interval'; everyMinutes: number }
  | { kind: 'daily'; time: string; weekdaysOnly?: boolean }
  | { kind: 'weekly'; dayOfWeek: number; time: string };

export type ScheduledTaskRunStatus = 'running' | 'waiting_for_approval' | 'completed' | 'skipped' | 'failed';

export type ScheduledTaskPermissionMode = 'ask';

export type ScheduledTaskRun = {
  id: string;
  scheduledFor: number;
  startedAt: number;
  completedAt?: number;
  status: ScheduledTaskRunStatus;
  runId?: string;
  sessionId?: string;
  processId?: string;
  pendingApprovalToolName?: string;
  pendingApprovalServerLabel?: string;
  pendingApprovalKind?: 'function' | 'mcp';
  reason?: string;
};

export type ScheduledTaskAllowedMcpTool = {
  serverLabel: string;
  toolName: string;
};

export type ScheduledTask = {
  id: string;
  name: string;
  description: string;
  instructions: string;
  schedule: ScheduledTaskSchedule;
  permissionMode: ScheduledTaskPermissionMode;
  enabled: boolean;
  projectId?: ProjectId;
  profileName?: string;
  createdAt: number;
  updatedAt: number;
  nextRunAt: number | null;
  lastRunAt?: number;
  runningSessionId?: string;
  runningProcessId?: string;
  allowedToolNames: string[];
  allowedMcpTools: ScheduledTaskAllowedMcpTool[];
  history: ScheduledTaskRun[];
};

/**
 * One entry in Home's "While you were away" feed: something a background
 * actor (routine run, ticket agent) finished without the user watching.
 * Outcomes only — transient attention states (approvals, gates) live in the
 * Needs-you rollup, never here, so the feed can't go stale.
 */
export type ActivityEvent = {
  id: string;
  /** When the event happened (ms). */
  at: number;
  kind: 'routine_run_finished' | 'agent_run_finished' | 'run_failed';
  /** What ran — the routine's name or the ticket's title. */
  title: string;
  /** One-line result summary, when the actor reported one. */
  outcome?: string;
  projectId?: ProjectId;
  /** Exactly one deep-link target. */
  link: { type: 'ticket'; ticketId: TicketId } | { type: 'routine'; taskId: string };
};

/** The pull-request watcher's event vocabulary (see src/lib/pull-request-watch.ts). */
export type PullRequestEventKind = 'merged' | 'closed' | 'approved' | 'changes_requested' | 'ci_failed' | 'ci_green';

export type ScheduledTaskInput = {
  name: string;
  description?: string;
  instructions: string;
  schedule: ScheduledTaskSchedule;
  permissionMode?: ScheduledTaskPermissionMode;
  enabled?: boolean;
  projectId?: ProjectId;
  profileName?: string;
};

export type ScheduledTaskUpdate = Partial<ScheduledTaskInput> & { enabled?: boolean };

// #region Browser types

export type BrowserProfileId = string;
export type BrowserTabsetId = string;
export type BrowserTabId = string;

/**
 * A browser identity. Each profile maps to an Electron `partition` name so its
 * cookies, localStorage, and cache are isolated from other profiles and from
 * non-browser webviews in the app.
 */
export type BrowserProfile = {
  id: BrowserProfileId;
  label: string;
  /** Electron webview `partition=` attribute, e.g. `persist:browser-default`. */
  partition: string;
  /** True for the built-in default profile. Cannot be deleted. */
  builtin?: boolean;
  /** Non-persistent partition (`partition` starts without `persist:`). */
  incognito?: boolean;
  createdAt: number;
};

/** One open tab within a tabset. */
export type BrowserTab = {
  id: BrowserTabId;
  url: string;
  title?: string;
  favicon?: string;
  /** Per-tab profile override. Falls back to the tabset's profile. */
  profileId?: BrowserProfileId;
  pinned?: boolean;
  createdAt: number;
  lastActiveAt: number;
};

/**
 * A collection of tabs owned by one surface (e.g. one browser column in the
 * code deck, or one per-session dock browser).
 */
export type BrowserTabset = {
  id: BrowserTabsetId;
  profileId: BrowserProfileId;
  tabs: BrowserTab[];
  activeTabId: BrowserTabId | null;
  createdAt: number;
  updatedAt: number;
};

export type BrowserHistoryEntry = {
  id: string;
  url: string;
  title?: string;
  profileId: BrowserProfileId;
  visitedAt: number;
};

export type BrowserBookmark = {
  id: string;
  url: string;
  title: string;
  folder?: string;
  createdAt: number;
};

/** Item returned by the omnibox suggestion service. */
export type BrowserSuggestion = {
  kind: 'history' | 'bookmark' | 'search';
  url: string;
  title?: string;
  /** Higher = more relevant. Used only for sort. */
  score: number;
};

// #endregion

// The electron store uses JSON schema to validate its data.

/**
 * JSON schema for the window properties.
 */
const winSizePropsSchema = {
  type: 'object',
  properties: {
    bounds: {
      type: 'object',
      properties: {
        x: { type: 'number' },
        y: { type: 'number' },
        width: { type: 'number' },
        height: { type: 'number' },
      },
    },
    isMaximized: { type: 'boolean' },
    isFullScreen: { type: 'boolean' },
  },
};

/**
 * JSON schema for the store data.
 */
export const schema: Schema<StoreData> = {
  workspaceDir: {
    type: 'string',
  },
  defaultProfileName: {
    type: 'string',
    default: 'host',
  },
  launcherWindowProps: winSizePropsSchema,
  appWindowProps: winSizePropsSchema,
  optInToLauncherPrereleases: {
    type: 'boolean',
    default: false,
  },
  previewFeatures: {
    type: 'boolean',
    default: false,
  },
  notifyOnAgentAttention: {
    type: 'boolean',
    default: false,
  },
  textScale: {
    type: 'number',
    enum: [90, 100, 110, 125],
    default: 100,
  },
  localVoiceEnabled: {
    type: 'boolean',
    default: false,
  },
  voicePersonas: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        name: { type: 'string' },
        builtin: { type: 'boolean' },
        instructions: { type: 'string' },
        voice: { type: 'object' },
      },
      required: ['id', 'name', 'builtin', 'instructions', 'voice'],
    },
  },
  activeVoicePersonaId: {
    type: 'string',
    default: 'default',
  },
  voiceToggleHotkey: {
    type: ['string', 'null'],
    default: null,
  },

  layoutMode: {
    type: 'string',
    // 'code' and 'os' are pre-v20 names for 'spaces'; 'more' is the retired
    // mobile overflow page; 'projects' is the pre-split container tab;
    // 'inbox' folded into Work, 'routines' into Agents, and 'home' retired
    // with the unified sidebar. Kept so existing stores load before the
    // renderer's migrateLayoutMode pass converts them.
    enum: [
      'home',
      'inbox',
      'work',
      'chat',
      'spaces',
      'os',
      'code',
      'projects',
      'dashboards',
      'routines',
      'agents',
      'plugins',
      'sandboxes',
      'settings',
      'more',
      'gallery',
    ],
    default: 'chat',
  },
  theme: {
    type: 'string',
    // Must list every OmniTheme: the store is created with
    // `clearInvalidConfig: true`, so a member missing here silently WIPES the
    // config of anyone persisted on that theme (teams-light/teams-dark were
    // missing until Phase 10). A parity test guards this against themeDefs.
    enum: ['omni', 'teams-light', 'teams-dark', 'default', 'tokyo-night', 'vscode-dark', 'vscode-light', 'utrgv'],
    default: 'omni',
  },
  onboardingComplete: {
    type: 'boolean',
    default: false,
  },
  cliCardDismissed: {
    type: 'boolean',
    default: false,
  },
  remoteBackend: {
    // Discriminated union — see RemoteBackend. Persisted values of removed
    // kinds fail validation here and are wiped by `clearInvalidConfig: true`.
    type: ['object', 'null'],
    default: null,
    anyOf: [
      { type: 'null' },
      {
        type: 'object',
        properties: {
          kind: { const: 'wsl' },
          distro: { type: 'string' },
          port: { type: 'number' },
          persistent: { type: 'boolean' },
        },
        required: ['kind', 'distro', 'port'],
      },
      {
        type: 'object',
        properties: {
          kind: { const: 'server' },
          url: { type: 'string' },
        },
        required: ['kind', 'url'],
      },
    ],
  },
  wipLimit: {
    type: 'number',
    default: 3,
  },
  scheduledTasks: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        name: { type: 'string' },
        description: { type: 'string' },
        instructions: { type: 'string' },
        schedule: { type: 'object' },
        permissionMode: { type: 'string', enum: ['ask'], default: 'ask' },
        enabled: { type: 'boolean' },
        projectId: { type: 'string' },
        profileName: { type: 'string' },
        createdAt: { type: 'number' },
        updatedAt: { type: 'number' },
        nextRunAt: { type: ['number', 'null'] },
        lastRunAt: { type: 'number' },
        runningSessionId: { type: 'string' },
        runningProcessId: { type: 'string' },
        allowedToolNames: { type: 'array', default: [], items: { type: 'string' } },
        allowedMcpTools: {
          type: 'array',
          default: [],
          items: {
            type: 'object',
            properties: {
              serverLabel: { type: 'string' },
              toolName: { type: 'string' },
            },
            required: ['serverLabel', 'toolName'],
          },
        },
        history: { type: 'array', default: [], items: { type: 'object' } },
      },
      required: [
        'id',
        'name',
        'description',
        'instructions',
        'schedule',
        'enabled',
        'createdAt',
        'updatedAt',
        'nextRunAt',
        'history',
      ],
    },
  },
  activityLog: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        at: { type: 'number' },
        kind: { type: 'string', enum: ['routine_run_finished', 'agent_run_finished', 'run_failed'] },
        title: { type: 'string' },
        outcome: { type: 'string' },
        projectId: { type: 'string' },
        link: { type: 'object' },
      },
      required: ['id', 'at', 'kind', 'title', 'link'],
    },
  },
  dismissedHomeHints: {
    type: 'array',
    default: [],
    items: { type: 'string' },
  },
  schemaVersion: {
    type: 'number',
    default: 0,
  },
  codeTabs: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        projectId: { type: ['string', 'null'] },
        ticketId: { type: 'string' },
        sessionId: { type: 'string' },
        ticketTitle: { type: 'string' },
        routineId: { type: 'string' },
        routineName: { type: 'string' },
        routineSchedule: { type: 'string' },
        workspaceDir: { type: 'string' },
        profileName: { type: 'string' },
        sidecarOpen: { type: 'boolean' },
        sidecarAppIds: { type: 'array', items: { type: 'string' } },
        activeSidecarAppId: { type: 'string' },
        spacesWidth: { type: 'number' },
        spacesExpanded: { type: 'boolean' },
        spacesSidecarWidth: { type: 'number' },
        spacesSidecarExpanded: { type: 'boolean' },
        createdAt: { type: 'number' },
        activatedAt: { type: 'number' },
      },
      required: ['id', 'createdAt'],
    },
  },
  chatCleanupJobs: {
    type: 'array',
    default: [],
    items: { type: 'object' },
  },
  chatConversations: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        sessionId: { type: 'string' },
        title: { type: 'string' },
        lastActiveAt: { type: 'number' },
        profileName: { type: 'string' },
        projectId: { type: 'string' },
        ticketId: { type: 'string' },
        ticketTitle: { type: 'string' },
        archivedAt: { type: 'number' },
      },
      required: ['sessionId', 'title', 'lastActiveAt'],
    },
  },
  activeCodeTabId: {
    type: ['string', 'null'],
    default: null,
  },
  codeLayoutMode: {
    type: 'string',
    // 'deck' and 'spaces' are pre-v20 names for 'tile'; kept so existing
    // stores load before the v19→v20 migration runs. Migration converts them.
    enum: ['tile', 'spaces', 'deck', 'focus'],
    // Still 'tile' so a migrated store's absent key keeps meaning the old
    // default; the v27 migration stamps 'focus' explicitly for installs
    // without deck columns (and fresh stores hit that path on first boot).
    default: 'tile',
  },
  activeTicketId: {
    type: ['string', 'null'],
    default: null,
  },
  projects: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        label: { type: 'string' },
        slug: { type: 'string' },
        isPersonal: { type: 'boolean' },
        sources: { type: 'array', default: [] },
        createdAt: { type: 'number' },
        pipeline: { type: 'object' },
        sandboxProfile: { type: ['string', 'null'] },
        dueDate: { type: 'number' },
        pinnedAt: { type: ['number', 'null'] },
        seedKey: { type: 'string' },
      },
      required: ['id', 'label', 'slug', 'createdAt'],
    },
  },
  milestones: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        projectId: { type: 'string' },
        title: { type: 'string' },
        description: { type: 'string' },
        branch: { type: 'string' },
        brief: { type: 'string' },
        status: { type: 'string', enum: ['active', 'completed', 'archived'] },
        dueDate: { type: 'number' },
        completedAt: { type: 'number' },
        pinnedAt: { type: ['number', 'null'] },
        createdAt: { type: 'number' },
        updatedAt: { type: 'number' },
        seedKey: { type: 'string' },
      },
      required: ['id', 'projectId', 'title', 'description', 'status', 'createdAt', 'updatedAt'],
    },
  },
  pages: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        projectId: { type: 'string' },
        parentId: { type: ['string', 'null'] },
        title: { type: 'string' },
        icon: { type: 'string' },
        sortOrder: { type: 'number' },
        isRoot: { type: 'boolean' },
        createdAt: { type: 'number' },
        updatedAt: { type: 'number' },
        seedKey: { type: 'string' },
      },
      required: ['id', 'projectId', 'title', 'sortOrder', 'createdAt', 'updatedAt'],
    },
  },
  inboxItems: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        title: { type: 'string' },
        note: { type: 'string' },
        attachments: { type: 'array', items: { type: 'string' } },
        projectId: { type: ['string', 'null'] },
        status: { type: 'string', enum: ['new', 'later'] },
        laterAt: { type: 'number' },
        promotedTo: {
          type: 'object',
          properties: {
            kind: { type: 'string', enum: ['ticket', 'project'] },
            id: { type: 'string' },
            at: { type: 'number' },
          },
          required: ['kind', 'id', 'at'],
        },
        createdAt: { type: 'number' },
        updatedAt: { type: 'number' },
        seedKey: { type: 'string' },
      },
      required: ['id', 'title', 'status', 'createdAt', 'updatedAt'],
    },
  },
  tasks: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        projectId: { type: 'string' },
        taskDescription: { type: 'string' },
        status: { type: 'object' },
        createdAt: { type: 'number' },
        branch: { type: 'string' },
        worktreePath: { type: 'string' },
        worktreeName: { type: 'string' },
        sessionId: { type: 'string' },
        ticketId: { type: 'string' },
        phaseId: { type: 'string' },
        columnId: { type: 'string' },
        iteration: { type: 'number' },
      },
      required: ['id', 'projectId', 'taskDescription', 'status', 'createdAt'],
    },
  },
  tickets: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        projectId: { type: 'string' },
        milestoneId: { type: 'string' },
        title: { type: 'string' },
        description: { type: 'string' },
        priority: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] },
        blockedBy: { type: 'array', items: { type: 'string' } },
        createdAt: { type: 'number' },
        updatedAt: { type: 'number' },
        phaseChangedAt: { type: 'number' },
        columnChangedAt: { type: 'number' },
        completedAt: { type: 'number' },
        archivedAt: { type: 'number' },
        cleanupPending: { type: 'boolean' },
        // Per-source map keyed by ProjectSource.id (last sync-to-host timestamps).
        prMergedAt: { type: 'object' },
        assignee: { type: 'string' },
        // Kanban fields
        columnId: { type: 'string' },
        currentPhaseId: { type: ['string', 'null'] },
        phases: { type: 'array', default: [] },
        // Legacy fields (kept for migration)
        taskId: { type: 'string' },
        loopEnabled: { type: 'boolean' },
        loopMaxIterations: { type: 'number' },
        loopIteration: { type: 'number' },
        loopStatus: { type: 'string', enum: ['running', 'completed', 'stopped', 'error'] },
        seedKey: { type: 'string' },
      },
      required: ['id', 'projectId', 'title', 'description', 'priority', 'blockedBy', 'createdAt', 'updatedAt'],
    },
  },
  skillSources: {
    type: 'object',
    additionalProperties: { type: 'object' },
    default: {},
  },
  installedBundles: {
    type: 'object',
    additionalProperties: { type: 'object' },
    default: {},
  },
  gitCredentials: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        host: { type: 'string' },
        username: { type: 'string' },
        last4: { type: 'string' },
        label: { type: 'string' },
        createdAt: { type: 'number' },
      },
      required: ['id', 'host', 'username', 'last4', 'createdAt'],
    },
  },
  pullRequestLinks: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        url: { type: 'string' },
        number: { type: 'number' },
        state: { type: 'string' },
        projectId: { type: 'string' },
        sourceId: { type: 'string' },
        sourceMountName: { type: 'string' },
        provider: { type: 'string' },
        branch: { type: 'string' },
        title: { type: 'string' },
        ticketId: { type: 'string' },
        codeTabId: { type: 'string' },
        sessionId: { type: 'string' },
        workspaceDir: { type: 'string' },
        createdAt: { type: 'number' },
        lastSeenAt: { type: 'number' },
      },
      required: ['url', 'number', 'state', 'createdAt', 'lastSeenAt'],
    },
  },
  modelsConfig: {
    type: 'object',
    default: { version: 3, default: null, voice_default: null, providers: {} },
  },
  mcpConfig: { type: 'object', default: { mcpServers: {} } },
  networkConfig: {
    type: 'object',
    default: {
      enabled: false,
      presets: [],
      allowlist: [],
    },
  },
  envVars: { type: 'string', default: '' },
  agentConfigMigratedFromFiles: { type: 'boolean', default: false },
  mcpConfigOwnership: { type: 'string', enum: ['omniagents'] },
  codexAccountOwnership: { type: 'string', enum: ['omniagents'] },
  audioSettings: {
    type: 'object',
    default: {
      inputDeviceId: null,
      outputDeviceId: null,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    properties: {
      inputDeviceId: { type: ['string', 'null'] },
      outputDeviceId: { type: ['string', 'null'] },
      echoCancellation: { type: 'boolean' },
      noiseSuppression: { type: 'boolean' },
      autoGainControl: { type: 'boolean' },
    },
    required: ['inputDeviceId', 'outputDeviceId', 'echoCancellation', 'noiseSuppression', 'autoGainControl'],
  },
  browserProfiles: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        label: { type: 'string' },
        partition: { type: 'string' },
        builtin: { type: 'boolean' },
        incognito: { type: 'boolean' },
        createdAt: { type: 'number' },
      },
      required: ['id', 'label', 'partition', 'createdAt'],
    },
  },
  browserTabsets: {
    type: 'object',
    default: {},
    additionalProperties: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        profileId: { type: 'string' },
        tabs: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              id: { type: 'string' },
              url: { type: 'string' },
              title: { type: 'string' },
              favicon: { type: 'string' },
              profileId: { type: 'string' },
              pinned: { type: 'boolean' },
              createdAt: { type: 'number' },
              lastActiveAt: { type: 'number' },
            },
            required: ['id', 'url', 'createdAt', 'lastActiveAt'],
          },
        },
        activeTabId: { type: ['string', 'null'] },
        createdAt: { type: 'number' },
        updatedAt: { type: 'number' },
      },
      required: ['id', 'profileId', 'tabs', 'activeTabId', 'createdAt', 'updatedAt'],
    },
  },
  browserHistory: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        url: { type: 'string' },
        title: { type: 'string' },
        profileId: { type: 'string' },
        visitedAt: { type: 'number' },
      },
      required: ['id', 'url', 'profileId', 'visitedAt'],
    },
  },
  browserBookmarks: {
    type: 'array',
    default: [],
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        url: { type: 'string' },
        title: { type: 'string' },
        folder: { type: 'string' },
        createdAt: { type: 'number' },
      },
      required: ['id', 'url', 'title', 'createdAt'],
    },
  },
};

/**
 * The type of GPU in the system. This and the operating system are used to determine:
 * - Whether to install xformers - torch's own SDP is faster for 30xx + series GPUs, otherwise xformers is faster.
 * - Which pypi indices to use for torch.
 */
export type GpuType = 'nvidia<30xx' | 'nvidia>=30xx' | 'amd' | 'nogpu';

/**
 * A map of GPU types to human-readable names.
 */
export const GPU_TYPE_MAP: Record<GpuType, string> = {
  'nvidia<30xx': 'Nvidia (20xx and below)',
  'nvidia>=30xx': 'Nvidia (30xx and above)',
  amd: 'AMD',
  nogpu: 'No dedicated GPU',
};

/**
 * Supported operating systems.
 */
export type OperatingSystem = 'Windows' | 'macOS' | 'Linux';

/**
 * A utility type that prefixes all keys in an object with a string using the specified separator.
 */
type Namespaced<Prefix extends string, T, Sep extends string = ':'> = {
  [K in keyof T as `${Prefix}${Sep}${string & K}`]: T[K];
};

/**
 * A status object that may optionally contain data. It represents an OK/good status.
 */
type OkStatus<StatusType extends string, Data = void> = Data extends void
  ? {
      type: StatusType;
    }
  : {
      type: StatusType;
      data: Data;
    };

/**
 * A status object that contains an error message and optionally some context. It represents an ERROR/bad status.
 */
type ErrorStatus = {
  type: 'error';
  error: {
    message: string;
    context?: Record<string, unknown>;
  };
};

/**
 * A status object that may be either an OK status or an ERROR status.
 */
type Status<State extends string> = OkStatus<State> | ErrorStatus;

/**
 * The various states the main process can be in.
 */
export type MainProcessStatus = Status<'initializing' | 'idle' | 'exiting'>;

export type OmniInstallProcessStatus = Status<
  'uninitialized' | 'starting' | 'installing' | 'canceling' | 'exiting' | 'completed' | 'canceled'
>;

/** Connection metadata for an embedded OmniAgents client. The base URL names
 *  the RPC server origin; it is not a document or iframe source. */
export type AgentRuntimeConnection = {
  baseUrl: string;
  authToken?: string;
};

/**
 * Complete address of a materialized AgentHost execution environment.
 *
 * The generation is part of the address: re-materializing an environment can
 * reuse its id with a newer generation, and Omniagents rejects stale callers
 * when this value is supplied. Workspace RPCs must carry all three fields.
 */
export type ExecutionTarget = {
  workspaceId: string;
  environmentId: string;
  environmentGeneration: number;
};

/** Result of retiring one execution environment or its owning AgentHost. */
export type AgentProcessStopResult = {
  scope: 'environment' | 'host' | 'compute' | 'none';
  /** Host-process termination mode. Environment-only stops do not terminate it. */
  shutdown: 'graceful' | 'forced' | 'not-applicable';
};

/** One registered source's landing spot inside a materialized environment. */
export type WorkspaceMountDescriptor = {
  name: string;
  /** Location relative to ``workspaceRoot`` (``'.'`` = the root itself). */
  path: string;
  writable: boolean;
  kind?: string;
};

// Unified agent process data — emitted by `omni serve` and the platform path.
export type AgentProcessData = {
  /** Base URL the renderer parses for WS + HTTP endpoints. */
  uiUrl: string;
  /** Direct WS URL for JSON-RPC; renderer derives from uiUrl if unset. */
  wsUrl?: string;
  /** Agent-host identity. Required once either local or platform readiness completes. */
  agentHostId?: string;
  /** Workspace resource bound to ``environmentId`` by the agent host. */
  workspaceId?: string;
  /**
   * Execution-environment address for filesystem, git, terminal, and other
   * tool operations. Local ``omni serve`` always supplies it; it is kept
   * separate from the conversation ``sessionId`` by design.
   */
  environmentId?: string;
  /** Generation paired with ``environmentId`` by AgentHost materialization. */
  environmentGeneration?: number;
  /**
   * Capability names from the materialized environment descriptor
   * (`pty`, `network`, `contained`, ...). The renderer gates
   * capability-dependent surfaces on these affirmatively — the Terminal
   * app renders only when `pty` is present, so no attached environment
   * (or a wasm sandbox, which has no PTY) shows no Terminal at all.
   */
  environmentCapabilities?: string[];
  /** Authoritative root returned by the materialized execution environment. */
  workspaceRoot?: string;
  /** Environment-selected initial cwd for terminals and agent commands. */
  defaultCwd?: string;
  /**
   * Authoritative source mount table reported by materialization: where
   * every registered source landed relative to ``workspaceRoot``. The
   * workspace surfaces (Files/Git/Review) derive their scope from this —
   * never from ``defaultCwd`` or path heuristics.
   */
  mounts?: WorkspaceMountDescriptor[];
  /**
   * Bearer token for authenticating WS/HTTP calls against this agent's
   * server. Main-process dials send it as an `Authorization: Bearer`
   * upgrade header; browser contexts exchange it for a one-time
   * `/auth/ws-ticket` dial ticket. Never appended to a URL — the omniagents
   * server rejects query-string tokens (rpc/protocol.md, Authentication).
   * Unset when the server runs unauthenticated (local `omni serve`).
   */
  authToken?: string;
  /** Same shape as uiUrl today — kept for status/debug surfaces. */
  sandboxUrl?: string;
  /**
   * Named services running inside the sandbox session, keyed by profile
   * service name (`code_server`, `vnc`, ...). Each value is a host URL the
   * renderer can iframe directly. Empty when the profile defines no services.
   */
  services?: Record<string, string>;
  /** Container id when the backend is docker; absent for unix_local/platform. */
  containerId?: string;
  /** Container name when the backend is docker. */
  containerName?: string;
  /** Host port the agent's WS/HTTP server is bound to. */
  port?: number;
  /**
   * True when the sandbox container is currently frozen via docker pause
   * (cgroup freezer). RAM is held; CPU is zero; tool calls into the
   * container hang until unpause. The renderer uses this to show a
   * "paused" badge and to trigger ``sandbox.unpause`` on activity.
   * Unset / false when the backend doesn't support pause or when we
   * never asked.
   */
  paused?: boolean;
  /**
   * Transitional UI state while a profile replacement is being prepared.
   */
  switching?: boolean;
};

export type AgentProcessStatus =
  | Status<'uninitialized' | 'starting' | 'stopping' | 'exiting' | 'exited'>
  | OkStatus<'connecting', AgentProcessData>
  | OkStatus<'running', AgentProcessData>;

/**
 * Start argument for the unified agent process manager.
 */
export type AgentProcessStartOptions = {
  workspaceDir: string;
  /**
   * Explicit replacement for the project's primary local source (for example
   * a ticket worktree). Unlike ``workspaceDir`` this changes the registered
   * Workspace source set; it is never just a renderer cwd hint.
   */
  sourceOverrideDir?: string;
  /**
   * Project id, when this consumer belongs to a project. AgentHost uses it
   * while registering the consumer's default profile definition.
   */
  projectId?: string;
  /**
   * Per-launch profile override. Wins over per-project ``sandboxProfile``
   * and the user's ``defaultProfileName``. Set by the pre-launch sandbox
   * picker; not persisted — a follow-up launch without this set falls
   * back through the normal resolution chain.
   */
  profileNameOverride?: string;
  /**
   * Conversation session id used for delegated-compute session ownership.
   * Local AgentHost Workspace identity is deliberately independent.
   */
  sessionId?: string;
  /** Durable Workspace snapshot reference, independent of conversation id. */
  snapshotRef?: string;
};

/**
 * A logging level.
 */
type LogLevel = 'debug' | 'info' | 'warn' | 'error';

/**
 * A log entry with a level and message.
 */
export type LogEntry = {
  level: LogLevel;
  message: string;
};

/**
 * A type that adds a timestamp to an object.
 */
export type WithTimestamp<T> = T & { timestamp: number };

export type OmniRuntimeInfo =
  | {
      isInstalled: false;
    }
  | {
      isInstalled: true;
      version: string;
      expectedVersion: string;
      isOutdated: boolean;
      pythonVersion?: string;
      omniPath: string;
    };

// #region Code Tab types

export type CodeTabId = string;

export type CodeLayoutMode = 'tile' | 'focus';

export type CodeTab = {
  /** Backend-owned runtime launcher identity; retained by removal jobs. */
  runtimeOwner?: string;
  id: CodeTabId;
  projectId: ProjectId | null;
  ticketId?: TicketId;
  sessionId?: string;
  /** Durable Workspace snapshot identity; never used for chat/RPC routing. */
  snapshotRef?: string;
  ticketTitle?: string;
  routineId?: string;
  routineName?: string;
  routineSchedule?: string;
  workspaceDir?: string;
  /** Whether this session's right sidecar is visible. Open tabs remain mounted while hidden. */
  sidecarOpen?: boolean;
  /** Column-scoped apps currently open as tabs in this session's right sidecar. */
  sidecarAppIds?: string[];
  /** Selected app in the right sidecar. Must also be present in `sidecarAppIds`. */
  activeSidecarAppId?: string;
  /** User-selected width for this column in Spaces. */
  spacesWidth?: number;
  /** Whether this column uses the expanded Spaces width preset. */
  spacesExpanded?: boolean;
  /** User-selected width for this session's sidecar column in Spaces. */
  spacesSidecarWidth?: number;
  /** Whether this session's sidecar uses the expanded Spaces width preset. */
  spacesSidecarExpanded?: boolean;
  /**
   * Sticky profile binding for this tab. Snapshotted from the resolution chain
   * (per-project ``sandboxProfile`` → user default) when the tab is created,
   * then persisted so a later default change doesn't silently drift the
   * workspace into another sandbox.
   */
  profileName?: string;
  profileNameExplicit?: boolean;
  createdAt: number;
  /**
   * When the user first expressed intent in this column (first message sent
   * or project attached). Chat-mode columns (projectless, no routine, no
   * custom app) don't launch their sandbox until this is set — creating a
   * column is free; the boot happens on intent. Project/routine columns
   * ignore it (they launch eagerly, as before).
   */
  activatedAt?: number;
};

/**
 * A chat-mode column: a projectless agent session running in a
 * per-conversation scratch workspace (``<workspace>/Sessions/<sessionId>``)
 * with ``surface: 'chat'`` variables. Purely derived — any tab the user
 * hasn't bound to a project, routine, or app is a chat column.
 */
export const isChatColumn = (tab: Pick<CodeTab, 'projectId' | 'routineId'>): boolean =>
  !tab.projectId && !tab.routineId;

/** An indexed agent conversation, resumable from the sidebar. */
export type ChatConversation = {
  /** Scratch-directory key and agent-server conversation id. */
  sessionId: string;
  /** First user message (truncated) — display label in the sidebar. */
  title: string;
  lastActiveAt: number;
  /** Sticky profile of the column that owned it, for faithful resume. */
  profileName?: string;
  /** Project grouping retained independently of an active column. */
  projectId?: ProjectId;
  /** Ticket context retained when the session originated from a ticket. */
  ticketId?: TicketId;
  ticketTitle?: string;
  /** Hidden from normal history until restored. */
  archivedAt?: number;
};

// #endregion

// #region Project & Ticket types

// --- ID types ---

export type ProjectId = string;

/**
 * Minimal pointer the launcher persists so it knows where each project's
 * file-backed data lives. All other project attributes are loaded from
 * `<dir>/.omni/project.yml` at runtime.
 */
export type ProjectIndexEntry = { id: ProjectId; dir: string };

export type MilestoneId = string;
export type PageId = string;
export type InboxItemId = string;
export type TaskId = string;
export type TicketId = string;
export type TicketCommentId = string;
export type ColumnId = string;

// --- Enums ---

export type TicketPriority = 'low' | 'medium' | 'high' | 'critical';
export type MilestoneStatus = 'active' | 'completed' | 'archived';

/** Re-export TicketPhase so renderer can import from shared/types. */
export type { TicketPhase } from '@/shared/ticket-phase';

/** Accumulated token usage for a supervisor session. */
export type TokenUsage = {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
};

// --- Pipeline & columns ---

/**
 * Human-facing workflow status. The column graph stays the agent's custom
 * state machine; the category is the universal state global views group by.
 * Valid pipelines read `todo* doing* done` in column order.
 */
export type ColumnCategory = 'todo' | 'doing' | 'done';

/**
 * A single column in the kanban pipeline. Visual milestones for the supervisor.
 */
export type Column = {
  id: ColumnId;
  label: string;
  /** Human-readable description of what this column represents. */
  description?: string;
  /** Max concurrent supervisors allowed in this column. Unlimited if undefined. */
  maxConcurrent?: number;
  /** When true, the supervisor is stopped on entry and only a human can move the ticket out. */
  gate?: boolean;
  /** DB-backed workflow metadata used by humans, agents, and project tools. */
  workflow?: ColumnWorkflowContract;
  /**
   * Status category. Optional only because legacy in-store pipelines predate
   * it — readers resolve through `categoryOf()` in `@/lib/pipeline-category`,
   * which falls back to the positional rule (first=todo, last=done).
   */
  category?: ColumnCategory;
};

export type ColumnWorkflowContract = {
  purpose?: string;
  entryCriteria?: string[];
  definitionOfDone?: string[];
  agentInstructions?: string;
  recommendedSkills?: string[];
  allowedTransitions?: ColumnId[];
  autoDispatch?: boolean;
};

/**
 * The pipeline definition for a project. Ordered list of columns.
 */
export type Pipeline = {
  columns: Column[];
};

// --- Core entities ---

/**
 * A stored git credential, keyed by host. Credentials are host-scoped and
 * resolved implicitly: a `git-remote` source authenticates with the entry whose
 * `host` matches the source URL's host, so a token is entered once per host and
 * reused across every project. This object holds *metadata only* — the token
 * bytes live in the main/server `SecretStore` (keyed by `id`) and never enter
 * the renderer or the `store:changed` snapshot. See `git-credentials.ts`.
 */
export type GitCredential = {
  id: string;
  /** Bare host the token authenticates, e.g. `github.com`, `gitlab.example.org`. */
  host: string;
  /**
   * HTTPS basic-auth username paired with the token. GitHub PATs use
   * `x-access-token`; GitLab uses `oauth2`. Editable for other hosts.
   */
  username: string;
  /** Last 4 chars of the token, for display only. Never the token itself. */
  last4: string;
  /** Optional human label shown in the credential list. */
  label?: string;
  createdAt: number;
};

/**
 * Linked GitHub account metadata (display only — the OAuth token lives in the
 * `SecretStore` as the `github.com` [[GitCredential]]). Connecting an account
 * both authenticates clone/push and unlocks repo discovery. Safe to broadcast
 * to the renderer.
 */
export type GithubAccount = {
  login: string;
  avatarUrl?: string;
  /** OAuth scopes granted on the stored token (e.g. `repo`, `read:org`). */
  scopes?: string[];
  /** API host the account is on — `github.com` or a GitHub Enterprise host. */
  host: string;
  connectedAt: number;
};

/** Connection status for the linked GitHub account. */
export type GithubStatus = { connected: boolean; account?: GithubAccount };

/** An account that owns repositories — the linked user, or an org they belong
 *  to. Drives the owner selector in the repo picker. */
export type GithubOwner = { login: string; kind: 'user' | 'org'; avatarUrl?: string };

/** A scoped repo query: search `query` within one owner. Empty `query` returns
 *  that owner's most-recently-pushed repos. */
export type GithubRepoQuery = { owner: string; kind: 'user' | 'org'; query: string };

/** A repository discovered via a linked provider (GitHub, Azure DevOps, …), for
 *  the source picker. Provider-agnostic. */
export type RemoteRepo = {
  /** `owner/name` (GitHub) or `project/repo` (Azure DevOps). */
  fullName: string;
  /** HTTPS clone URL. */
  cloneUrl: string;
  defaultBranch: string;
  private: boolean;
  /** Last push time (epoch ms) — drives most-recent-first ordering. */
  pushedAt?: number;
};

/**
 * Device-flow user code, pushed to the renderer mid-link so the connect UI can
 * display the code the user must enter at `verificationUri`.
 */
export type GithubDeviceCode = { userCode: string; verificationUri: string; expiresIn: number };

/**
 * One repo/directory attached to a project. Projects can have any number
 * of sources (zero, one, or many) — a cross-cutting ticket like "ship a
 * change across launcher + omni-code + omniagents" attaches three sources
 * to one project so the agent sees all three at once in /workspace.
 *
 * - `id`: stable identifier (nanoid). Session sync and optional PR links key
 *   off this so renaming the user-facing ``mountName`` doesn't orphan
 *   per-source state.
 * - `mountName`: subdirectory under ``/workspace/`` inside the container
 *   (e.g. ``launcher``). Defaults to a slug derived from path or repo
 *   name; must be unique within a project.
 * - `kind`: `local` (host directory) or `git-remote` (URL the container
 *   clones; private repos authenticate with the host-matched
 *   [[GitCredential]], resolved at clone time — never stored on the source).
 */
export type ProjectSource = (
  | { kind: 'local'; workspaceDir: string; gitDetected?: boolean }
  | { kind: 'git-remote'; repoUrl: string; defaultBranch?: string }
) & {
  id: string;
  mountName: string;
  /** When true, filesystem tools must not mutate this source. Missing means writable. */
  readOnly?: boolean;
};

/**
 * Single-source ergonomic accessor: the first source attached to a project,
 * or undefined if none. Use this for callers that conceptually want "the
 * project's repo" — the launcher's legacy code paths and the MCP API
 * (which doesn't model multi-source).
 *
 * Multi-source-aware callers (PR UI, container seeding, per-source merge)
 * iterate ``project.sources`` directly.
 */
export const firstSource = (project: { sources?: ProjectSource[] } | undefined | null): ProjectSource | undefined =>
  project?.sources?.[0];

/**
 * True when a source is a curated version-controlled tree — membership rules
 * apply (not every file belongs in it), so agent output that isn't part of the
 * change routes to the artifacts channel instead of the tree.
 */
export const isRepoSource = (s: ProjectSource): boolean =>
  s.kind === 'git-remote' || (s.kind === 'local' && s.gitDetected === true);

/**
 * True when any of the project's sources is version-controlled. Gates the
 * developer-only output surfaces (Artifacts panel, PR affordances) — plain
 * folder projects have exactly one output concept: the folder itself.
 */
export const projectHasRepoSource = (project: { sources?: ProjectSource[] } | undefined | null): boolean =>
  (project?.sources ?? []).some(isRepoSource);

export type Project = {
  id: ProjectId;
  label: string;
  /** Filesystem-safe name derived from label, used for project folder path. */
  slug: string;
  /** True for the auto-created Personal project. Cannot be deleted. */
  isPersonal?: boolean;
  /**
   * Repos/directories attached to this project. Empty array = no source.
   * The container exposes each source under ``/workspace/<mountName>``.
   */
  sources: ProjectSource[];
  createdAt: number;
  /** Pipeline configuration. If undefined, DEFAULT_PIPELINE is used. */
  pipeline?: Pipeline;
  /** When true, automatically dispatch tickets from backlog in priority order. */
  autoDispatch?: boolean;
  /**
   * Per-project sandbox profile name. Overrides ``defaultProfileName`` for
   * this project. ``null``/missing inherits the user-default.
   */
  sandboxProfile?: string | null;
  /** Optional target date (epoch ms). Drives deadline-pressure risk signals. */
  dueDate?: number;
  /** Timestamp of the most recent pin action. Set = pinned to Home; undefined = not pinned. */
  /** Pinned to This Week. `null` = explicit unpin (undefined doesn't survive the WS JSON transport). */
  pinnedAt?: number | null;
  /** Populated by the seed script; tracked in seed-manifest for reset. */
  seedKey?: string;
};

export type Milestone = {
  id: MilestoneId;
  projectId: ProjectId;
  title: string;
  description: string;
  /** Optional feature branch. Tickets inherit this unless they override with their own branch. */
  branch?: string;
  /** Milestone brief — describes the deliverable, goals, and scope. */
  brief?: string;
  status: MilestoneStatus;
  /** Optional target date (epoch ms). Drives deadline-pressure risk signals. */
  dueDate?: number;
  /** Stamped when status transitions to 'completed'. */
  completedAt?: number;
  /** Timestamp of the most recent pin action. Set = pinned to Home; undefined = not pinned. */
  /** Pinned to This Week. `null` = explicit unpin (undefined doesn't survive the WS JSON transport). */
  pinnedAt?: number | null;
  createdAt: number;
  updatedAt: number;
  /** Populated by the seed script; tracked in seed-manifest for reset. */
  seedKey?: string;
};

/**
 * A knowledge-base page. Notion-style: hierarchical doc with a title, icon,
 * and markdown body (stored on disk, not in the store). Pages have no
 * lifecycle status — they are docs, not work items. Work happens on tickets.
 */
export type PageProperties = {
  status?: string;
  size?: string;
  projectId?: ProjectId;
  milestoneId?: MilestoneId;
  outcome?: string;
  notDoing?: string;
  laterAt?: number;
};

export type Page = {
  id: PageId;
  projectId: ProjectId;
  /** Null for root-level pages in the project. */
  parentId: PageId | null;
  title: string;
  /** Emoji for sidebar display. */
  icon?: string;
  /** Sort position among siblings. Lower = first. */
  sortOrder: number;
  /** True for the auto-created root page. Cannot be deleted. */
  isRoot?: boolean;
  /** Structured metadata fields (status, size, outcome, etc.) for this page. */
  properties?: PageProperties;
  createdAt: number;
  updatedAt: number;
  /** Populated by the seed script; tracked in seed-manifest for reset. */
  seedKey?: string;
};

// --- Inbox ---

/**
 * Inbox lifecycle:
 * - new:   captured, waiting to be promoted or deferred
 * - later: deferred; `laterAt` drives stale-item reminders
 *
 * Terminal transition is promotion: the item is stamped with `promotedTo`
 * pointing at the ticket or project it became, and is filtered out of the
 * active inbox view. Promoted items stay as tombstones for undo + audit
 * (GC'd after 30d by the inbox manager).
 *
 * (A legacy 'shaped' status existed before v25 — migrated to 'new' on boot,
 * with its structured shaping block folded into the item note.)
 */
export type InboxItemStatus = 'new' | 'later';

export type InboxPromotion = {
  kind: 'ticket' | 'project';
  /** ID of the ticket or project this inbox item became. */
  id: string;
  /** Timestamp of promotion, used for tombstone GC. */
  at: number;
};

export type InboxItem = {
  id: InboxItemId;
  title: string;
  /** Free-form capture body. */
  note?: string;
  attachments?: string[];
  /** Optional project context at capture time. Null = loose / global. */
  projectId?: ProjectId | null;
  status: InboxItemStatus;
  /** Stamped when moved to `later`. Drives the stale-items reminder. */
  laterAt?: number;
  /** Stamped on promotion. Presence hides the item from the active view. */
  promotedTo?: InboxPromotion;
  createdAt: number;
  updatedAt: number;
  /** Populated by the seed script; tracked in seed-manifest for reset. */
  seedKey?: string;
};

export type TicketComment = {
  id: TicketCommentId;
  author: 'agent' | 'human';
  content: string;
  createdAt: number;
};

export type TicketRun = {
  id: string;
  startedAt: number;
  endedAt: number;
  endReason: string;
  tokenUsage?: TokenUsage;
};

/**
 * One detected pull request, remembered so the UI can tell "we watched this PR
 * while it was open" from "historical PR detection re-reports forever". Two
 * homes: ticket-backed links live on `Ticket.pullRequests` (DB `pr_review`
 * column); links for ticket-less scopes (chat conversations, un-ticketed code
 * tabs) live in the global `StoreData.pullRequestLinks` list, scoped by
 * `sessionId`/`codeTabId`. Project/source fields are absent for chat-scratch
 * links — a chat conversation has no project or `ProjectSource`.
 */
export type PullRequestLink = {
  url: string;
  number: number;
  state: string;
  projectId?: ProjectId;
  sourceId?: string;
  sourceMountName?: string;
  provider?: 'github' | 'azure';
  branch?: string;
  title?: string;
  ticketId?: TicketId;
  codeTabId?: CodeTabId;
  sessionId?: string;
  workspaceDir?: string;
  createdAt: number;
  lastSeenAt: number;
  /** Newest review timestamp already delivered (or covered at first sight) by
   *  the pull-request watcher — its durable review cursor, so reviews are
   *  never re-announced across restarts. */
  reviewWatermarkAt?: number;
  /** The watcher's CI announce cursor: the last settled check verdict and the
   *  head commit it was computed from (always persisted together — `failing`
   *  is meaningless without knowing for which commit). */
  ciState?: 'failing' | 'green';
  ciSha?: string;
};

/**
 * One step of an omniagents plan snapshot — the `tasks_snapshot` summary
 * shape (`to_summaries()` in omniagents' plans.py). Persisted per ticket as
 * `last_plan_snapshot` so a relaunched session can be prompted to recreate
 * its plan (docs/agentic-workflow-enforcement-plan.md §F). The renderer's
 * `TaskSummary` (omniagents-ui/canonical-plan-tasks.ts) is the same wire
 * shape minus the review fields.
 */
export type PlanSnapshotEntry = {
  id: string;
  subject: string;
  activeForm?: string;
  status: 'pending' | 'in_progress' | 'completed' | 'blocked';
  /** Owning principal ('agent:<id>' for agents; anything else is a human gate). */
  owner?: string;
  blockedBy?: string[];
  /** Checkable definition-of-done. Empty/absent = no semantic review. */
  exitCriteria?: string;
  /** Completion review: true verified, false unverified, null/absent unreviewed. */
  verified?: boolean | null;
  /** True when the criteria were edited while the step was in progress (ratchet stamp). */
  criteriaEdited?: boolean;
};

export type Ticket = {
  id: TicketId;
  projectId: ProjectId;
  milestoneId?: MilestoneId;
  title: string;
  description: string;
  priority: TicketPriority;
  blockedBy: TicketId[];
  createdAt: number;
  updatedAt: number;

  // Kanban state
  /** Current column in the kanban pipeline. */
  columnId: ColumnId;

  // Git settings
  /** Git branch to work on. If set with useWorktree, a worktree is created from this branch. */
  branch?: string;
  /** Whether to create an isolated git worktree for this ticket's sandbox. */
  useWorktree?: boolean;

  // Worktree state (persisted so worktrees survive server restarts)
  /** Path to the git worktree on disk. */
  worktreePath?: string;
  /** Name of the git worktree (used for branch naming). */
  worktreeName?: string;
  /**
   * Set when the ticket was moved to the terminal column but the worktree
   * had uncommitted changes, so cleanup was deferred. The worktree + sandbox
   * stay alive until the user explicitly finalizes via `project:finalize-ticket-cleanup`.
   */
  cleanupPending?: boolean;

  // --- Sync to host (per source) ---
  /**
   * Stamped per source when that source's container changes were last applied
   * to its host working copy ("synced"). Keyed by ``ProjectSource.id``.
   * (Legacy DB column name: ``pr_merged_at``.)
   */
  prMergedAt?: Record<string, number>;

  // Supervisor state
  /** True when autopilot is driving this ticket. Flipped by start/stopSupervisor. */
  autopilot?: boolean;
  /** Current supervisor lifecycle phase. */
  phase?: import('@/shared/ticket-phase').TicketPhase;
  /** Stamped whenever `phase` transitions. Drives stalled-ticket risk. */
  phaseChangedAt?: number;
  /** Stamped whenever `columnId` changes. Drives aging-in-column risk. */
  columnChangedAt?: number;
  /** Stamped when the task enters a Done-category column; cleared when reopened. */
  completedAt?: number;
  /** Task ID for the supervisor's sandbox. */
  supervisorTaskId?: TaskId;
  /** Accumulated token usage across all supervisor runs. */
  tokenUsage?: TokenUsage;
  /** Timestamp when the ticket was archived from active views. */
  archivedAt?: number;
  /** Agent/human comments — serves as persistent memory across runs. */
  comments?: TicketComment[];
  /**
   * Assigned principal id (a resident `agent:<id>`). Undefined = unassigned.
   */
  assignee?: string;
  /** History of supervisor runs on this ticket. */
  runs?: TicketRun[];
  /**
   * Latest plan snapshot from the ticket's agent session (`tasks_snapshot`).
   * Seeds the supervisor prompt's "Prior plan" section on relaunch and the
   * kanban card's step fraction; cleared when the ticket settles into a
   * Done-category column. Absent = no prior plan.
   */
  lastPlanSnapshot?: PlanSnapshotEntry[];
  pullRequests?: PullRequestLink[];
  /** Populated by the seed script; tracked in seed-manifest for reset. */
  seedKey?: string;
};

export type Task = {
  id: TaskId;
  projectId: ProjectId;
  taskDescription: string;
  status: WithTimestamp<AgentProcessStatus>;
  createdAt: number;
  branch?: string;
  worktreePath?: string;
  worktreeName?: string;
  sessionId?: string;
  ticketId?: TicketId;
  /** Snapshot of sandbox URLs from the last 'running' state, for replaying past sessions. */
  lastUrls?: {
    uiUrl: string;
    codeServerUrl?: string;
    noVncUrl?: string;
  };
};

export type ArtifactFileEntry = {
  relativePath: string;
  name: string;
  isDirectory: boolean;
  size: number;
  modifiedAt: number;
};

export type ArtifactFileContent = {
  relativePath: string;
  mimeType: string;
  textContent: string | null;
  size: number;
};

export type SessionMessage = {
  id: number;
  role: 'user' | 'assistant' | 'tool_call' | 'tool_result';
  content: string;
  toolName?: string;
  createdAt: string;
};

export type GitRepoInfo = { isGitRepo: true; branches: string[]; currentBranch: string } | { isGitRepo: false };

export type PrMergeResult = { ok: true; mergeCommitSha: string } | { ok: false; error: string };

/**
 * A GitHub pull request detected for one source's branch by running
 * ``gh pr view`` inside the running container (where the agent pushed the
 * branch and ``gh`` is authenticated). Absent (null) when the source has no
 * open PR — e.g. a plain local directory, a git repo with no remote, or a
 * branch the agent hasn't opened a PR for yet.
 */
export interface ContainerPullRequest {
  number: number;
  url: string;
  /** Normalized across providers: Azure `active` → OPEN, `completed` → MERGED. */
  state: 'OPEN' | 'MERGED';
  title?: string;
  sourceId?: string;
  sourceMountName?: string;
  projectId?: ProjectId;
  ticketId?: TicketId;
  codeTabId?: CodeTabId;
  sessionId?: string;
  workspaceDir?: string;
  provider?: 'github' | 'azure';
  branch?: string;
}

// #endregion

/**
 * Store API. Main process handles these events, renderer process invokes them.
 */
type StoreIpcEvents = Namespaced<
  'store',
  {
    'chat-command': (command: import('./chat-commands').ChatCommand) => import('./chat-commands').ChatCommandResult;
    'get-key': <K extends keyof StoreData>(key: K) => StoreData[K];
    'set-key': <K extends keyof StoreData>(key: K, val: StoreData[K]) => void;
    get: () => StoreData;
    set: (data: StoreData) => void;
    reset: () => void;
  }
>;

/**
 * Main Process API. Main process handles these events, renderer process invokes them.
 */
type MainProcessIpcEvents = Namespaced<
  'main-process',
  {
    'get-status': () => WithTimestamp<MainProcessStatus>;
    exit: () => void;
  }
>;

type OmniInstallProcessIpcEvents = Namespaced<
  'omni-install-process',
  {
    'get-status': () => WithTimestamp<OmniInstallProcessStatus>;
    'start-install': (repair?: boolean) => void;
    'cancel-install': () => void;
    resize: (cols: number, rows: number) => void;
  }
>;

/** Local voice (Option A) — STT/TTS run launcher-side via the ONNX sidecar. */
export interface VoiceStatus {
  state: 'idle' | 'provisioning' | 'starting' | 'ready' | 'error';
  stt: boolean;
  tts: boolean;
  sampleRate: number | null;
  error?: string;
}

type VoiceIpcEvents = Namespaced<
  'voice',
  {
    'get-status': () => VoiceStatus;
    /** Ensure the venv is provisioned and the sidecar is running. */
    start: () => VoiceStatus;
    /** PCM16LE mono base64 in → recognized text out. */
    transcribe: (pcmBase64: string, sampleRate: number) => string;
    /** Synthesize `text`; audio streams back via the `voice:audio` renderer event keyed by `streamId`. */
    speak: (streamId: string, text: string, voice?: string) => void;
    /**
     * Import a voice-clone sample for a persona: writes the audio, precomputes
     * the mimi embedding once, and returns the stored wav + `.npy` paths. The
     * `.npy` path goes into the persona's `voice.embeddingFile`.
     */
    'import-sample': (
      personaId: string,
      filename: string,
      dataBase64: string
    ) => { file: string; embeddingFile: string };
  }
>;

/**
 * Options for ``agent-process:stop``.
 */
export type AgentProcessStopOptions = {
  /**
   * Skip persisting the workspace snapshot during serve's SIGTERM teardown.
   * Set when a non-chat code column is removed and its terminal exits, where
   * the caller deletes the snapshot tar right after the stop — persisting it
   * (fingerprint pass + full workspace tar export) would be wasted work.
   * Best-effort: an older omni-code without the ``sandbox.discard_snapshot``
   * server function just persists as before.
   */
  discardSnapshot?: boolean;
};

/**
 * Unified agent process API. Main process handles these events, renderer process invokes them.
 * All operations are keyed by a processId — "chat" for the chat tab, CodeTabId for code tabs.
 */
type AgentProcessIpcEvents = Namespaced<
  'agent-process',
  {
    start: (processId: string, arg: AgentProcessStartOptions) => void;
    stop: (processId: string, opts?: AgentProcessStopOptions) => AgentProcessStopResult;
    rebuild: (processId: string, arg: AgentProcessStartOptions) => void;
    resize: (processId: string, cols: number, rows: number) => void;
    'get-status': (processId: string) => WithTimestamp<AgentProcessStatus>;
    /**
     * Freeze the sandbox container via ``docker pause``. Returns an
     * envelope that distinguishes "ok" from "backend doesn't support
     * pause" so callers can decide between updating UI state, falling
     * through to stop/shutdown, or surfacing an error.
     */
    pause: (processId: string) => SandboxPauseResult;
    /** Thaw a previously-paused container. Same envelope shape as ``pause``. */
    unpause: (processId: string) => SandboxPauseResult;
    /**
     * Materialize the selected profile as an environment and atomically rebind
     * this tab. The shared AgentHost and conversation remain alive when the
     * profile stays within the same security domain.
     */
    'switch-sandbox': (processId: string, profileName: string) => SandboxSwitchResult;
  }
>;

/** Product-scoped read connection. Process-wide mutations stay on main's
 * privileged AgentHost control/admin path and are intentionally absent. */
type ManagementRuntimeIpcEvents = Namespaced<
  'management-runtime',
  {
    ensure: () => AgentRuntimeConnection;
    /** Closed main-process broker. The control/admin credential never appears
     * in this request or response and arbitrary RPC methods are rejected. */
    mutate: (request: ManagementAdminRequest) => ManagementAdminResult;
  }
>;

/** Result envelope returned by ``agent-process:pause`` / ``:unpause``.
 *  Mirrors the omni-code server-function schema. */
export type SandboxPauseResult = {
  ok: boolean;
  supported: boolean;
  paused?: boolean;
  reason?: string;
  /** Raw server-function result, when a caller needs fields beyond the envelope. */
  data?: Record<string, unknown>;
};

/** Result of an AgentHost environment profile rebind. */
export type SandboxSwitchResult = {
  ok: boolean;
  /** New profile name once switched (display label). */
  profile?: string;
  /** New backend client type (`docker` / `unix_local` / …). */
  backend?: string;
  /** New container id (docker); absent for unix_local. */
  containerId?: string;
  /** New in-sandbox service URLs (code_server / vnc / …). */
  services?: Record<string, string>;
  /** Failure detail; present when `ok` is false. */
  reason?: string;
  /**
   * When `ok` is false: whether the selected profile crosses an AgentHost
   * security boundary and therefore requires a full stop+relaunch.
   */
  fallback?: boolean;
};

type SnapshotIpcEvents = Namespaced<
  'snapshot',
  {
    /**
     * Cascade-delete the snapshot tar for *snapshotRef*. Called by the
     * renderer when a code tab is removed — the tab is gone for good,
     * its workspace pickle is dead weight. Idempotent (missing file is
     * not an error).
     */
    delete: (snapshotRef: string) => void;
  }
>;

/**
 * Utils API. Main process handles these events, renderer process invokes them.
 */
type UtilIpcEvents = Namespaced<
  'util',
  {
    'select-directory': (path?: string) => string | null;
    'select-file': (path?: string, filters?: Array<{ name: string; extensions: string[] }>) => string | null;
    'get-home-directory': () => string;
    'get-is-directory': (path: string) => boolean;
    'get-is-file': (path: string) => boolean;
    'get-path-exists': (path: string) => boolean;
    'get-os': () => OperatingSystem;
    'get-default-install-dir': () => string;
    'get-default-workspace-dir': () => string;
    'ensure-directory': (path: string) => boolean;
    /**
     * Resolve (and create) a per-session scratch workspace under
     * `<baseDir>/Sessions/<sessionId>`. Used by ambient surfaces (Chat,
     * Orchestrator) that aren't bound to a project, so each session gets an
     * isolated dir instead of mounting the whole workspace tree.
     */
    'session-workspace-dir': (baseDir: string, sessionId: string) => string;
    'open-directory': (path: string) => string;
    /** Open a URL in the user's default browser via Electron `shell.openExternal`. */
    'open-external': (url: string) => void;
    'get-launcher-version': () => string;
    'get-omni-runtime-info': () => OmniRuntimeInfo;
    'check-url': (url: string) => boolean;
    'check-ws': (url: string) => boolean;
    'install-cli-to-path': () => { success: true; symlinkPath: string } | { success: false; error: string };
    'get-cli-in-path-status': () => { installed: boolean; symlinkPath: string };
    'check-models-configured': () => boolean;
    'test-model-connection': (modelRef?: string) => { success: boolean; output: string };
    /** Validate provider credentials with a free GET against the provider's
     *  model-listing endpoint. Runtime-independent (works before the omni
     *  runtime is installed) — the single validation chokepoint for
     *  onboarding and the AI settings connection cards. */
    'validate-provider': (probe: ProviderProbe) => ProviderProbeResult;
    /** Live merged model list from `omni model list --json` (includes discovered models). */
    'list-models': () => RuntimeModelList;
    'list-directory': (path: string) => { name: string; path: string; isDirectory: boolean }[];
    'rebuild-sandbox-image': () => { success: boolean; error?: string };
  }
>;

/**
 * Terminal API. Main process handles these events, renderer process invokes them.
 */
type TerminalIpcEvents = Namespaced<
  'terminal',
  {
    create: (tabId: string) => string;
    list: (tabId: string) => string[];
    write: (id: string, data: string) => void;
    resize: (id: string, cols: number, rows: number) => void;
    dispose: (id: string) => void;
    'dispose-all-for-tab': (tabId: string) => void;
  }
>;

/**
 * Config file I/O API. Main process handles these events, renderer process invokes them.
 */
type ConfigIpcEvents = Namespaced<
  'config',
  {
    'get-omni-config-dir': () => string;
    'get-env-file-path': () => string;
    'read-json-file': (path: string) => unknown | null;
    'write-json-file': (path: string, data: unknown) => void;
    'read-text-file': (path: string) => string | null;
    'write-text-file': (path: string, content: string) => void;
  }
>;

/**
 * ChatGPT (Codex) OAuth API. Main runs the browser PKCE flow and writes tokens
 * to the omni-code config dir; the renderer drives sign-in from Settings.
 */
type CodexIpcEvents = Namespaced<
  'codex',
  {
    /** Open the consent page, await the loopback callback, persist tokens.
     *  Electron-only: requires browser open + loopback :1455. */
    login: () => CodexAuthStatus;
    /** Device-flow sign-in (server/headless). Surfaces user code + URL via
     *  `codex:device-code` then polls until authorized. Works everywhere. */
    link: () => CodexAuthStatus;
    /** Remove the stored tokens. */
    logout: () => void;
    /** Current sign-in status, read from the token store. */
    status: () => CodexAuthStatus;
  }
>;

export type CodexAuthStatus = {
  signedIn: boolean;
  accountId?: string;
  /** Signed in on paper, but the issuer rejected the refresh — re-auth needed.
   *  Read from the `auth_error` stamp the runtime writes into `codex.json`. */
  broken?: boolean;
};

/** Device-flow code surface for the renderer to display while `codex:link` polls. */
export type CodexDeviceCode = { userCode: string; verificationUri: string };

/** One installed WSL distro, from `wsl.exe -l -q` (+ default marker). */
export type WslDistro = { name: string; isDefault: boolean };

/** Result of probing for WSL on this machine. */
export type WslDetectResult = { wsl: 'missing' } | { wsl: 'ok'; distros: WslDistro[] };

/** Timestamped WslBackendManager status, mirroring the manager getStatus() pattern. */
export type WslBackendStatus = {
  state: 'idle' | 'provisioning' | 'starting' | 'running' | 'error';
  distro?: string;
  port?: number;
  docker: 'ok' | 'missing' | 'daemon-down' | 'unknown';
  /** Mirrors WslBackend.persistent so the settings card renders the toggle
   *  from status alone. */
  persistent?: boolean;
  error?: string;
  timestamp: number;
};

/**
 * WSL-backend API (Windows only; all channels resolve in LOCAL Electron
 * main via `localEmitter`). ``wsl:detect`` probes `wsl.exe` and lists
 * distros. ``wsl:link`` provisions the daemon payload into *distro*,
 * persists the ``remoteBackend`` flag, and relaunches the app so the
 * renderer boots on the WS transport. Disconnecting is
 * ``remote-backend:unlink``.
 * ``wsl:get-ws-token`` mints a WS auth token signed with the daemon's
 * shared secret (per-boot, or durable in persistent mode) — no HTTP fetch.
 * ``wsl:set-persistent`` flips persistent daemon mode, restarting the daemon
 * into the new lifecycle. ``wsl:install`` drives `wsl --install`:
 * ``'platform'`` launches an elevated one-shot (UAC prompt; completion is
 * unobservable — likely reboot), ``'distro'`` registers Ubuntu via a plain
 * `wsl.exe --install -d Ubuntu --no-launch` (no elevation, minutes-long
 * Store download). Handlers are implemented by WslBackendManager (Phase 2).
 */
type WslIpcEvents = Namespaced<
  'wsl',
  {
    detect: () => WslDetectResult;
    link: (distro: string) => void;
    'get-ws-token': () => string;
    status: () => WslBackendStatus;
    'set-persistent': (persistent: boolean) => void;
    install: (mode: 'platform' | 'distro') => void;
    /**
     * Install docker-ce inside the linked distro (get.docker.com script run
     * as root via `wsl -u root` — no sudo password needed) and add the
     * default user to the docker group. Long-running; rejects with decoded
     * stderr on failure.
     */
    'install-docker': () => void;
    /** Start dockerd in the linked distro (`service docker start` as root) —
     *  the daemon-down remedy on distros where it isn't auto-started. */
    'start-docker': () => void;
  }
>;

/**
 * Self-hosted-server link API (all channels resolve in LOCAL Electron main
 * via `localEmitter`). ``server:link`` validates *url* (``/api/health`` +
 * ``/api/ws-token`` must answer), persists the ``remoteBackend`` flag, and
 * relaunches the app so the renderer boots on the WS transport. No
 * wsl.exe — auth is the server's own ``/api/ws-token``, which the server
 * gates to loopback + ``OMNI_TRUSTED_CIDRS``. ``server:get-ws-token`` runs
 * the fetch in main (Node fetch, no CORS). Disconnecting is
 * ``remote-backend:unlink``.
 */
type ServerIpcEvents = Namespaced<
  'server',
  {
    link: (url: string) => void;
    'get-ws-token': () => string;
  }
>;

/** Shared disconnect for every remote-backend kind: clears the
 *  ``remoteBackend`` flag and relaunches into standalone-local mode. */
type RemoteBackendIpcEvents = Namespaced<'remote-backend', { unlink: () => void }>;

/**
 * Typed agent-config API. Replaces the path-based `config:*` file I/O for the
 * four Settings configs whose source of truth is the store rather than a
 * file: model providers, MCP servers, network policy, and
 * `.env`. The backend persists them as store keys and re-materializes the
 * agent's on-disk config; the renderer never names a file path.
 */
type SettingsConfigIpcEvents = Namespaced<
  'settings',
  {
    'get-models-config': () => ModelsConfig;
    'set-models-config': (
      config: ModelsConfig,
      options?: { expectedProvider: string | null; confirmProviderChange?: boolean }
    ) => void;
    'get-mcp-config': () => McpConfig;
    'set-mcp-config': (config: McpConfig) => void;
    'get-network-config': () => NetworkConfig;
    'set-network-config': (config: NetworkConfig) => void;
    'get-env': () => string;
    'set-env': (content: string) => void;
  }
>;

/** Payload for creating/replacing a git credential. The token is sent up once
 *  and never returned — handlers persist it to the `SecretStore` and echo back
 *  metadata only. */
export type GitCredentialInput = {
  host: string;
  username: string;
  /** The PAT / token. Write-only: never round-trips back to the renderer. */
  token: string;
  label?: string;
};

/**
 * Git credential management. Write-only by design: `set` accepts a plaintext
 * token but every channel returns metadata (`GitCredential[]`) only, so the
 * token never re-enters the renderer or the `store:changed` snapshot. Host-
 * scoped — `set` upserts by host (one credential per host).
 */
type GitCredentialIpcEvents = Namespaced<
  'git-cred',
  {
    list: () => GitCredential[];
    set: (input: GitCredentialInput) => GitCredential[];
    delete: (id: string) => GitCredential[];
  }
>;

/**
 * GitHub account linking. `link` runs the OAuth device flow (emitting
 * `github:device-code` to the renderer with the user code mid-flow), stores the
 * token as the `github.com` credential, and saves profile metadata. Discovery
 * (`list-owners` / `search-repos`) reads that token in the main process; it
 * never crosses to the renderer.
 */
type GithubIpcEvents = Namespaced<
  'github',
  {
    link: () => GithubStatus;
    status: () => GithubStatus;
    unlink: () => void;
    /** Owners to scope discovery by: the linked user plus their orgs. */
    'list-owners': () => GithubOwner[];
    /** Server-side repo search scoped to one owner (empty query → recent). */
    'search-repos': (query: GithubRepoQuery) => RemoteRepo[];
  }
>;

/**
 * Azure DevOps discovery, authenticated by the stored `dev.azure.com` PAT
 * credential (no separate OAuth — the credential is the link). Org-scoped: the
 * user supplies the org (typed in the picker), and `list-repos` lists every
 * repo across that org's projects, filtered by `query`.
 */
type AzureIpcEvents = Namespaced<
  'azure',
  {
    /** Repos in an org across its projects, name-filtered by `query`. */
    'list-repos': (input: { org: string; query: string }) => RemoteRepo[];
  }
>;

/**
 * How a skill was installed. Drives the source line shown in the UI and
 * determines whether the skill can be uninstalled by the user.
 */
export type SkillSource =
  | { kind: 'local' }
  | { kind: 'file'; filename: string }
  | { kind: 'marketplace'; repo: string; plugin: string; ref: string };

/**
 * One entry in a `.claude-plugin/marketplace.json` plugins[] array.
 * Each plugin is a curated bundle of skill folders within the same repo.
 */
export type MarketplacePlugin = {
  name: string;
  description: string;
  /** Relative root that `skills[]` paths are resolved against. */
  source: string;
  strict?: boolean;
  /** Directory paths (relative to `source`) that contain a SKILL.md. */
  skills: string[];
};

/**
 * One entry in a marketplace.json connectors[] array — a pre-configured MCP
 * server the user can install with one click. `server` is the exact entry
 * merged into `McpConfig.mcpServers` under key `id`.
 */
export type MarketplaceConnector = {
  /** Stable id; becomes the `McpConfig.mcpServers` key. */
  id: string;
  label: string;
  description: string;
  /** Lucide icon name resolved via AppIcon's ICON_MAP; optional. */
  icon?: string;
  server: McpServerEntry;
};

/**
 * Shape of a `.claude-plugin/marketplace.json` file as published by
 * Anthropic's skills repo and other Claude Code plugin marketplaces.
 */
export type MarketplaceManifest = {
  name: string;
  owner?: { name?: string; email?: string };
  metadata?: { description?: string; version?: string };
  plugins: MarketplacePlugin[];
  connectors?: MarketplaceConnector[];
};

/**
 * Persisted record of a marketplace bundle install. Keyed by `${repo}:${plugin}`
 * in `StoreData.installedBundles`. Used to diff against live manifests when
 * checking for updates and to reap skills that the upstream removed.
 */
export type InstalledBundle = {
  repo: string;
  plugin: string;
  /** Git ref the bundle was installed from (defaults to `main`). */
  ref: string;
  /** `metadata.version` from the manifest at install time, if any. */
  version?: string;
  /** SKILL.md `name` of every skill that was installed as part of this bundle. */
  skillNames: string[];
  installedAt: number;
};

export type BundleUpdateStatus = 'up-to-date' | 'update-available' | 'unreachable';

/**
 * Per-bundle update report returned by `skills:check-bundle-updates`. The UI
 * uses this to decide whether to show an "Update" button and what changed.
 */
export type BundleUpdateInfo = {
  bundleKey: string;
  repo: string;
  plugin: string;
  status: BundleUpdateStatus;
  installedVersion?: string;
  /** Manifest version live in the upstream repo. */
  liveVersion?: string;
  /** Skill names present upstream but not in the installed record. */
  addedSkills: string[];
  /** Skill names installed locally but no longer in the upstream manifest. */
  removedSkills: string[];
  /** Reason the manifest could not be fetched (when `status === 'unreachable'`). */
  error?: string;
};

/**
 * A discovered skill entry returned by the skills:list IPC handler.
 */
export type SkillEntry = {
  /** Directory name (must match `name` in SKILL.md frontmatter). */
  name: string;
  /** Human-readable description from SKILL.md frontmatter. */
  description: string;
  /** Absolute path to the skill directory. */
  path: string;
  /** Whether this skill is enabled for agent sessions. */
  enabled: boolean;
  /** How the skill was installed. */
  source: SkillSource;
  /** Skill version from frontmatter (optional). */
  version?: string;
  /** Skill author from frontmatter (optional). */
  author?: string;
  /** License identifier from frontmatter (optional). */
  license?: string;
  /** Environment/dependency requirements from frontmatter (optional). */
  compatibility?: string;
};

/**
 * Skills management API. Main process handles these events, renderer process invokes them.
 */
type SkillsIpcEvents = Namespaced<
  'skills',
  {
    list: () => SkillEntry[];
    install: (filePath: string) => SkillEntry;
    uninstall: (name: string) => void;
    'set-enabled': (name: string, enabled: boolean) => void;
    /** Download a marketplace.json from a github repo and parse it. */
    'fetch-marketplace': (repo: string) => MarketplaceManifest;
    /** Install every skill in the named plugin from the given repo. */
    'install-marketplace-plugin': (repo: string, pluginName: string) => SkillEntry[];
    /**
     * Re-fetch a previously installed bundle, install added/changed skills,
     * and remove skills that the upstream no longer ships. Bumps the stored
     * bundle record's version + skillNames + installedAt on success.
     */
    'update-marketplace-plugin': (repo: string, pluginName: string) => SkillEntry[];
    /** Probe upstream manifests for every installed bundle and report diffs. */
    'check-bundle-updates': () => BundleUpdateInfo[];
  }
>;

/**
 * Project & Ticket API. Main process handles these events, renderer process invokes them.
 */
type ProjectIpcEvents = Namespaced<
  'project',
  {
    'add-project': (project: Omit<Project, 'id' | 'createdAt'>) => Project;
    'update-project': (id: ProjectId, patch: Partial<Omit<Project, 'id' | 'createdAt'>>) => void;
    'remove-project': (id: ProjectId) => void;
    /** Resolved project directory — `Projects/<slug>/` or the default workspace root for Personal. Null if the project is unknown. */
    'get-dir': (projectId: ProjectId) => string | null;
    'check-git-repo': (workspaceDir: string) => GitRepoInfo;
    'add-ticket': (ticket: Omit<Ticket, 'id' | 'createdAt' | 'updatedAt' | 'columnId'>) => Ticket;
    'update-ticket': (id: TicketId, patch: Partial<Omit<Ticket, 'id' | 'projectId' | 'createdAt'>>) => void;
    'get-tickets': (projectId: ProjectId) => Ticket[];
    'get-ticket-workspace': (ticketId: TicketId) => string;
    'get-tasks': () => Task[];
    'get-next-ticket': (projectId: ProjectId) => Ticket | null;
    'move-ticket-to-column': (ticketId: TicketId, columnId: ColumnId) => void;
    'get-pipeline': (projectId: ProjectId) => Pipeline;
    'list-artifacts': (ticketId: TicketId, dirPath?: string) => ArtifactFileEntry[];
    'read-artifact': (ticketId: TicketId, relativePath: string) => ArtifactFileContent;
    'open-artifact-external': (ticketId: TicketId, relativePath: string) => void;
    'apply-code-tab-source-changes': (tabId: CodeTabId, sourceId: string) => PrMergeResult;
    /**
     * Detect open PRs across a code tab's sources (deck banner). For
     * projectless (chat) columns the tab's live process workspace dir is the
     * single implicit source, so this returns 0 or 1 entries.
     */
    'detect-code-tab-pull-requests': (tabId: CodeTabId) => ContainerPullRequest[];
    // Supervisor operations
    'ensure-supervisor-infra': (ticketId: TicketId) => void;
    'start-supervisor': (ticketId: TicketId, profileName?: string) => void;
    'stop-supervisor': (ticketId: TicketId) => void;
    'send-supervisor-message': (ticketId: TicketId, message: string) => void;
    'reset-supervisor-session': (ticketId: TicketId) => void;
    /** Assign (string principal id) or unassign (null) a ticket. */
    'assign-ticket': (ticketId: TicketId, assignee: string | null) => void;
    /**
     * Retry the deferred cleanup for a completed ticket whose worktree was
     * dirty. Returns true when cleanup succeeded, false when the worktree
     * still has uncommitted changes (cleanupPending stays set).
     */
    'finalize-ticket-cleanup': (ticketId: TicketId) => boolean;
    'set-auto-dispatch': (projectId: ProjectId, enabled: boolean) => void;
    // Context file operations (replaces project.brief)
    'read-context': (projectId: ProjectId) => string;
    'write-context': (projectId: ProjectId, content: string) => void;
    // Project file listing
    'list-project-files': (projectId: ProjectId) => ArtifactFileEntry[];
    'get-context-preview': (projectId: ProjectId) => string;
    'open-project-file': (projectId: ProjectId, relativePath: string) => void;
  }
>;

/**
 * Supervisor bridge. The Code column owns the session, the WebSocket, and the
 * run lifecycle. Main only orchestrates — decides *when* to submit a prompt
 * and reacts to forwarded events. No main-process session id; no prepare step;
 * no client-request round trip (tool calls are handled entirely in the
 * renderer's `buildClientToolHandler`).
 */
/**
 * Per-dispatch run intent. The orchestrator composes this from its own state
 * (autopilot mode, supervisor framing, retry context, etc.) and ships it with
 * the `bridge.run` dispatch. The column merges it onto its locally owned
 * variables. Fields here are the subset that need to differ between
 * orchestrator-driven and user-driven submits.
 */
export type RunOverrides = {
  /** Prepended to the column's existing additional_instructions. */
  additionalInstructions?: string;
  /** Session approval reviewer: 'auto' routes approvals to the guardian. */
  approvalsReviewer?: 'user' | 'auto';
  /** Approval policy override; replaces the column's default. */
  safeToolOverrides?: {
    safe_tool_names?: string[];
    safe_tool_patterns?: string[];
    safe_mcp_tools?: { server_label: string; tool_name: string }[];
  };
};

export type SupervisorBridgeRequest =
  | {
      /**
       * Make sure a Code tab exists for this ticket. Creates one if needed,
       * switches the layout to code. Resolves after the actor registers.
       */
      kind: 'ensure-column';
      ticketId: TicketId;
      workspaceDir?: string;
      profileName?: string;
    }
  | {
      /**
       * Start a run. Routes through the same handleSubmit path the user's
       * keyboard uses. `runOverrides` carries the orchestrator's intent for
       * THIS run (supervisor prompt to inject, approval policy) atomically
       * with the dispatch — the column merges these on top of its locally
       * owned variables (`client_tools`, `workspace_root`) without having to
       * read steady-state autopilot mode (which would race with the dispatch).
       *
       * For user-initiated submits, `runOverrides` is undefined and the
       * column derives variables from steady-state `ticket.autopilot`.
       */
      kind: 'run';
      ticketId: TicketId;
      prompt: string;
      runOverrides?: RunOverrides;
    }
  | {
      /**
       * Start an autopilot ``/goal`` loop on this ticket's session. The
       * agent-side ``goal`` server function (in omni-code) owns the loop:
       * it installs the periodic tick, drives continuation prompts, and
       * stops when the agent calls ``goal_complete`` or when the launcher
       * issues a ``goal-stop``. Launcher subscribes to ``goal-update``
       * events to mirror snapshot state into the ticket record.
       */
      kind: 'goal-start';
      ticketId: TicketId;
      prompt: string;
      maxTurns?: number;
      tickInterval?: number;
      runOverrides?: RunOverrides;
    }
  | {
      /** Cancel the running ``/goal`` loop on this ticket's session. */
      kind: 'goal-stop';
      ticketId: TicketId;
    }
  | { kind: 'send'; ticketId: TicketId; message: string }
  | { kind: 'stop'; ticketId: TicketId }
  | {
      /** Stop current run and mint a fresh session id on the column. */
      kind: 'reset';
      ticketId: TicketId;
    }
  | { kind: 'dispose'; ticketId: TicketId };

export type GoalSnapshotPayload = {
  goal: string;
  turn: number;
  max_turns: number;
  tick_interval?: number;
  last_reason: string | null;
  status: 'active' | 'completed' | 'cancelled';
  started_at: number;
  completion_reason: string | null;
};

export type SupervisorBridgeEvent =
  | { kind: 'run-started'; ticketId: TicketId; runId: string }
  | { kind: 'run-end'; ticketId: TicketId; reason: string }
  | {
      kind: 'message';
      ticketId: TicketId;
      content: string;
      role?: 'user' | 'assistant';
      toolName?: string;
    }
  | { kind: 'token-usage'; ticketId: TicketId; usage: TokenUsage }
  | { kind: 'disconnected'; ticketId: TicketId }
  | {
      /**
       * Forwarded ``ui.goal.update`` snapshot from the omniagents ``/goal``
       * loop. ``snapshot`` is null when no goal is set on the session
       * (terminal cleanup). The orchestrator maps this onto ticket phase.
       */
      kind: 'goal-update';
      ticketId: TicketId;
      snapshot: GoalSnapshotPayload | null;
    }
  | {
      /**
       * Forwarded plan (`tasks_snapshot`) update from the column's session.
       * The orchestrator persists non-empty snapshots to the ticket's
       * `lastPlanSnapshot`. `null` means the session currently has no
       * canonical plan — which every fresh session reports before the agent
       * (re)creates its plan — so it carries no information and is ignored;
       * clearing happens at ticket settlement instead.
       */
      kind: 'plan-update';
      ticketId: TicketId;
      snapshot: PlanSnapshotEntry[] | null;
    };

type SupervisorIpcEvents = Namespaced<
  'supervisor',
  {
    /** Renderer reports the result of a bridge request issued by main. */
    'dispatch-result': (requestId: string, ok: boolean, result?: { runId?: string }, error?: string) => void;
    /** Renderer forwards a sandbox run event so main's orchestrator can react. */
    event: (event: SupervisorBridgeEvent) => void;
  }
>;

/**
 * Routine bridge — same column-ownership model as the supervisor bridge, but
 * for scheduled tasks (Routines). When a routine fires, main asks the renderer
 * to ensure a Code column exists for the run and to start a single run on that
 * column's live session, so the conversation streams into the visible UI
 * exactly like a user-driven session. Main only orchestrates (history, status,
 * toasts) by observing the forwarded run/approval events — it never opens its
 * own socket into the sandbox.
 */
export type RoutineBridgeRequest =
  | {
      /**
       * Ensure a Code tab exists for this routine run (keyed by `sessionId`)
       * and switch the layout to it. Idempotent. Resolves immediately — the
       * column may still be booting; `start-run` awaits the actor.
       */
      kind: 'ensure-column';
      taskId: string;
      sessionId: string;
      activate?: boolean;
    }
  | {
      /**
       * Start a single run on the routine column's session, routed through the
       * same handleSubmit path the user's keyboard uses. `safeToolOverrides`
       * carries the routine's always-allow list so pre-approved tools don't
       * prompt. Resolves with the run id once `start_run` is acked.
       */
      kind: 'start-run';
      taskId: string;
      prompt: string;
      safeToolOverrides?: RunOverrides['safeToolOverrides'];
    }
  | { kind: 'stop'; taskId: string }
  | { kind: 'dispose'; taskId: string };

export type RoutineApprovalRequest = {
  kind: 'function' | 'mcp';
  toolName?: string;
  serverLabel?: string;
};

export type RoutineBridgeEvent =
  | { kind: 'run-started'; taskId: string; runId: string }
  | { kind: 'run-end'; taskId: string; reason: string }
  | { kind: 'approval-requested'; taskId: string; approval: RoutineApprovalRequest }
  | { kind: 'approval-resolved'; taskId: string }
  | { kind: 'disconnected'; taskId: string };

type RoutineIpcEvents = Namespaced<
  'routine',
  {
    /** Renderer reports the result of a routine bridge request issued by main. */
    'dispatch-result': (requestId: string, ok: boolean, result?: { runId?: string }, error?: string) => void;
    /** Renderer forwards a routine run/approval event so main can update history. */
    event: (event: RoutineBridgeEvent) => void;
    /**
     * Renderer announces its dispatch listener is wired. Main queues
     * `routine:dispatch` sends until this arrives — at boot the manager can
     * fire (missed-run catch-up) before any window exists, and a send into
     * the void would otherwise burn the whole request timeout.
     */
    'renderer-ready': () => void;
    /**
     * Claim a dispatch for execution. `routine:dispatch` is a broadcast; in
     * server mode every connected client receives it, and exactly one may
     * act (double `start-run` would enqueue the prompt twice). First claim
     * wins; the rest drop the request.
     */
    'claim-dispatch': (requestId: string) => boolean;
  }
>;

/**
 * Milestone API. Main process handles these events, renderer process invokes them.
 */
type MilestoneIpcEvents = Namespaced<
  'milestone',
  {
    'get-items': (projectId: ProjectId) => Milestone[];
    'add-item': (item: Omit<Milestone, 'id' | 'createdAt' | 'updatedAt'>) => Milestone;
    'update-item': (id: MilestoneId, patch: Partial<Omit<Milestone, 'id' | 'projectId' | 'createdAt'>>) => void;
    'remove-item': (id: MilestoneId) => void;
  }
>;

/**
 * Page API. Main process handles these events, renderer process invokes them.
 */
type PageIpcEvents = Namespaced<
  'page',
  {
    'get-items': (projectId: ProjectId) => Page[];
    /** All pages across all projects. Used by the global Inbox view. */
    'get-all': () => Page[];
    /** Create a new page. Optional `template` seeds the body with starter content. */
    'add-item': (
      item: Omit<Page, 'id' | 'createdAt' | 'updatedAt'>,
      template?: import('@/lib/page-templates').TemplateKey
    ) => Page;
    'update-item': (id: PageId, patch: Partial<Omit<Page, 'id' | 'projectId' | 'createdAt'>>) => void;
    'remove-item': (id: PageId) => void;
    'read-content': (pageId: PageId) => string;
    'write-content': (pageId: PageId, content: string) => void;
    reorder: (pageId: PageId, newParentId: PageId | null, newSortOrder: number) => void;
    /** Subscribe to external-edit notifications for a page's file. Returns the current on-disk content. */
    watch: (pageId: PageId) => { content: string } | null;
    /** Unsubscribe from external-edit notifications. */
    unwatch: (pageId: PageId) => void;
  }
>;

/**
 * One-shot migration notice IPC. The Task #18 pages-relocation migration
 * records its summary into the store on boot; the renderer reads it to
 * show a dismissible notice, then either acknowledges or runs cleanup
 * (which `rm -rf`s the legacy paths and clears the notice in one step).
 */
type MigrationIpcEvents = Namespaced<
  'migration',
  {
    /** Read the current notice state. Null = nothing to show. */
    'get-pages-state': () => PagesMigrationState | null;
    /** Mark the notice dismissed without touching legacy files. */
    'acknowledge-pages': () => void;
    /**
     * Delete the recorded legacy directories/files and clear the notice.
     * Returns the count actually removed (paths that no longer exist are
     * skipped silently). Idempotent.
     */
    'cleanup-legacy-pages': () => { removed: number };
  }
>;

/**
 * Inbox API. Renderer invokes, main handles. The inbox is a global capture
 * surface (items can be projectless) backed by electron-store. Its lifecycle
 * is: new → promoted (to ticket/project) or deferred.
 */
type InboxIpcEvents = Namespaced<
  'inbox',
  {
    /** All inbox items including promoted tombstones. */
    'get-all': () => InboxItem[];
    /** Active view: status !== 'later' and not promoted. */
    'get-active': () => InboxItem[];
    /** Create a new item. Defaults status to 'new'. */
    add: (input: { title: string; note?: string; projectId?: ProjectId | null; attachments?: string[] }) => InboxItem;
    /** Patch basic fields. Status transitions use the dedicated verbs below. */
    update: (id: InboxItemId, patch: Partial<Pick<InboxItem, 'title' | 'note' | 'projectId' | 'attachments'>>) => void;
    /** Hard delete. Use sparingly — prefer defer/promote for audit trail. */
    remove: (id: InboxItemId) => void;
    /** Move to 'later'. Stamps `laterAt`. */
    defer: (id: InboxItemId) => void;
    /** Move out of 'later' back to 'new'. Clears `laterAt`. */
    reactivate: (id: InboxItemId) => void;
    /**
     * Promote to a ticket. Requires projectId. Seeds the ticket's title and
     * description from the inbox item. Stamps `promotedTo` on the inbox item
     * so it disappears from active views but stays as a tombstone for
     * undo/audit.
     */
    'promote-to-ticket': (
      id: InboxItemId,
      opts: { projectId: ProjectId; milestoneId?: MilestoneId; columnId?: ColumnId }
    ) => Ticket;
    /**
     * Promote to a new project. Seeds the project's label. Stamps
     * `promotedTo`.
     */
    'promote-to-project': (id: InboxItemId, opts: { label: string }) => Project;
    /** Manually trigger the expiry sweep. Returns number of items flipped to later. */
    sweep: () => number;
    /**
     * GC promoted tombstones older than 30d. Returns number removed.
     * Called on a schedule; exposed for manual triggering in tests/tools.
     */
    'gc-promoted': () => number;
  }
>;

/**
 * App-control events. Renderer registers every live `<Webview>` with main so
 * agents can drive them via client tools (list_apps, app_click, app_snapshot,
 * ...). See `src/shared/app-control-types.ts` for payload shapes.
 */
type AppControlIpcEvents = Namespaced<
  'app',
  {
    register: (payload: import('@/shared/app-control-types').AppRegistrationPayload) => void;
    update: (
      handleId: import('@/shared/app-control-types').AppHandleId,
      patch: Partial<import('@/shared/app-control-types').AppRegistrationPayload>
    ) => void;
    unregister: (handleId: import('@/shared/app-control-types').AppHandleId) => void;
    list: () => import('@/shared/app-control-types').LiveAppSnapshot[];
    navigate: (handleId: import('@/shared/app-control-types').AppHandleId, url: string) => void;
    reload: (handleId: import('@/shared/app-control-types').AppHandleId) => void;
    back: (handleId: import('@/shared/app-control-types').AppHandleId) => void;
    forward: (handleId: import('@/shared/app-control-types').AppHandleId) => void;
    eval: (handleId: import('@/shared/app-control-types').AppHandleId, code: string) => unknown;
    screenshot: (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options?: import('@/shared/app-control-types').AppScreenshotOptions
    ) => string;
    console: (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options?: { minLevel?: import('@/shared/app-control-types').AppConsoleLevel; clear?: boolean }
    ) => import('@/shared/app-control-types').AppConsoleEntry[];
    snapshot: (
      handleId: import('@/shared/app-control-types').AppHandleId
    ) => import('@/shared/app-control-types').AxNode;
    'snapshot-diff': (
      handleId: import('@/shared/app-control-types').AppHandleId
    ) => import('@/main/app-control-cdp').SnapshotDiff;
    click: (
      handleId: import('@/shared/app-control-types').AppHandleId,
      ref: string,
      options?: { button?: import('@/shared/app-control-types').AppClickButton }
    ) => void;
    fill: (handleId: import('@/shared/app-control-types').AppHandleId, ref: string, text: string) => void;
    type: (handleId: import('@/shared/app-control-types').AppHandleId, text: string) => void;
    press: (handleId: import('@/shared/app-control-types').AppHandleId, key: string) => void;
    scroll: (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options: { dx?: number; dy?: number; toTop?: boolean; toBottom?: boolean }
    ) => void;
    'inject-css': (handleId: import('@/shared/app-control-types').AppHandleId, css: string) => string;
    'remove-inserted-css': (handleId: import('@/shared/app-control-types').AppHandleId, key: string) => void;
    find: (
      handleId: import('@/shared/app-control-types').AppHandleId,
      query: string,
      options?: { caseSensitive?: boolean; forward?: boolean; findNext?: boolean }
    ) => { matches: number; activeOrdinal: number };
    'stop-find': (handleId: import('@/shared/app-control-types').AppHandleId) => void;
    'wait-for': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options: { selector?: string; urlIncludes?: string; networkIdle?: boolean; timeoutMs?: number }
    ) => { ok: true; matched: 'selector' | 'url' | 'networkIdle' };
    'scroll-to-ref': (handleId: import('@/shared/app-control-types').AppHandleId, ref: string) => void;
    'network-log': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options?: { limit?: number; since?: number; urlIncludes?: string; statusMin?: number; clear?: boolean }
    ) => import('@/main/app-control-cdp').NetworkLogEntry[];
    pdf: (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options?: { artifactsSubdir?: string; landscape?: boolean; printBackground?: boolean }
    ) => string;
    'full-screenshot': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options?: import('@/shared/app-control-types').AppScreenshotOptions
    ) => string;
    'element-screenshot': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      ref: string,
      options?: import('@/shared/app-control-types').AppScreenshotOptions
    ) => string;
    'set-viewport': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      options: { width: number; height: number; deviceScaleFactor?: number; mobile?: boolean } | { clear: true }
    ) => void;
    'set-user-agent': (handleId: import('@/shared/app-control-types').AppHandleId, ua: string) => void;
    'set-zoom': (handleId: import('@/shared/app-control-types').AppHandleId, factor: number) => void;
    'cookies-get': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      filter?: { url?: string; name?: string; domain?: string; path?: string }
    ) => unknown[];
    'cookies-set': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      cookie: {
        url: string;
        name: string;
        value: string;
        domain?: string;
        path?: string;
        secure?: boolean;
        httpOnly?: boolean;
        expirationDate?: number;
        sameSite?: 'unspecified' | 'no_restriction' | 'lax' | 'strict';
      }
    ) => void;
    'cookies-clear': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      filter?: { url?: string; name?: string }
    ) => number;
    'storage-get': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      which: 'local' | 'session'
    ) => Record<string, string>;
    'storage-set': (
      handleId: import('@/shared/app-control-types').AppHandleId,
      which: 'local' | 'session',
      entries: Record<string, string>
    ) => void;
    'storage-clear': (handleId: import('@/shared/app-control-types').AppHandleId, which: 'local' | 'session') => void;
  }
>;

/**
 * Browser API. Owns tabs, history, bookmarks, and profiles. Renderer invokes,
 * main handles. State mutations broadcast `browser:state-changed` events so
 * every open surface re-renders from the same source of truth.
 */
type BrowserIpcEvents = Namespaced<
  'browser',
  {
    /** Full snapshot of the persisted browser state. Cheap — entire state lives in electron-store. */
    'get-state': () => {
      profiles: BrowserProfile[];
      tabsets: Record<BrowserTabsetId, BrowserTabset>;
      bookmarks: BrowserBookmark[];
    };
    /** Create a new profile. Partition is derived from id. */
    'profile-add': (input: { label: string; incognito?: boolean }) => BrowserProfile;
    'profile-remove': (id: BrowserProfileId) => void;
    /** Idempotent. Ensures a tabset exists and returns it; creates a blank tab on first call. */
    'tabset-ensure': (
      id: BrowserTabsetId,
      opts?: { profileId?: BrowserProfileId; initialUrl?: string }
    ) => BrowserTabset;
    'tabset-remove': (id: BrowserTabsetId) => void;
    /** Switch a tabset to a different profile; swaps its partition on remount. */
    'tabset-set-profile': (id: BrowserTabsetId, profileId: BrowserProfileId) => void;
    /** Create a new tab. If `activate` is true (default), also activates it. */
    'tab-create': (
      tabsetId: BrowserTabsetId,
      opts?: { url?: string; activate?: boolean; profileId?: BrowserProfileId }
    ) => BrowserTab;
    'tab-close': (tabsetId: BrowserTabsetId, tabId: BrowserTabId) => void;
    'tab-activate': (tabsetId: BrowserTabsetId, tabId: BrowserTabId) => void;
    'tab-navigate': (tabsetId: BrowserTabsetId, tabId: BrowserTabId, url: string) => void;
    'tab-update-meta': (
      tabsetId: BrowserTabsetId,
      tabId: BrowserTabId,
      patch: { title?: string; favicon?: string; url?: string }
    ) => void;
    'tab-reorder': (tabsetId: BrowserTabsetId, tabIds: BrowserTabId[]) => void;
    'tab-pin': (tabsetId: BrowserTabsetId, tabId: BrowserTabId, pinned: boolean) => void;
    'tab-duplicate': (tabsetId: BrowserTabsetId, tabId: BrowserTabId) => BrowserTab;
    /** Restore the most recently closed tab in this tabset (LIFO). */
    'tab-reopen': (tabsetId: BrowserTabsetId) => BrowserTab | null;
    /** Record a visit. Dedupes against the most recent entry for the same URL. */
    'history-record': (entry: { url: string; title?: string; profileId: BrowserProfileId }) => void;
    'history-list': (opts?: { query?: string; limit?: number; profileId?: BrowserProfileId }) => BrowserHistoryEntry[];
    'history-clear': (opts?: { profileId?: BrowserProfileId }) => void;
    'bookmark-add': (input: { url: string; title: string; folder?: string }) => BrowserBookmark;
    'bookmark-remove': (id: string) => void;
    /** Returns a ranked mix of history + bookmarks + a synthetic search entry. */
    suggest: (query: string, opts?: { limit?: number; profileId?: BrowserProfileId }) => BrowserSuggestion[];
    /** List in-memory downloads across every watched partition. Newest first. */
    'downloads-list': () => BrowserDownloadEntry[];
    'downloads-clear': () => number;
    'downloads-remove': (id: string) => void;
    /** Open a completed download with the OS default handler. */
    'downloads-open-file': (id: string) => string;
    /** Reveal a completed download in Finder / Explorer. */
    'downloads-show-in-folder': (id: string) => void;
    /**
     * Signal that a new webview with this partition is about to render, so
     * the main process attaches its `will-download` listener. Safe to call
     * repeatedly — idempotent per-session.
     */
    'downloads-watch-partition': (partition: string) => void;
    /** List all outstanding permission requests awaiting a decision. */
    'permissions-list': () => import('@/shared/permissions-types').PermissionRequest[];
    /** Allow or deny a pending permission request by id. */
    'permissions-decide': (id: string, allow: boolean) => void;
    /** Attach the permission handler to a new partition on first mount. */
    'permissions-watch-partition': (partition: string) => void;
  }
>;

/**
 * Download tracking — one entry per `DownloadItem` observed via
 * `session.will-download`. Lives in memory (not persisted), newest first.
 */
export type BrowserDownloadState = 'progressing' | 'interrupted' | 'paused' | 'completed' | 'cancelled';

export type BrowserDownloadEntry = {
  id: string;
  url: string;
  filename: string;
  savePath?: string;
  mimeType?: string;
  totalBytes: number;
  receivedBytes: number;
  state: BrowserDownloadState;
  startedAt: number;
  endedAt?: number;
  partition?: string;
};

/**
 * Sandbox management IPC (Sandboxes tab). Profile discovery reads bundled +
 * user YAMLs from disk; container channels shell out to docker with the same
 * env resolution as the orphan sweep. Registered in both shells
 * (`src/main/index.ts` and `src/server/managers.ts` wireGlobalHandlers).
 */
type SandboxIpcEvents = Namespaced<
  'sandbox',
  {
    'list-profiles': () => ProfileSummary[];
    /** Raw YAML for the read-only detail view; null for the implicit host profile (no file). */
    'read-profile': (name: string) => { yaml: string } | null;
    /** Copy the resolved bundled YAML to `<config>/sandbox/<name>.yml`. Throws if the override exists or the profile is implicit. */
    'create-override': (name: string) => { path: string };
    'list-containers': () => SandboxContainerSummary[];
    /** Force-remove a container. Throws (with the owner label) when the id is protected. */
    'remove-container': (id: string) => void;
    /** Run the orphan cleanup pass on demand. */
    'sweep-orphans': () => { removed: string[] };
    'substrate-status': () => SandboxSubstrateStatus;
    /**
     * Overwrite a profile YAML. Only file-backed profiles are writable
     * (`origin: 'user-override'` or user-created — never the implicit host);
     * builtin-origin profiles must go through `create-override` first. The
     * YAML is parse-validated (and must contain a `client.type` string)
     * before the write; invalid input throws without touching the file.
     */
    'write-profile': (name: string, yaml: string) => void;
    /** Workspace snapshot tars (`<config>/snapshots/*.tar`) for the browser UI. */
    'list-snapshots': () => SandboxSnapshotSummary[];
    /** Tail of `docker logs` for one labeled container (both streams, capped). */
    'container-logs': (id: string, tailLines: number) => { logs: string };
    /** Presence/size of the devbox (or given) image on the backend's dockerd. */
    'image-status': (image: string) => SandboxImageStatus;
    /** `docker pull <image>` — resolves when the pull completes (may take minutes). */
    'pull-image': (image: string) => void;
    /** `docker image prune -f` (dangling only). Returns reclaimed byte count when docker reports it. */
    'prune-images': () => { reclaimedBytes: number | null };
  }
>;

/** One workspace snapshot tar under `<config>/snapshots/`. */
export type SandboxSnapshotSummary = {
  /** Workspace snapshot reference — the tar's filename stem. */
  snapshotRef: string;
  sizeBytes: number;
  /** mtime, epoch ms. */
  modifiedAt: number;
  /** True when an open code tab still claims this Workspace snapshot. */
  inUse: boolean;
  /** Human label when the owning tab (or an archived conversation's title)
   *  is known; else null. */
  label: string | null;
};

export type SandboxImageStatus = {
  /** Image reference that was queried (as passed in). */
  image: string;
  present: boolean;
  /** Local image size in bytes when present. */
  sizeBytes: number | null;
};

/**
 * Intersection of all the events that the renderer can invoke and main process can handle.
 */
type ScheduledTaskIpcEvents = Namespaced<
  'scheduled-task',
  {
    list: () => ScheduledTask[];
    create: (input: ScheduledTaskInput) => ScheduledTask;
    update: (taskId: string, patch: ScheduledTaskUpdate) => ScheduledTask;
    delete: (taskId: string) => void;
    'run-now': (taskId: string) => ScheduledTask;
    'allow-tool': (taskId: string, toolName: string) => ScheduledTask;
    'revoke-tool': (taskId: string, toolName: string) => ScheduledTask;
    'allow-mcp-tool': (taskId: string, tool: ScheduledTaskAllowedMcpTool) => ScheduledTask;
    'revoke-mcp-tool': (taskId: string, tool: ScheduledTaskAllowedMcpTool) => ScheduledTask;
  }
>;

export type IpcEvents = MainProcessIpcEvents &
  OmniInstallProcessIpcEvents &
  VoiceIpcEvents &
  AgentProcessIpcEvents &
  ManagementRuntimeIpcEvents &
  SnapshotIpcEvents &
  UtilIpcEvents &
  TerminalIpcEvents &
  StoreIpcEvents &
  ScheduledTaskIpcEvents &
  ConfigIpcEvents &
  CodexIpcEvents &
  WslIpcEvents &
  ServerIpcEvents &
  RemoteBackendIpcEvents &
  SettingsConfigIpcEvents &
  GitCredentialIpcEvents &
  GithubIpcEvents &
  AzureIpcEvents &
  SkillsIpcEvents &
  ProjectIpcEvents &
  MilestoneIpcEvents &
  PageIpcEvents &
  InboxIpcEvents &
  MigrationIpcEvents &
  AppControlIpcEvents &
  BrowserIpcEvents &
  SupervisorIpcEvents &
  RoutineIpcEvents &
  SandboxIpcEvents;

/**
 * Store events. Main process emits these events, renderer process listens to them.
 */
type StoreIpcRendererEvents = Namespaced<
  'store',
  {
    changed: [StoreData | undefined];
  }
>;

/**
 * Terminal events. Main process emits these events, renderer process listens to them.
 */
type TerminalIpcRendererEvents = Namespaced<
  'terminal',
  {
    output: [string, string, string];
    exited: [string, string, number];
  }
>;

/**
 * Main process events. Main process emits these events, renderer process listens to them.
 */
type MainProcessIpcRendererEvents = Namespaced<
  'main-process',
  {
    status: [WithTimestamp<MainProcessStatus>];
  }
>;

type OmniInstallProcessIpcRendererEvents = Namespaced<
  'omni-install-process',
  {
    status: [WithTimestamp<OmniInstallProcessStatus>];
    log: [WithTimestamp<LogEntry>];
    'raw-output': [string];
  }
>;

type VoiceIpcRendererEvents = Namespaced<
  'voice',
  {
    /** One streamed TTS chunk (PCM16LE base64) for the `speak` call `streamId`. */
    audio: [{ streamId: string; pcm: string; sampleRate: number }];
    /** Terminal marker for a `speak` stream. */
    'audio-end': [{ streamId: string }];
  }
>;

/**
 * Unified agent process events. Main process emits these, renderer listens.
 * All keyed by processId.
 */
type AgentProcessIpcRendererEvents = Namespaced<
  'agent-process',
  {
    status: [string, WithTimestamp<AgentProcessStatus>];
    'raw-output': [string, string];
  }
>;

/**
 * Dev events. Main process emits these events, renderer process listens to them.
 */
type DevIpcRendererEvents = Namespaced<
  'dev',
  {
    'console-log': [unknown];
  }
>;

/**
 * Toast notification level and payload pushed from main to renderer.
 */
export type ToastPayload = {
  level: 'info' | 'success' | 'warning' | 'error';
  title: string;
  description?: string;
};

/**
 * Toast events. Main process emits these events, renderer process listens to them.
 */
type ToastIpcRendererEvents = Namespaced<
  'toast',
  {
    show: [ToastPayload];
  }
>;

/**
 * Project events. Main process emits these events, renderer process listens to them.
 */
type ProjectIpcRendererEvents = Namespaced<
  'project',
  {
    'task-status': [TaskId, WithTimestamp<AgentProcessStatus>];
    'task-session': [TaskId, string];
    phase: [TicketId, import('@/shared/ticket-phase').TicketPhase];
    'supervisor-message': [TicketId, SessionMessage];
    'token-usage': [TicketId, TokenUsage];
  }
>;

/**
 * Page content events. Main process emits when a watched page file changes on
 * disk due to an external edit (e.g. the user edited a page in their IDE, or
 * an MCP client like Claude Desktop wrote into `<config>/pages/<projectId>/`).
 */
type PageIpcRendererEvents = Namespaced<
  'page',
  {
    'content-changed': [PageId, string];
    'content-deleted': [PageId];
  }
>;

/**
 * Supervisor bridge — main → renderer. Main issues commands to the column
 * actor. Tool calls / approvals never round-trip through main; the column's
 * `buildClientToolHandler` handles them directly.
 */
type SupervisorIpcRendererEvents = Namespaced<
  'supervisor',
  {
    dispatch: [string, SupervisorBridgeRequest];
  }
>;

/**
 * Routine bridge — main → renderer. Main issues commands to the routine column
 * actor (ensure the column, start a run). Mirrors the supervisor bridge: tool
 * calls / approvals are handled by the column itself, never round-tripped
 * through main.
 */
type RoutineIpcRendererEvents = Namespaced<
  'routine',
  {
    dispatch: [string, RoutineBridgeRequest];
  }
>;

/**
 * Browser events. Main process emits a full-state snapshot whenever any
 * browser mutation lands — tabs, history, bookmarks, profiles. Renderers
 * simply replace their atom.
 */
type BrowserIpcRendererEvents = Namespaced<
  'browser',
  {
    'state-changed': [
      {
        profiles: BrowserProfile[];
        tabsets: Record<BrowserTabsetId, BrowserTabset>;
        bookmarks: BrowserBookmark[];
      },
    ];
    'downloads-changed': [BrowserDownloadEntry[]];
    'permissions-changed': [import('@/shared/permissions-types').PermissionRequest[]];
  }
>;

/**
 * Intersection of all the events emitted by main process that the renderer can listen to.
 */
/** Main→renderer: the device-flow user code to display while `github:link` polls. */
type GithubIpcRendererEvents = Namespaced<'github', { 'device-code': [GithubDeviceCode] }>;

/** Main→renderer: the device-flow user code to display while `codex:link` polls. */
type CodexIpcRendererEvents = Namespaced<'codex', { 'device-code': [CodexDeviceCode] }>;

/** Main→renderer: WslBackendManager status pushes (Windows only). Broadcast
 *  on every state transition so the settings card tracks the daemon live. */
type WslIpcRendererEvents = Namespaced<'wsl', { 'status-changed': [WslBackendStatus] }>;

export type IpcRendererEvents = TerminalIpcRendererEvents &
  GithubIpcRendererEvents &
  CodexIpcRendererEvents &
  WslIpcRendererEvents &
  MainProcessIpcRendererEvents &
  OmniInstallProcessIpcRendererEvents &
  VoiceIpcRendererEvents &
  AgentProcessIpcRendererEvents &
  DevIpcRendererEvents &
  StoreIpcRendererEvents &
  ProjectIpcRendererEvents &
  PageIpcRendererEvents &
  ToastIpcRendererEvents &
  BrowserIpcRendererEvents &
  SupervisorIpcRendererEvents &
  RoutineIpcRendererEvents;

// #region Config file types

export type ReasoningEffort = 'low' | 'medium' | 'high' | 'xhigh';

export type ModelEntry = {
  model: string;
  label?: string;
  realtime?: boolean;
  reasoning?: ReasoningEffort;
  max_input_tokens?: number;
  max_output_tokens?: number;
  model_settings?: Record<string, unknown>;
};

/** One entry from `omni model list --json` — the live, merged runtime view. */
export type RuntimeModelEntry = {
  name: string;
  label?: string;
  provider?: string;
  realtime?: boolean;
  reasoning?: ReasoningEffort;
};

export type RuntimeModelList = {
  models: RuntimeModelEntry[];
  default: string | null;
  voice_default: string | null;
};

export type ProviderEntry = {
  // `openai-oauth` — ChatGPT (Codex) subscription auth. No `api_key`; the
  // runtime authenticates with stored OAuth tokens (see codex.json /
  // omni_code.codex_auth) and talks the Responses API to the Codex backend.
  type: 'openai' | 'azure' | 'openai-compatible' | 'litellm' | 'openai-oauth';
  api_key?: string;
  base_url?: string;
  api_version?: string;
  routing_prefix?: string;
  use_responses?: boolean;
  use_responses_websocket?: boolean;
  models: Record<string, ModelEntry>;
};

export type ModelsConfig = {
  version: 3;
  provider_scope_version?: 1;
  default: string | null;
  voice_default: string | null;
  providers: Record<string, ProviderEntry>;
};

/**
 * Credential probe for `util:validate-provider`. `kind` is the user-facing
 * provider identity (what onboarding asked), not the runtime provider type —
 * `anthropic` maps to a `litellm` ProviderEntry, `ollama` to
 * `openai-compatible`. Probes are free GETs against the provider's model
 * listing endpoint; they never spend tokens.
 */
export type ProviderProbe = {
  kind: 'openai' | 'anthropic' | 'openai-compatible' | 'ollama';
  apiKey?: string;
  /** Required for `openai-compatible` and `ollama`. */
  baseUrl?: string;
};

export type ProviderProbeResult =
  | { ok: true; models: string[] }
  | { ok: false; code: 'unauthorized' | 'network' | 'not-found' | 'unknown'; detail: string };

export type McpServerEntry = {
  type?: 'stdio' | 'sse' | 'http' | 'streamable_http';
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
};

export type McpConfig = {
  mcpServers: Record<string, McpServerEntry>;
};

/**
 * Sandbox egress policy (`network.json`). `enabled` turns the iptables
 * allowlist on; `allowlist` is the host/CIDR list (presets expand into it).
 * Enforced by omni serve at container bring-up — these two fields are the
 * entire consumed surface.
 */
export type NetworkConfig = {
  enabled: boolean;
  presets: string[];
  allowlist: string[];
};

// #endregion
