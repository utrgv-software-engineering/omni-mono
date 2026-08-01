import React from 'react'
import { DOT_CLASS, orderTasks, taskCounts, type TaskSummary } from './tasks'

export type { TaskStatus, TaskSummary } from './tasks'

const MAX_VISIBLE = 5

// Docked task panel rendered just above the input. Mirrors the Ink
// dock: header counts, live status line for the active task, then a
// compact list of rows. Returns null when empty so the message area
// reclaims the space.
//
// Ordering, counting and the dot palette live in `./tasks` so they stay
// exhaustive over the status set and can be tested without a DOM.
export function Tasks({ tasks }: { tasks: TaskSummary[] }) {
  if (!tasks || tasks.length === 0) return null

  const counts = taskCounts(tasks)
  const ordered = orderTasks(tasks)
  const visible = ordered.slice(0, MAX_VISIBLE)
  const overflow = ordered.length - visible.length
  const live = tasks.find(t => t.status === 'in_progress')

  return (
    <div className="px-3 pt-2">
      <div className="rounded-md border border-bgCardAlt bg-bgCardAlt/60 p-2.5">
        <div className="flex items-center gap-2 text-xs text-textSubtle">
          <span className="font-medium text-textPrimary">Tasks</span>
          <span aria-hidden>·</span>
          <span><span className="text-tweetBlue">{counts.in_progress}</span> active</span>
          {counts.blocked > 0 ? (
            <>
              <span aria-hidden>·</span>
              <span><span className="text-warningOrange">{counts.blocked}</span> blocked</span>
            </>
          ) : null}
          <span aria-hidden>·</span>
          <span>{counts.pending} pending</span>
          <span aria-hidden>·</span>
          <span><span className="text-successGreen">{counts.completed}</span> done</span>
        </div>
        {live ? (
          <div className="mt-1.5 flex items-center gap-2 text-xs text-tweetBlue">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-tweetBlue animate-pulse flex-shrink-0" />
            <span className="italic text-shimmer truncate">
              {live.activeForm || live.subject}
            </span>
          </div>
        ) : null}
        {visible.length > 0 ? (
          <ul className="mt-1.5 space-y-1">
            {visible.map(t => {
              const blockers = t.blockedBy ?? []
              const isBlocked = t.status === 'blocked' || blockers.length > 0
              const isDone = t.status === 'completed'
              return (
                <li key={t.id} className="flex items-center gap-2 text-xs leading-5">
                  <span
                    className={[
                      'inline-block w-1.5 h-1.5 rounded-full flex-shrink-0',
                      DOT_CLASS[t.status] ?? DOT_CLASS.pending,
                    ].join(' ')}
                    aria-hidden
                  />
                  <span className="text-textSubtle font-mono">#{t.id}</span>
                  <span
                    className={[
                      'min-w-0 truncate',
                      isDone ? 'text-textSubtle line-through' : 'text-textPrimary',
                    ].join(' ')}
                  >
                    {t.subject}
                  </span>
                  {isBlocked ? (
                    <span className="text-warningOrange whitespace-nowrap">
                      {blockers.length > 0
                        ? `› blocked by #${blockers.join(', #')}`
                        : '› blocked'}
                    </span>
                  ) : null}
                </li>
              )
            })}
          </ul>
        ) : null}
        {overflow > 0 ? (
          <div className="mt-1 text-[11px] text-textSubtle">… +{overflow} more</div>
        ) : null}
      </div>
    </div>
  )
}
