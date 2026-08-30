/**
 * Atomic state management with Zustand + Immer
 * Single source of truth to prevent render cascades
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { immer } from "zustand/middleware/immer";
import { enableMapSet } from "immer";
import { debugLog } from "../utils/debugLog.js";
import {
  upsertArtifact,
  type ArtifactEntry,
} from "../components/artifact-model.js";
import type {
  ConversationItem,
  ConversationRunDiff,
} from "../types/conversation.js";
import { mergeItems } from "./conversation.js";

// Opt into Immer's Map/Set support so producers can mutate `Set` fields
// (e.g. mintedSessionIds) directly. Safe to call repeatedly.
enableMapSet();

export interface ToolCallInfo {
  tool: string;
  input: string;
  call_id: string;
  /** Server-side ConversationToolKind, when hydrated from the item model. */
  kind?: string;
  output?: string;
  metadata?: Record<string, unknown>;
  running: boolean;
}

export interface ApprovalInfo {
  call_id: string;
  tool: string;
  arguments: string;
  metadata?: Record<string, unknown>;
  session_id?: string;
  run_id?: string;
}

// Hosted-MCP approval prompts mirror ApprovalInfo but are keyed by
// request_id (from the model's McpApprovalRequest) and identify the
// hosted MCP server via server_label. No "always" affordance — the SDK
// has no equivalent of the per-session function-tool always-allow set on
// the MCP path.
export interface McpApprovalInfo {
  request_id: string;
  server_label: string;
  tool: string;
  arguments: string;
  session_id?: string;
  run_id?: string;
}

// Escalation prompts surface when the agent calls the ``escalate``
// builtin (a blocking client-function tool). The TUI shows the banner,
// the user's next submitted message becomes the tool's reply, and the
// banner clears. ``request_id`` lets the input-submit path route the
// reply via ``client_response`` instead of starting a new run.
export interface EscalationInfo {
  request_id: string;
  message: string;
  session_id?: string;
  run_id?: string;
}

// Structured user-elicitation prompts (elicitation_requested — the
// non-security input lane, protocol.md § "User Elicitation Flow").
// One prompt is shown at a time; ``question`` renders as a banner whose
// next user submit becomes the answer (the escalation idiom), while
// confirm/select/url render as interactive dialogs. Kinds the TUI cannot
// render surface a typed-decline dialog. Dismissed everywhere by
// elicitation_resolved (first responder wins across clients).
export interface ElicitationInfo {
  elicitation_id: string;
  kind: string;
  message: string;
  title?: string;
  input_schema?: Record<string, unknown>;
  options?: unknown[];
  url?: string;
  session_id?: string;
  run_id?: string;
  expires_at?: string;
}

// Notifications accumulate in a docked panel between user submissions.
// The agent's ``notify`` calls during a run pile up here so a user
// returning to a long-running session can see every heads-up they
// missed at a glance. The panel clears when the user starts the next
// run (handleSubmit), so each "user → run → user" cycle gets a fresh
// notification window.
export interface NotificationInfo {
  id: string;
  message: string;
  timestamp: number;
}

/** Agent-produced artifacts (`ui.add_artifact`), newest last. */
export type { ArtifactEntry } from "../components/artifact-model.js";

/**
 * A reviewer's verdict on a tool call or a plan-step completion.
 *
 * Its own message kind, not a `system` message: the transcript folds
 * consecutive tool calls into one beat and ANY non-tool message flushes
 * that span. Under "Approve for me" there is a review per tool call, so
 * as system messages these would flush every span and permanently
 * disable the aggregation the beat renderer exists to do.
 */
export interface ReviewInfo {
  /** `guardian` blocked or allowed a call; `workflow` judged a step. */
  kind: "guardian" | "workflow";
  /** Something was stopped. Blocking reviews break out of the span. */
  blocking: boolean;
  /** One-line summary, already worded. */
  line: string;
  /** Why — shown only on a broken-out row, where there is room. */
  rationale?: string;
}

