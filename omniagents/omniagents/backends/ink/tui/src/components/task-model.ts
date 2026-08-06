// Presentation logic for the docked task list, kept out of the component so
// it is unit-testable without an Ink render.
//
// The status set is CLOSED and must stay exhaustive. When the server grew a
// fourth step status (`blocked`, tkt_FqUypyxF2tzC) the previous shape —
// `active.filter(in_progress)` ++ `active.filter(pending)` ++ `completed`
// for the order, and `Record<'pending'|'in_progress'|'completed', string>`
// for the glyph and colour — silently DROPPED blocked steps from the dock
// and rendered `undefined` for their colour. A task the agent flagged as
// blocked is the single most important row in the panel, so the ordering is
// derived from one exhaustive list here and the maps are keyed by it.

import type { TaskStatus, TaskSummary } from "../store/appStore.js";
import type { PlanStep } from "../types/conversation.js";
import { Theme } from "../theme.js";

export const TASK_STATUSES: readonly TaskStatus[] = [
  "in_progress",
  "blocked",
  "pending",
  "completed",
] as const;

export const STATUS_GLYPH: Record<TaskStatus, string> = {
  in_progress: "◐",
  blocked: "◇",
  pending: "○",
  completed: "✔",
};

export const STATUS_COLOR: Record<TaskStatus, string> = {
  in_progress: Theme.stateActive,
  blocked: Theme.stateBlocked,
  pending: Theme.textChrome,
  completed: Theme.stateDone,
};

/** Tasks in dock order: active work first, finished work last. */
export function orderTasks(tasks: TaskSummary[]): TaskSummary[] {
  const ordered: TaskSummary[] = [];
  for (const status of TASK_STATUSES) {
    for (const task of tasks) {
      if (task.status === status) ordered.push(task);
    }
  }
  // Anything carrying a status this build does not know about still
  // belongs in the dock — dropping it is what the bug was.
  for (const task of tasks) {
    if (!ordered.includes(task)) ordered.push(task);
  }
  return ordered;
}

/**
 * Project durable plan steps (get_plan / `plan` item content) onto the
 * dock's TaskSummary shape. The statuses are the same closed set; the
 * field names differ (snake_case wire → camelCase dock). Tolerant of a
 * malformed payload: returns null so the caller keeps the current dock
 * instead of blanking it.
 */
export function planStepsToTasks(steps: unknown): TaskSummary[] | null {
  if (!Array.isArray(steps)) return null;
  const out: TaskSummary[] = [];
  for (const raw of steps) {
    const step = raw as Partial<PlanStep>;
    if (typeof step?.id !== "string" || typeof step?.subject !== "string") {
      return null;
    }
    out.push({
      id: step.id,
      subject: step.subject,
      activeForm:
        typeof step.active_form === "string" ? step.active_form : undefined,
      status: (step.status as TaskStatus) ?? "pending",
      owner: typeof step.owner === "string" ? step.owner : undefined,
      blockedBy: Array.isArray(step.blocked_by)
        ? step.blocked_by.filter((id): id is string => typeof id === "string")
        : undefined,
    });
  }
  return out;
}

export function taskCounts(tasks: TaskSummary[]): Record<TaskStatus, number> {
  const counts: Record<TaskStatus, number> = {
    in_progress: 0,
    blocked: 0,
    pending: 0,
    completed: 0,
  };
  for (const task of tasks) {
    if (task.status in counts) counts[task.status] += 1;
  }
  return counts;
}
