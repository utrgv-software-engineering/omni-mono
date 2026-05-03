/**
 * Atomic state management with Zustand + Immer
 * Single source of truth to prevent render cascades
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { immer } from "zustand/middleware/immer";
import { debugLog } from "../utils/debugLog.js";

export interface ToolCallInfo {
  tool: string;
  input: string;
  call_id: string;
  output?: string;
  metadata?: Record<string, unknown>;
  running: boolean;
}

export interface ApprovalInfo {
  approval_id: string;
  request_id: string;
  tool: string;
  arguments: string;
  metadata?: Record<string, unknown>;
  session_id?: string;
  run_id?: string;
}

export interface Message {
  role: "user" | "assistant" | "system" | "tool_call" | "approval_request";
  content: string;
  timestamp?: Date;
  toolCall?: ToolCallInfo;
  approval?: ApprovalInfo;
}

export interface UsageInfo {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  model?: string;
  maxInputTokens?: number;
  maxOutputTokens?: number;
}

export interface TaskSummary {
  id: string;
  subject: string;
  activeForm?: string;
  status: "pending" | "in_progress" | "completed";
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

interface AppState {
  // Data
  messages: Message[];
  usage: UsageInfo | null;
  sessionModel: string | null;
  sessionReasoningEffort: string | null;
  sessionId: string | undefined;

  // UI State
  thinking: boolean;
  status: string | null;
  statusType: "info" | "warning" | "error" | "success" | "progress" | null;
  statusSpinner: boolean;
  approval: ApprovalInfo | null;
  currentRunId: string | null;
  preamble: string | null;
  tasks: TaskSummary[];
  bashJobs: BashJobSummary[];

  // Actions (ONLY way to update state)
  actions: {
    addMessage: (msg: Message) => void;
    setMessages: (msgs: Message[]) => void;
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
    setUsage: (usage: UsageInfo) => void;
    setSessionState: (state: {
      model?: string | null;
      label?: string | null;
      provider?: string | null;
      reasoning_effort?: string | null;
    }) => void;
    setSessionId: (sessionId: string | undefined) => void;
    setCurrentRunId: (runId: string | null) => void;
    setPreamble: (preamble: string | null) => void;
    cancelRun: (runId: string) => void;
    removeApprovalMessage: () => void;
    clearMessages: () => void;
    setTasks: (tasks: TaskSummary[]) => void;
    setBashJobs: (jobs: BashJobSummary[]) => void;
  };
}

export const useAppStore = create<AppState>()(
  immer((set, get) => ({
    // Initial state
    messages: [],
    usage: null,
    sessionModel: null,
    sessionReasoningEffort: null,
    sessionId: undefined,
    thinking: false,
    status: null,
    statusType: null,
    statusSpinner: false,
    approval: null,
    currentRunId: null,
    preamble: null,
    tasks: [],
    bashJobs: [],

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
            debugLog(`setThinking: ${currentThinking} -> ${thinking}, status: ${status ?? "null"}`);
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
          state.sessionReasoningEffort =
            (sessionState.reasoning_effort ?? null) as string | null;
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

      cancelRun: (runId) =>
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
              debugLog(`setPreamble: updated with content (${preamble.length} chars)`);
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
          state.preamble = null;
          state.tasks = [];
          state.bashJobs = [];
        }),

      setTasks: (tasks) =>
        set((state) => {
          state.tasks = tasks;
        }),

      setBashJobs: (jobs) =>
        set((state) => {
          state.bashJobs = jobs;
        }),
    },
  })),
);

// Selectors to prevent unnecessary re-renders
export const useMessages = () => useAppStore((s) => s.messages);
export const useThinking = () => useAppStore((s) => s.thinking);
export const useStatus = () =>
  useAppStore(
    useShallow((s) => ({ status: s.status, statusType: s.statusType, statusSpinner: s.statusSpinner }))
  );
export const useApproval = () => useAppStore((s) => s.approval);
export const useUsage = () => useAppStore((s) => s.usage);
export const useSessionModel = () => useAppStore((s) => s.sessionModel);
export const useSessionReasoningEffort = () =>
  useAppStore((s) => s.sessionReasoningEffort);
export const useSessionId = () => useAppStore((s) => s.sessionId);
export const useCurrentRunId = () => useAppStore((s) => s.currentRunId);
export const usePreamble = () => useAppStore((s) => s.preamble);
export const useTasks = () => useAppStore((s) => s.tasks);
export const useBashJobs = () => useAppStore((s) => s.bashJobs);
export const useActions = () => useAppStore((s) => s.actions);