export interface Message {
  role:
    | "user"
    | "assistant"
    | "system"
    | "tool_call"
    | "approval_request"
    | "review";
  content: string;
  timestamp?: Date;
  toolCall?: ToolCallInfo;
  approval?: ApprovalInfo;
  review?: ReviewInfo;
  /** A run-end diff receipt — rendered as a card, `content` is the
   * plain-text fallback. */
  runDiff?: ConversationRunDiff;
}

export interface UsageInfo {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  model?: string;
  maxInputTokens?: number;
  maxOutputTokens?: number;
}

/** Closed set, mirroring the server's plan step statuses. ``blocked`` is
 * set by the agent, never inferred from the ``blockedBy`` edge list — see
 * protocol.md § Durable Structured Plans. */
export type TaskStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "blocked";

export interface TaskSummary {
  id: string;
  subject: string;
  activeForm?: string;
  status: TaskStatus;
  owner?: string;
  blockedBy?: string[];
}

export interface BashJobSummary {
  job_id: string;
  pid: number;
  command: string;
  running: boolean;
  exit_code: number | null;
  wall_time_ms: number;
  started_at?: number;
  log_path?: string;
  cwd?: string;
}

// One entry in the unified subagent snapshot (ui.subagents.update):
// background workers (WorkerManager) and agent-tool runs (Agent.as_tool
// streamed onto the subagent bus) share this shape, discriminated by
// `kind`. Empty list means nothing is tracked for this session.
const WORKER_EVENT_CAP = 200;

/** One relayed subagent transcript event (ui.subagent.event). */
export interface SubagentEvent {
  method: string;
  params: Record<string, unknown>;
}

export interface SubagentSummary {
  subagent_id: string;
  kind: "worker" | "agent_tool";
  /** Agent name, for agent_tool runs. */
  agent?: string;
  status: "running" | "completed" | "cancelled" | "error";
  task: string;
  parent_session_id: string | null;
  session_id: string;
  run_id: string;
  result: string | null;
  error: string | null;
  isolation: string | null;
  started_at: number | null;
  finished_at: number | null;
  wall_time_ms: number | null;
}

// Mirror of QueuedItem.to_dict() on the server. Drives the "Up next"
// dock; items appear when a user submits while a run is active and
// disappear when the drainer pops them (or the user cancels).
export interface QueuedMessage {
  id: string;
  content: string;
  role: string;
  trigger_run: boolean;
  variables: Record<string, unknown> | null;
  safe_tool_overrides: Record<string, unknown> | null;
  source: string | null;
  enqueued_at: number;
}

// Mirror of goal_snapshot_dict on the server (omni-code
// server_functions/goal.py). Drives the docked goal panel; null means
// no goal is set on this session (panel renders nothing). "paused" is
// a non-terminal hold state — the periodic tick is off but the goal
// can be resumed via /goal.resume.
export interface GoalSnapshot {
  goal: string;
  turn: number;
  max_turns: number;
  tick_interval: number;
  last_reason: string | null;
  status: "active" | "completed" | "cancelled" | "paused";
  started_at: number;
  completion_reason: string | null;
  // Auditable artifact attached on terminal states (achieved/blocked).
  // Cited by the agent when calling goal_complete.
  evidence?: string | null;
}

// Mirror of wakeup_snapshot_dict on the server (omni-code
// server_functions/wakeup.py). Drives the docked wakeup panel; null
// means no schedule is active on this session (panel renders nothing).
// The server emits a final snapshot=null on cancel/exhaustion, so the
// panel disappears server-driven — no client-side dismissal needed.
export interface WakeupSnapshot {
  message: string;
  interval_seconds: number;
  recurring: boolean;
  end_at: number | null; // unix timestamp (exclusive bound)
  end_after: number | null; // remaining fire count
  fires: number;
  started_at: number;
  status: "active" | "cancelled";
}

export interface LoopTaskSnapshot {
  id: string;
  prompt: string;
  mode: "dynamic" | "fixed_interval" | "fixed_cron";
  cron: string | null;
  interval_seconds: number | null;
  recurring: boolean;
  created_at: number;
  expires_at: number | null;
  next_fire_at: number | null;
  fires: number;
  status: "active" | "cancelled" | "expired" | "completed";
  last_fire_at: number | null;
  last_reason: string | null;
  jitter_seconds: number;
}

