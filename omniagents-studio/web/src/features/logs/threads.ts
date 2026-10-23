import type { OrganizationThread } from '@omniagents/client/rpc/conversation-organization'

/** What an evaluation run records on its conversation (`omniagents` in the
 * thread's metadata, the same block its trace carries). */
export interface EvaluationRun {
  run_type: 'evaluation'
  scenario_type?: string
  scenario?: { id?: string | null; name?: string | null; goal?: string | null }
  run?: { id?: string | null }
  expected?: unknown
  synthetic?: { selection?: { mapping?: Record<string, unknown> } }
}

export function evaluationOf(thread: OrganizationThread): EvaluationRun | null {
  const block = (thread.metadata as { omniagents?: unknown } | undefined)?.omniagents
  if (!block || typeof block !== 'object') {
    return null
  }
  const run = block as Partial<EvaluationRun>
  return run.run_type === 'evaluation' ? (run as EvaluationRun) : null
}

/** The scenario dimensions an evaluation run exercised (`tone: serious`). */
export function dimensionsOf(thread: OrganizationThread): Record<string, string> {
  const mapping = evaluationOf(thread)?.synthetic?.selection?.mapping ?? {}
  return Object.fromEntries(
    Object.entries(mapping)
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([key, value]) => [key, String(value)]),
  )
}

/**
 * Where a conversation's review (judgment, notes, categories) is kept: the
 * trace group its run records under. An evaluation run names its own group;
 * any other run records under `session_<thread>`. Reviews made on a run's
 * traces therefore stay with its conversation, and the timing view finds the
 * same traces.
 */
export function reviewKey(thread: OrganizationThread): string {
  return evaluationOf(thread)?.run?.id || `session_${thread.thread_id}`
}

export function threadTitle(thread: OrganizationThread): string {
  const evaluation = evaluationOf(thread)
  return (
    thread.title?.trim() ||
    evaluation?.scenario?.name ||
    evaluation?.scenario?.id ||
    `Conversation ${thread.thread_id.slice(0, 8)}`
  )
}
