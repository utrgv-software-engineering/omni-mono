import React from 'react'

// Server payload from omni-code's /goal autopilot loop (server_functions/goal.py).
// snapshot=null means no goal is set on this session (panel renders nothing).
// "paused" is a non-terminal hold state — the periodic tick is off but
// the goal can be resumed via /goal.resume.
export type GoalSnapshot = {
  goal: string
  turn: number
  max_turns: number
  tick_interval?: number
  last_reason: string | null
  status: 'active' | 'completed' | 'cancelled' | 'paused'
  started_at: number
  completion_reason: string | null
  // Auditable artifact attached on terminal states (achieved/blocked).
  evidence?: string | null
}

const GOAL_TRUNCATE = 80

function shortGoal(goal: string, max: number): string {
  const oneLine = goal.replace(/\s+/g, ' ').trim()
  if (oneLine.length <= max) return oneLine
  return oneLine.slice(0, max - 1) + '…'
}

function dotClass(status: GoalSnapshot['status']): string {
  if (status === 'active') return 'bg-tweetBlue animate-pulse'
  if (status === 'completed') return 'bg-successGreen'
  if (status === 'paused') return 'bg-warningOrange'
  return 'bg-errorRed'
}

// Compact single-line docked panel for the /goal autopilot loop. Mirrors the
// ink TUI's Goal.tsx contract:
//   Active:    ● goal · <text>
//   Completed: ● goal · <text> · completed
//   Cancelled: ● goal · <text> · cancelled
export function GoalPanel({ snapshot }: { snapshot: GoalSnapshot | null }) {
  if (!snapshot) return null
  return (
    <div className="px-3 py-1 mx-3 mb-1 text-xs text-muted-foreground flex items-center gap-2 border-t border-border/40">
      <span
        className={`inline-block w-2 h-2 rounded-full shrink-0 ${dotClass(snapshot.status)}`}
        aria-hidden
      />
      <span className="opacity-70 shrink-0">goal</span>
      <span className="opacity-40 shrink-0">·</span>
      <span className="truncate flex-1 min-w-0" title={snapshot.goal}>
        {shortGoal(snapshot.goal, GOAL_TRUNCATE)}
      </span>
      {snapshot.status !== 'active' && (
        <>
          <span className="opacity-40 shrink-0">·</span>
          <span className="shrink-0">{snapshot.status}</span>
        </>
      )}
    </div>
  )
}