/**
 * What the initialize handshake negotiated — the feature-detection
 * surface for protocol-gated UI. `grantedExperimental` is the server's
 * echoed grant list (experimental requests AND notifications); panels
 * gate on membership instead of try/catching -32013. `agentHost` routes
 * environment-scoped operations (fs_*, git_*, server_call read leases).
 */
export interface ServerInitInfo {
  protocolVersion: string;
  grantedExperimental: string[];
  agentHost: {
    agentHostId: string;
    defaultWorkspaceId: string | null;
    defaultEnvironmentId: string | null;
  };
}

interface AppState {
  // Data
  messages: Message[];
  // Canonical conversation item cache for the active session, keyed by
  // item_id inside mergeConversationItems. Hydrated from list_items and
  // kept current by item_updated notifications; the transcript is
  // re-derived from it via normalizeItems whenever no local run is
  // streaming (during a run the live event stream drives `messages`).
  conversationItems: ConversationItem[];
  // Latest aggregated run diff (durable `run_diff` item / get_run_diff).
  // Cleared on run_started like the recap.
  runDiff: ConversationRunDiff | null;
  // Live subagent mini-transcripts: ui.subagent.event relays keyed by
  // worker_id, ring-capped so a chatty worker can't grow unbounded.
  subagentEvents: Record<string, SubagentEvent[]>;
  // Ctrl+O: render the transcript's activity beats expanded into full
  // per-tool rows (the mechanical view) instead of semantic one-liners.
  transcriptDetail: boolean;
  // The active screen's context line for the status bar (vim-statusline
  // style): review publishes "review · turn · repo", agents publishes
  // "agents"; null on chat, where mission tokens own the bar.
  statusContext: string | null;
  usage: UsageInfo | null;
  sessionModel: string | null;
  sessionReasoningEffort: string | null;
  sessionId: string | undefined;
  // sessionIds that were created locally via a first sendMessage response
  // (as opposed to resumed via the picker / supplied at launch). Consumed
  // exactly once by useMessagesNew before the history-load fetch — see
  // markSessionAsMinted / consumeMintedSession below.
  mintedSessionIds: Set<string>;

  // UI State
  thinking: boolean;
  status: string | null;
  statusType: "info" | "warning" | "error" | "success" | "progress" | null;
  statusSpinner: boolean;
  approval: ApprovalInfo | null;
  mcpApproval: McpApprovalInfo | null;
  escalation: EscalationInfo | null;
  elicitation: ElicitationInfo | null;
  notifications: NotificationInfo[];
  artifacts: ArtifactEntry[];
  currentRunId: string | null;
  preamble: string | null;
  tasks: TaskSummary[];
  bashJobs: BashJobSummary[];
  subagents: SubagentSummary[];
  queuedMessages: QueuedMessage[];
  goal: GoalSnapshot | null;
  wakeup: WakeupSnapshot | null;
  loops: LoopTaskSnapshot[];
  // Last successful handshake; null until the first rpc_ready. Kept
  // across reconnects (each rpc_ready overwrites it).
  serverInit: ServerInitInfo | null;
  // IDs of dock items the user has "moved past" — snapshotted from
  // currently-terminal items each time the user submits, so the next
  // run starts with a clean dock instead of dragging the previous
  // run's exits forward. New items spawned by the next run aren't in
  // these sets and render normally; their IDs are added on the *next*
  // submit. Reset on session change.
  dismissedSubagentIds: Set<string>;
  dismissedJobIds: Set<string>;
  dismissedTaskIds: Set<string>;

