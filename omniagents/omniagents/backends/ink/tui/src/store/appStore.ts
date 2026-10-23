/**
 * Atomic state management with Zustand + Immer
 * Single source of truth to prevent render cascades
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { immer } from "zustand/middleware/immer";
import { enableMapSet } from "immer";
import { debugLog } from "../utils/debugLog.js";
import type { QueuedMessage } from "@omniagents/client/rpc/client";
import type {
  BashJobSummary,
  SubagentSummary,
} from "@omniagents/client/transcript/activity-store";
import type { TaskSummary } from "@omniagents/client/transcript/canonical-plan-tasks";
import type { RunDiffItem } from "@omniagents/client/transcript/chat-types";
import type {
  EscalationInfo,
  GoalSnapshot,
  LoopTaskSnapshot,
  NotificationInfo,
  WakeupSnapshot,
} from "@omniagents/client/session/panel-types";
import type { TokenUsage } from "@omniagents/client/session/session-panels";
import type { ArtifactEntry } from "../components/artifact-model.js";

// Opt into Immer's Map/Set support so producers can mutate `Set` fields
// (the dismissed-dock sets) directly. Safe to call repeatedly.
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
    | "command"
    | "tool_call"
    | "approval_request"
    | "review";
  content: string;
  /** For `command` rows: the verb that produced `content`, e.g. "status". */
  command?: string;
  timestamp?: Date;
  toolCall?: ToolCallInfo;
  approval?: ApprovalInfo;
  review?: ReviewInfo;
  /** A run-end diff receipt — rendered as a card, `content` is the
   * plain-text fallback. */
  runDiff?: RunDiffItem;
}

// One entry in the unified subagent snapshot (ui.subagents.update):
// background workers (WorkerManager) and agent-tool runs (Agent.as_tool
// streamed onto the subagent bus) share this shape, discriminated by
// `kind`. Empty list means nothing is tracked for this session.

// Mirror of QueuedItem.to_dict() on the server. Drives the "Up next"
// dock; items appear when a user submits while a run is active and
// disappear when the drainer pops them (or the user cancels).

// Mirror of goal_snapshot_dict on the server (omni-code
// server_functions/goal.py). Drives the docked goal panel; null means
// no goal is set on this session (panel renders nothing). "paused" is
// a non-terminal hold state — the periodic tick is off but the goal
// can be resumed via /goal.resume.

// Mirror of wakeup_snapshot_dict on the server (omni-code
// server_functions/wakeup.py). Drives the docked wakeup panel; null
// means no schedule is active on this session (panel renders nothing).
// The server emits a final snapshot=null on cancel/exhaustion, so the
// panel disappears server-driven — no client-side dismissal needed.

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
  // The conversation's latest aggregated run diff (its last `run_diff` item).
  runDiff: RunDiffItem | null;
  // Ctrl+O: render the transcript's activity beats expanded into full
  // per-tool rows (the mechanical view) instead of semantic one-liners.
  transcriptDetail: boolean;
  // The active screen's context line for the status bar (vim-statusline
  // style): review publishes "review · turn · repo", agents publishes
  // "agents"; null on chat, where mission tokens own the bar.
  statusContext: string | null;
  usage: TokenUsage | null;
  sessionModel: string | null;
  sessionReasoningEffort: string | null;
  /** The open conversation. A new chat has an id before its first message. */
  sessionId: string;

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
  /** Incremented on every palette change. */
  themeVersion: number;
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
    setMessages: (msgs: Message[]) => void;
    setRunDiff: (diff: RunDiffItem | null) => void;
    toggleTranscriptDetail: () => void;
    setStatusContext: (context: string | null) => void;
    setThinking: (thinking: boolean) => void;
    /** Bumped when the palette changes; `themedMemo` re-renders on it. */
    bumpThemeVersion: () => void;
    setStatus: (
      status: string | null,
      type?: "info" | "warning" | "error" | "success" | "progress" | null,
      showSpinner?: boolean,
    ) => void;
    setApproval: (approval: ApprovalInfo | null) => void;
    setMcpApproval: (approval: McpApprovalInfo | null) => void;
    setEscalation: (escalation: EscalationInfo | null) => void;
    setElicitation: (elicitation: ElicitationInfo | null) => void;
    setNotifications: (notifications: NotificationInfo[]) => void;
    setArtifacts: (artifacts: ArtifactEntry[]) => void;
    setUsage: (usage: TokenUsage) => void;
    setSessionState: (state: {
      model?: string | null;
      label?: string | null;
      provider?: string | null;
      reasoning_effort?: string | null;
    }) => void;
    setSessionId: (sessionId: string) => void;
    setCurrentRunId: (runId: string | null) => void;
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
  immer((set) => ({
    // Initial state
    messages: [],
    runDiff: null,
    transcriptDetail: false,
    statusContext: null,
    usage: null,
    sessionModel: null,
    sessionReasoningEffort: null,
    sessionId: "",
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
    themeVersion: 0,

    actions: {
      bumpThemeVersion: () =>
        set((state) => {
          state.themeVersion += 1;
        }),

      setMessages: (msgs) =>
        set((state) => {
          state.messages = msgs;
        }),

      setRunDiff: (diff) =>
        set((state) => {
          state.runDiff = diff;
        }),

      toggleTranscriptDetail: () =>
        set((state) => {
          state.transcriptDetail = !state.transcriptDetail;
        }),

      setStatusContext: (context) =>
        set((state) => {
          state.statusContext = context;
        }),

      setThinking: (thinking) =>
        set((state) => {
          if (state.thinking !== thinking) {
            debugLog(`setThinking: ${state.thinking} -> ${thinking}`);
            state.thinking = thinking;
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

      setNotifications: (notifications) =>
        set((state) => {
          state.notifications = notifications;
        }),

      setArtifacts: (artifacts) =>
        set((state) => {
          state.artifacts = artifacts;
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

      setCurrentRunId: (runId) =>
        set((state) => {
          if (state.currentRunId !== runId) {
            state.currentRunId = runId;
          }
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
export const useThemeVersion = () => useAppStore((s) => s.themeVersion);
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
export const useStatusContext = () => useAppStore((s) => s.statusContext);
export const useTranscriptDetail = () => useAppStore((s) => s.transcriptDetail);
export const useServerInit = () => useAppStore((s) => s.serverInit);
/** Whether the server granted an experimental operation at the handshake. */
export const useIsGranted = (operation: string) =>
  useAppStore(
    (s) => s.serverInit?.grantedExperimental.includes(operation) ?? false,
  );
export const useActions = () => useAppStore((s) => s.actions);
