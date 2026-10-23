// Presentation logic for the ActivityStrip — the one-line ambient
// summary that replaced the stacked Goal/Wakeup/Loop/Tasks/Workers/
// BashJobs/QueuedMessages/Notifications dock panels. Pure and
// unit-testable (same split as ./task-model).

import type {
  BashJobSummary,
  SubagentSummary,
} from "@omniagents/client/transcript/activity-store";
import type {
  GoalSnapshot,
  LoopTaskSnapshot,
  WakeupSnapshot,
} from "@omniagents/client/session/panel-types";
import type { QueuedMessage } from "@omniagents/client/rpc/client";
import type { TaskSummary } from "@omniagents/client/transcript/canonical-plan-tasks";
import type { RunDiffItem } from "@omniagents/client/transcript/chat-types";

export type StripTone = "info" | "warn" | "dim" | "success";
export type StripToken = { text: string; tone: StripTone };

export interface StripState {
  tasks: TaskSummary[];
  subagents: SubagentSummary[];
  bashJobs: BashJobSummary[];
  queuedMessages: QueuedMessage[];
  notificationCount: number;
  /** Artifacts the agent has emitted this session. */
  artifactCount: number;
  goal: GoalSnapshot | null;
  wakeup: WakeupSnapshot | null;
  loops: LoopTaskSnapshot[];
  runDiff: RunDiffItem | null;
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

  if (state.artifactCount > 0) {
    tokens.push({
      text: plural(state.artifactCount, "artifact"),
      tone: "info",
    });
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

  if (
    state.goal &&
    (state.goal.status === "active" || state.goal.status === "paused")
  ) {
    const suffix = state.goal.status === "paused" ? " (paused)" : "";
    tokens.push({
      text: `goal ${short(state.goal.goal)}${suffix}`,
      tone: "dim",
    });
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