  // Actions (ONLY way to update state)
  actions: {
    addMessage: (msg: Message) => void;
    setMessages: (msgs: Message[]) => void;
    setConversationItems: (items: ConversationItem[]) => void;
    // Revision-aware merge (see store/conversation.ts mergeItems): an
    // out-of-order arrival can never regress an item that advanced.
    mergeConversationItems: (incoming: ConversationItem[]) => void;
    setRunDiff: (diff: ConversationRunDiff | null) => void;
    addSubagentEvent: (workerId: string, event: SubagentEvent) => void;
    toggleTranscriptDetail: () => void;
    setStatusContext: (context: string | null) => void;
    updateToolResult: (
      callId: string,
      output: string,
      metadata?: Record<string, unknown>,
    ) => void;
    setThinking: (thinking: boolean, status?: string | null) => void;
    setStatus: (
      status: string | null,
      type?: "info" | "warning" | "error" | "success" | "progress" | null,
      showSpinner?: boolean,
    ) => void;
    setApproval: (approval: ApprovalInfo | null) => void;
    setMcpApproval: (approval: McpApprovalInfo | null) => void;
    setEscalation: (escalation: EscalationInfo | null) => void;
    setElicitation: (elicitation: ElicitationInfo | null) => void;
    addNotification: (message: string) => void;
    removeNotification: (id: string) => void;
    clearNotifications: () => void;
    /** Upsert an artifact: same `artifactId` replaces, else appends. */
    addArtifact: (entry: ArtifactEntry) => void;
    clearArtifacts: () => void;
    setUsage: (usage: UsageInfo) => void;
    setSessionState: (state: {
      model?: string | null;
      label?: string | null;
      provider?: string | null;
      reasoning_effort?: string | null;
    }) => void;
    setSessionId: (sessionId: string | undefined) => void;
    // Tag `id` as having been minted by a local first-send response. The
    // history-load effect treats local optimistic state as canonical for
    // minted ids and skips the server fetch (which would race against the
    // server persisting the user message and could clobber it with []).
    markSessionAsMinted: (id: string) => void;
    // Returns true and clears the mark if `id` was previously marked as
    // minted; returns false otherwise. Idempotent / single-shot per id.
    consumeMintedSession: (id: string) => boolean;
    setCurrentRunId: (runId: string | null) => void;
    setPreamble: (preamble: string | null) => void;
    cancelRun: (runId: string) => void;
    removeApprovalMessage: () => void;
    clearMessages: () => void;
    setTasks: (tasks: TaskSummary[]) => void;
    setBashJobs: (jobs: BashJobSummary[]) => void;
    setSubagents: (subagents: SubagentSummary[]) => void;
    setQueuedMessages: (items: QueuedMessage[]) => void;
    setGoal: (goal: GoalSnapshot | null) => void;
    setWakeup: (wakeup: WakeupSnapshot | null) => void;
    setLoops: (loops: LoopTaskSnapshot[]) => void;
    setServerInit: (init: ServerInitInfo | null) => void;
    // Add every currently-terminal dock item (exited workers / bash
    // jobs, completed tasks) to its dismissed set. Call this at the
    // start of handleSubmit so the next run shows a clean dock.
    dismissDockTerminals: () => void;
    // Wipe all three dismissed sets — used on session change so the
    // freshly bound session starts with full server-side state.
    resetDockDismissals: () => void;
  };
}

