// Presentation logic for the docked task panel, kept out of the component
// so it is unit-testable (the `conversation.ts` / `conversation.test.ts`
// pattern).
//
// The status set is CLOSED and must stay exhaustive. When the server grew a
// fourth step status (`blocked`, tkt_FqUypyxF2tzC) the previous shape —
// three hard-coded filters concatenated into `ordered`, and a
// `Record<'pending'|'in_progress'|'completed', string>` dot map — silently
// DROPPED blocked steps from the dock and rendered `undefined` for their
// colour. A task the agent flagged as blocked is the single most important
// row in the panel, so the ordering is derived from one exhaustive list
// here and the maps are keyed by it.

export const TASK_STATUSES = [
  'in_progress',
  'blocked',
  'pending',
  'completed',
] as const

export type TaskStatus = (typeof TASK_STATUSES)[number]

export type TaskSummary = {
  id: string
  subject: string
  activeForm?: string
  status: TaskStatus
  owner?: string
  blockedBy?: string[]
}

export const DOT_CLASS: Record<TaskStatus, string> = {
  in_progress: 'bg-tweetBlue',
  blocked: 'bg-warningOrange',
  pending: 'bg-textSubtle/50',
  completed: 'bg-successGreen',
}

/** Tasks in dock order: active work first, finished work last. */
export function orderTasks(tasks: TaskSummary[]): TaskSummary[] {
  const ordered: TaskSummary[] = []
  for (const status of TASK_STATUSES) {
    for (const task of tasks) {
      if (task.status === status) ordered.push(task)
    }
  }
  // Anything carrying a status this build does not know about still
  // belongs in the dock — dropping it is what the bug was.
  for (const task of tasks) {
    if (!ordered.includes(task)) ordered.push(task)
  }
  return ordered
}

export function taskCounts(tasks: TaskSummary[]): Record<TaskStatus, number> {
  const counts = { in_progress: 0, blocked: 0, pending: 0, completed: 0 }
  for (const task of tasks) {
    if (task.status in counts) counts[task.status] += 1
  }
  return counts
}
