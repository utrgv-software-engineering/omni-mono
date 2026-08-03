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
import { TokyoNightTheme } from "../theme.js";

export const TASK_STATUSES: readonly TaskStatus[] = [
  "in_progress",
  "blocked",
  "pending",
  "completed",
] as const;

export const STATUS_GLYPH: Record<TaskStatus, string> = {
  in_progress: "◐",
  blocked: "⊘",
  pending: "○",
  completed: "●",
};

export const STATUS_COLOR: Record<TaskStatus, string> = {
  in_progress: TokyoNightTheme.info,
  blocked: TokyoNightTheme.warning,
  pending: TokyoNightTheme.defaultText,
  completed: TokyoNightTheme.success,
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