export const useAppStore = create<AppState>()(
  immer((set, get) => ({
    // Initial state
    messages: [],
    conversationItems: [],
    runDiff: null,
    subagentEvents: {},
    transcriptDetail: false,
    statusContext: null,
    usage: null,
    sessionModel: null,
    sessionReasoningEffort: null,
    sessionId: undefined,
    mintedSessionIds: new Set<string>(),
    thinking: false,
    status: null,
    statusType: null,
    statusSpinner: false,
    approval: null,
    mcpApproval: null,
    escalation: null,
    elicitation: null,
    notifications: [],
    artifacts: [],
    currentRunId: null,
    preamble: null,
    tasks: [],
    bashJobs: [],
    subagents: [],
    dismissedSubagentIds: new Set<string>(),
    dismissedJobIds: new Set<string>(),
    dismissedTaskIds: new Set<string>(),
    queuedMessages: [],
    goal: null,
    wakeup: null,
    loops: [],
    serverInit: null,

    actions: {
      addMessage: (msg) =>
        set((state) => {
          state.messages.push(msg);

          // Update related state atomically
          if (msg.role === "user") {
            state.thinking = true;
            state.status = null;
          }
        }),

      setMessages: (msgs) =>
        set((state) => {
          state.messages = msgs;
        }),

      setConversationItems: (items) =>
        set((state) => {
          state.conversationItems = items;
        }),

      mergeConversationItems: (incoming) =>
        set((state) => {
          state.conversationItems = mergeItems(
            state.conversationItems as ConversationItem[],
            incoming,
          );
        }),

      setRunDiff: (diff) =>
        set((state) => {
          state.runDiff = diff;
        }),

      addSubagentEvent: (workerId, event) =>
        set((state) => {
          const buffer = state.subagentEvents[workerId] ?? [];
          buffer.push(event);
          if (buffer.length > WORKER_EVENT_CAP) {
            buffer.splice(0, buffer.length - WORKER_EVENT_CAP);
          }
          state.subagentEvents[workerId] = buffer;
        }),

      toggleTranscriptDetail: () =>
        set((state) => {
          state.transcriptDetail = !state.transcriptDetail;
        }),

      setStatusContext: (context) =>
        set((state) => {
          state.statusContext = context;
        }),

      updateToolResult: (callId, output, metadata) =>
        set((state) => {
          // Immer makes this mutation safe and efficient
          const msg = state.messages.find(
            (m) => m.toolCall?.call_id === callId,
          );
          if (msg?.toolCall) {
            msg.toolCall.output = output;
            msg.toolCall.metadata = metadata;
            msg.toolCall.running = false;
          }
        }),

      setThinking: (thinking, status) =>
        set((state) => {
          // Only update if actually changing to prevent unnecessary renders
          const currentThinking = state.thinking;
          const currentStatus = state.status;

          if (currentThinking !== thinking) {
            debugLog(
              `setThinking: ${currentThinking} -> ${thinking}, status: ${status ?? "null"}`,
            );
            state.thinking = thinking;
          }

          if (status !== undefined && currentStatus !== status) {
            state.status = status;
            if (status) {
              const s = status.toLowerCase();
              if (s.startsWith("error:")) {
                state.statusType = "error";
              } else if (s.startsWith("retrying")) {
                state.statusType = "progress";
              } else {
                state.statusType = null;
              }
            } else {
              state.statusType = null;
            }
            state.statusSpinner = thinking;
          }
        }),

      setStatus: (status, type, showSpinner) =>
        set((state) => {
          state.status = status ?? null;
          state.statusType = type ?? null;
          if (typeof showSpinner === "boolean") {
            state.statusSpinner = showSpinner;
          }
        }),

      setApproval: (approval) =>
        set((state) => {
          state.approval = approval;
          // When approval appears, pause thinking indicator to prevent flicker
          if (approval) {
            debugLog(`setApproval: turning thinking OFF (approval shown)`);
            state.thinking = false;
            state.status = null;
            state.statusType = null;
            state.statusSpinner = false;
          }
        }),

      setMcpApproval: (approval) =>
        set((state) => {
          state.mcpApproval = approval;
          if (approval) {
            debugLog(`setMcpApproval: turning thinking OFF (approval shown)`);
            state.thinking = false;
            state.status = null;
            state.statusType = null;
            state.statusSpinner = false;
          }
        }),

      setEscalation: (escalation) =>
        set((state) => {
          state.escalation = escalation;
          // While an escalation is pending the agent is blocked on the
          // tool call awaiting our client_response, so the thinking
          // spinner is misleading — turn it off and let the banner speak
          // for itself.
          if (escalation) {
            state.thinking = false;
            state.status = null;
            state.statusType = null;
            state.statusSpinner = false;
          }
        }),

      setElicitation: (elicitation) =>
        set((state) => {
          state.elicitation = elicitation;
          // The agent is blocked on the prompt awaiting our
          // elicitation_response — same posture as approvals: stop the
          // thinking spinner and let the prompt surface speak.
          if (elicitation) {
            state.thinking = false;
            state.status = null;
            state.statusType = null;
            state.statusSpinner = false;
          }
        }),

      addNotification: (message) =>
        set((state) => {
          state.notifications.push({
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            message,
            timestamp: Date.now(),
          });
        }),

      removeNotification: (id) =>
        set((state) => {
          state.notifications = state.notifications.filter((n) => n.id !== id);
        }),

      clearNotifications: () =>
        set((state) => {
          if (state.notifications.length > 0) {
            state.notifications = [];
          }
        }),

      addArtifact: (entry) =>
        set((state) => {
          // The upsert rule (same id replaces and bumps revision) lives
          // in the model so it stays testable without a store.
          const next = upsertArtifact(state.artifacts, entry);
          if (next !== state.artifacts) {
            state.artifacts = next;
          }
        }),

      clearArtifacts: () =>
        set((state) => {
          if (state.artifacts.length > 0) {
            state.artifacts = [];
          }
        }),

      setUsage: (usage) =>
        set((state) => {
          // Only update if values actually changed
          const current = state.usage;
          if (
            !current ||
            current.totalTokens !== usage.totalTokens ||
            current.model !== usage.model ||
            current.maxInputTokens !== usage.maxInputTokens ||
            current.maxOutputTokens !== usage.maxOutputTokens
          ) {
            state.usage = usage;
          }
        }),

      setSessionState: (sessionState) =>
        set((state) => {
          const label = (sessionState.label ?? null) as string | null;
          const model = (sessionState.model ?? null) as string | null;
          state.sessionModel = label || model;
          state.sessionReasoningEffort = (sessionState.reasoning_effort ??
            null) as string | null;
        }),

      setSessionId: (sessionId) =>
        set((state) => {
          state.sessionId = sessionId;
        }),

      markSessionAsMinted: (id) =>
        set((state) => {
          state.mintedSessionIds.add(id);
        }),

      consumeMintedSession: (id) => {
        const had = get().mintedSessionIds.has(id);
        if (had) {
          set((state) => {
            state.mintedSessionIds.delete(id);
          });
        }
        return had;
      },

      setCurrentRunId: (runId) =>
        set((state) => {
          if (state.currentRunId !== runId) {
            state.currentRunId = runId;
          }
        }),

      cancelRun: () =>
        set((state) => {
          // Mark running tools as cancelled
          state.messages.forEach((msg) => {
            if (msg.toolCall?.running) {
              msg.toolCall.output = "[Cancelled]";
              msg.toolCall.running = false;
            }
          });

          debugLog(`cancelRun: turning thinking OFF (run cancelled)`);
          state.thinking = false;
          state.status = null;
          state.statusType = null;
          state.statusSpinner = false;
          state.currentRunId = null;
          state.preamble = null;

          // Add cancellation message
          state.messages.push({
            role: "assistant",
            content: "[Run cancelled by user]",
            timestamp: new Date(),
          });
        }),

      removeApprovalMessage: () =>
        set((state) => {
          state.messages = state.messages.filter(
            (msg) => msg.role !== "approval_request",
          );
        }),

      setPreamble: (preamble) =>
        set((state) => {
          if (state.preamble !== preamble) {
            state.preamble = preamble;
            if (preamble) {
              debugLog(
                `setPreamble: updated with content (${preamble.length} chars)`,
              );
            } else {
              debugLog(`setPreamble: cleared`);
            }
          }
        }),

      clearMessages: () =>
        set((state) => {
          debugLog(`clearMessages: turning thinking OFF (messages cleared)`);
          state.messages = [];
          state.thinking = false;
          state.status = null;
          state.statusType = null;
          state.statusSpinner = false;
          state.approval = null;
          state.mcpApproval = null;
          state.elicitation = null;
          state.preamble = null;
          state.tasks = [];
          state.bashJobs = [];
          state.conversationItems = [];
          state.runDiff = null;
        }),

      setTasks: (tasks) =>
        set((state) => {
          state.tasks = tasks;
        }),

      setBashJobs: (jobs) =>
        set((state) => {
          state.bashJobs = jobs;
        }),

      setSubagents: (subagents) =>
        set((state) => {
          state.subagents = subagents;
        }),

      setQueuedMessages: (items) =>
        set((state) => {
          state.queuedMessages = items;
        }),

      setGoal: (goal) =>
        set((state) => {
          state.goal = goal;
        }),

      setServerInit: (init) =>
        set((state) => {
          state.serverInit = init;
        }),
      setWakeup: (wakeup) =>
        set((state) => {
          state.wakeup = wakeup;
        }),

      setLoops: (loops) =>
        set((state) => {
          state.loops = loops;
        }),

      dismissDockTerminals: () =>
        set((state) => {
          // Only auto-dismiss unambiguous successes. Failures
          // (worker error/cancelled, non-zero or null bash exit) stick
          // until the user dismisses them so a quietly-failed background
          // task isn't lost behind the next prompt.
          for (const w of state.subagents) {
            if (w.status === "completed")
              state.dismissedSubagentIds.add(w.subagent_id);
          }
          for (const j of state.bashJobs) {
            if (!j.running && j.exit_code === 0)
              state.dismissedJobIds.add(j.job_id);
          }
          for (const t of state.tasks) {
            if (t.status === "completed") state.dismissedTaskIds.add(t.id);
          }
        }),

      resetDockDismissals: () =>
        set((state) => {
          state.dismissedSubagentIds = new Set<string>();
          state.dismissedJobIds = new Set<string>();
          state.dismissedTaskIds = new Set<string>();
        }),
    },
  })),
);

