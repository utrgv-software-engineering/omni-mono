// Presentation logic for the ActivityStrip — the one-line ambient
// summary that replaced the stacked Goal/Wakeup/Loop/Tasks/Workers/
// BashJobs/QueuedMessages/Notifications dock panels. Pure and
// unit-testable (same split as ./task-model).

import type {
  BashJobSummary,
  GoalSnapshot,
  LoopTaskSnapshot,
  QueuedMessage,
  TaskSummary,
  WakeupSnapshot,
  SubagentSummary,
} from "../store/appStore.js";
import type { ConversationRunDiff } from "../types/conversation.js";

export type StripTone = "info" | "warn" | "dim" | "success";
export type StripToken = { text: string; tone: StripTone };

export interface StripState {
  tasks: TaskSummary[];
  subagents: SubagentSummary[];
  bashJobs: BashJobSummary[];
  queuedMessages: QueuedMessage[];
  notificationCount: number;
  goal: GoalSnapshot | null;
  wakeup: WakeupSnapshot | null;
  loops: LoopTaskSnapshot[];
  runDiff: ConversationRunDiff | null;
}

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function short(text: string, max = 24): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}

function interval(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

/**
 * One token per fact worth ambient space. Attention states (blocked
 * tasks, failed workers/jobs) get their own warn-toned token so a
 * quietly-failed background job is visible without opening anything —
 * the exact signal the old dock hid.
 */
export function buildStripTokens(state: StripState): StripToken[] {
  const tokens: StripToken[] = [];

  const total = state.tasks.length;
  if (total > 0) {
    const done = state.tasks.filter((t) => t.status === "completed").length;
    tokens.push({ text: `tasks ${done}/${total}`, tone: "info" });
    const blocked = state.tasks.filter((t) => t.status === "blocked").length;
    if (blocked > 0) {
      tokens.push({ text: `${blocked} blocked`, tone: "warn" });
    }
  }

  const runningWorkers = state.subagents.filter(
    (w) => w.kind === "worker" && w.status === "running",
  ).length;
  if (runningWorkers > 0) {
    tokens.push({ text: plural(runningWorkers, "worker"), tone: "info" });
  }
  const failedWorkers = state.subagents.filter(
    (w) => w.kind === "worker" && w.status === "error",
  ).length;
  if (failedWorkers > 0) {
    tokens.push({
      text: `${plural(failedWorkers, "worker")} failed`,
      tone: "warn",
    });
  }

  const runningJobs = state.bashJobs.filter((j) => j.running).length;
  if (runningJobs > 0) {
    tokens.push({ text: plural(runningJobs, "job"), tone: "info" });
  }
  const failedJobs = state.bashJobs.filter(
    (j) => !j.running && j.exit_code !== 0 && j.exit_code !== null,
  ).length;
  if (failedJobs > 0) {
    tokens.push({ text: `${plural(failedJobs, "job")} failed`, tone: "warn" });
  }

  if (state.queuedMessages.length > 0) {
    tokens.push({
      text: `${state.queuedMessages.length} queued`,
      tone: "dim",
    });
  }
  if (state.notificationCount > 0) {
    tokens.push({
      text: plural(state.notificationCount, "note"),
      tone: "dim",
    });
  }

  if (state.goal && (state.goal.status === "active" || state.goal.status === "paused")) {
    const suffix = state.goal.status === "paused" ? " (paused)" : "";
    tokens.push({ text: `goal ${short(state.goal.goal)}${suffix}`, tone: "dim" });
  }
  if (state.wakeup && state.wakeup.status === "active") {
    tokens.push({
      text: `wake ${interval(state.wakeup.interval_seconds)}`,
      tone: "dim",
    });
  }
  const activeLoops = state.loops.filter((l) => l.status === "active").length;
  if (activeLoops > 0) {
    tokens.push({ text: plural(activeLoops, "loop"), tone: "dim" });
  }

  return tokens;
}

export type MenuPanelId =
  | "tasks"
  | "workers"
  | "queue"
  | "jobs"
  | "notifications"
  | "git"
  | "terminal"
  | "models"
  | "commands";

export type PanelMenuRow = {
  /** Mnemonic key that opens the panel from the menu. */
  key: string;
  panel: MenuPanelId;
  label: string;
  /** Live summary — the same facts the strip shows, per panel. */
  summary: string | null;
  /** Something in this panel needs attention; sorts to the top. */
  warn: boolean;
};

/**
 * Rows for the which-key panel menu (ctrl+a). Every panel is always
 * listed — the menu doubles as the discoverability surface for the
 * direct chords — with live summaries, and panels in an attention state
 * sort first so "show me the problem" is always the first key listed.
 */
export function buildPanelMenuRows(state: StripState): PanelMenuRow[] {
  const total = state.tasks.length;
  const doneTasks = state.tasks.filter((t) => t.status === "completed").length;
  const blocked = state.tasks.filter((t) => t.status === "blocked").length;
  const runningWorkers = state.subagents.filter(
    (w) => w.kind === "worker" && w.status === "running",
  ).length;
  const failedWorkers = state.subagents.filter(
    (w) => w.kind === "worker" && w.status === "error",
  ).length;
  const runningJobs = state.bashJobs.filter((j) => j.running).length;
  const failedJobs = state.bashJobs.filter(
    (j) => !j.running && j.exit_code !== 0 && j.exit_code !== null,
  ).length;

  const join = (parts: Array<string | null>): string | null => {
    const kept = parts.filter((p): p is string => p !== null);
    return kept.length > 0 ? kept.join(" · ") : null;
  };

  const rows: PanelMenuRow[] = [
    {
      key: "t",
      panel: "tasks",
      label: "tasks",
      summary: join([
        total > 0 ? `${doneTasks}/${total}` : null,
        blocked > 0 ? `${blocked} blocked` : null,
      ]),
      warn: blocked > 0,
    },
    {
      key: "w",
      panel: "workers",
      label: "workers",
      summary: join([
        runningWorkers > 0 ? `${runningWorkers} running` : null,
        failedWorkers > 0 ? `${failedWorkers} failed` : null,
      ]),
      warn: failedWorkers > 0,
    },
    {
      key: "q",
      panel: "queue",
      label: "queue",
      summary:
        state.queuedMessages.length > 0
          ? `${state.queuedMessages.length} queued`
          : null,
      warn: false,
    },
    {
      key: "j",
      panel: "jobs",
      label: "jobs",
      summary: join([
        runningJobs > 0 ? `${runningJobs} running` : null,
        failedJobs > 0 ? `${failedJobs} failed` : null,
      ]),
      warn: failedJobs > 0,
    },
    {
      key: "n",
      panel: "notifications",
      label: "notes",
      summary:
        state.notificationCount > 0
          ? plural(state.notificationCount, "note")
          : null,
      warn: false,
    },
    {
      key: "g",
      panel: "git",
      label: "git",
      summary: state.runDiff
        ? `${plural(state.runDiff.stats.files_changed, "file")} edited +${state.runDiff.stats.additions} −${state.runDiff.stats.deletions}`
        : null,
      warn: false,
    },
    {
      key: "s",
      panel: "terminal",
      label: "shell",
      summary: null,
      warn: false,
    },
    { key: "m", panel: "models", label: "models", summary: null, warn: false },
    {
      key: "c",
      panel: "commands",
      label: "commands",
      summary: null,
      warn: false,
    },
  ];

  return [...rows].sort((a, b) => Number(b.warn) - Number(a.warn));
}
