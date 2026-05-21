import React from 'react'

// Server payload from omni-code's schedule_wakeup tick loop
// (server_functions/wakeup.py). snapshot=null means no schedule is
// active on this session (panel renders nothing). The server emits a
// final snapshot=null on cancel/exhaustion so the panel disappears
// server-driven — no client-side dismissal needed.
export type WakeupSnapshot = {
  message: string
  interval_seconds: number
  recurring: boolean
  end_at: number | null
  end_after: number | null
  fires: number
  started_at: number
  status: 'active' | 'cancelled'
}

const MESSAGE_TRUNCATE = 80

function shortMessage(message: string, max: number): string {
  const oneLine = message.replace(/\s+/g, ' ').trim()
  if (oneLine.length <= max) return oneLine
  return oneLine.slice(0, max - 1) + '…'
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const rem = minutes - hours * 60
  return rem === 0 ? `${hours}h` : `${hours}h${rem}m`
}

function formatTimeRemaining(unixTimestamp: number): string {
  const remaining = Math.max(0, unixTimestamp - Date.now() / 1000)
  return formatInterval(remaining)
}

function dotClass(status: WakeupSnapshot['status']): string {
  if (status === 'active') return 'bg-tweetBlue animate-pulse'
  return 'bg-errorRed'
}

// Compact single-line docked panel for schedule_wakeup, mirroring
// GoalPanel's shape.
//   Active recurring:  ● wakeup · <text> · every 5m · 3 fires
//   Active one-shot:   ● wakeup · <text> · in 30s
//   Cancelled:         ● wakeup · <text> · cancelled
export function WakeupPanel({ snapshot }: { snapshot: WakeupSnapshot | null }) {
  if (!snapshot) return null

  const tail: string[] = []
  if (snapshot.status === 'active') {
    if (snapshot.recurring) {
      tail.push(`every ${formatInterval(snapshot.interval_seconds)}`)
      if (snapshot.end_after !== null && snapshot.end_after !== undefined) {
        tail.push(`${snapshot.end_after} left`)
      } else if (snapshot.end_at !== null && snapshot.end_at !== undefined) {
        tail.push(`until ${formatTimeRemaining(snapshot.end_at)}`)
      } else if (snapshot.fires > 0) {
        tail.push(`${snapshot.fires} fire${snapshot.fires === 1 ? '' : 's'}`)
      }
    } else {
      tail.push(
        snapshot.end_at !== null && snapshot.end_at !== undefined
          ? `in ${formatTimeRemaining(snapshot.end_at)}`
          : `in ${formatInterval(snapshot.interval_seconds)}`,
      )
    }
  } else {
    tail.push(snapshot.status)
  }

  return (
    <div className="px-3 py-1 mx-3 mb-1 text-xs text-muted-foreground flex items-center gap-2 border-t border-border/40">
      <span
        className={`inline-block w-2 h-2 rounded-full shrink-0 ${dotClass(snapshot.status)}`}
        aria-hidden
      />
      <span className="opacity-70 shrink-0">wakeup</span>
      <span className="opacity-40 shrink-0">·</span>
      <span className="truncate flex-1 min-w-0" title={snapshot.message}>
        {shortMessage(snapshot.message, MESSAGE_TRUNCATE)}
      </span>
      {tail.map((part, idx) => (
        <React.Fragment key={idx}>
          <span className="opacity-40 shrink-0">·</span>
          <span className="shrink-0">{part}</span>
        </React.Fragment>
      ))}
    </div>
  )
}