// Selectors to prevent unnecessary re-renders
export const useMessages = () => useAppStore((s) => s.messages);
export const useThinking = () => useAppStore((s) => s.thinking);
export const useStatus = () =>
  useAppStore(
    useShallow((s) => ({
      status: s.status,
      statusType: s.statusType,
      statusSpinner: s.statusSpinner,
    })),
  );
export const useApproval = () => useAppStore((s) => s.approval);
export const useMcpApproval = () => useAppStore((s) => s.mcpApproval);
export const useEscalation = () => useAppStore((s) => s.escalation);
export const useElicitation = () => useAppStore((s) => s.elicitation);
export const useNotifications = () => useAppStore((s) => s.notifications);
export const useArtifacts = () => useAppStore((s) => s.artifacts);
export const useUsage = () => useAppStore((s) => s.usage);
export const useSessionModel = () => useAppStore((s) => s.sessionModel);
export const useSessionReasoningEffort = () =>
  useAppStore((s) => s.sessionReasoningEffort);
export const useSessionId = () => useAppStore((s) => s.sessionId);
export const useCurrentRunId = () => useAppStore((s) => s.currentRunId);
export const usePreamble = () => useAppStore((s) => s.preamble);
export const useTasks = () => useAppStore((s) => s.tasks);
export const useBashJobs = () => useAppStore((s) => s.bashJobs);
export const useSubagents = () => useAppStore((s) => s.subagents);
export const useDismissedSubagentIds = () =>
  useAppStore((s) => s.dismissedSubagentIds);
export const useDismissedJobIds = () => useAppStore((s) => s.dismissedJobIds);
export const useDismissedTaskIds = () => useAppStore((s) => s.dismissedTaskIds);
export const useQueuedMessages = () => useAppStore((s) => s.queuedMessages);
export const useGoal = () => useAppStore((s) => s.goal);
export const useWakeup = () => useAppStore((s) => s.wakeup);
export const useLoops = () => useAppStore((s) => s.loops);
export const useRunDiff = () => useAppStore((s) => s.runDiff);
export const useSubagentEvents = () => useAppStore((s) => s.subagentEvents);
export const useStatusContext = () =>
  useAppStore((s) => s.statusContext);
export const useTranscriptDetail = () =>
  useAppStore((s) => s.transcriptDetail);
export const useServerInit = () => useAppStore((s) => s.serverInit);
/** Whether the server granted an experimental operation at the handshake. */
export const useIsGranted = (operation: string) =>
  useAppStore((s) => s.serverInit?.grantedExperimental.includes(operation) ?? false);
export const useActions = () => useAppStore((s) => s.actions);
