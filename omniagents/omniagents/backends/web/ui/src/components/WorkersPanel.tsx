import React, { useCallback, useEffect, useState } from 'react'

// Server payload from omni-code's ``workers.list`` / ``ui.workers.update``.
// Matches ``worker_state_dict`` in ``tools/worker_tools.py``.
export type WorkerSummary = {
  worker_id: string
  status: 'running' | 'completed' | 'cancelled' | 'error'
  task: string
  parent_session_id: string | null
  session_id: string
  run_id: string
  result: string | null
  error: string | null
  isolation: string | null
  started_at: number | null
  finished_at: number | null
  wall_time_ms: number | null
}

export type WorkersKillResult = {
  ok: boolean
  worker?: WorkerSummary
  status?: string
  snapshot?: WorkerSummary[]
  error?: string
  message?: string
}

const MAX_VISIBLE = 5
const TASK_TRUNCATE = 80

function shortTask(task: string): string {
  const oneLine = task.replace(/\s+/g, ' ').trim()
  if (oneLine.length <= TASK_TRUNCATE) return oneLine
  return oneLine.slice(0, TASK_TRUNCATE - 1) + '…'
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const rem = Math.floor(seconds - minutes * 60)
  return `${minutes}m${rem.toString().padStart(2, '0')}s`
}

function liveElapsedMs(worker: WorkerSummary, nowMs: number): number {
  if (worker.status === 'running' && worker.started_at) {
    return Math.max(nowMs - worker.started_at * 1000, worker.wall_time_ms ?? 0)
  }
  return worker.wall_time_ms ?? 0
}

function dotClass(worker: WorkerSummary): string {
  if (worker.status === 'running') return 'bg-tweetBlue animate-pulse'
  if (worker.status === 'completed') return 'bg-successGreen'
  return 'bg-errorRed'
}

type Props = {
  workers: WorkerSummary[]
  onKill?: (worker_id: string) => Promise<WorkersKillResult>
}

// Docked workers panel. Mirrors BashJobs in shape — see that component
// for the design rationale. Renders nothing when there are no workers.
export function WorkersPanel({ workers, onKill }: Props) {
  const [, setNowTick] = useState(0)
  const anyRunning = workers.some(w => w.status === 'running')
  useEffect(() => {
    if (!anyRunning) return
    const id = window.setInterval(() => setNowTick(n => n + 1), 1000)
    return () => window.clearInterval(id)
  }, [anyRunning])

  const [killing, setKilling] = useState<Set<string>>(new Set())
  const [killError, setKillError] = useState<string | null>(null)

  const handleKill = useCallback(async (worker_id: string) => {
    if (!onKill) return
    if (!window.confirm(`Stop worker ${worker_id}?`)) return
    setKillError(null)
    setKilling(prev => {
      const next = new Set(prev)
      next.add(worker_id)
      return next
    })
    try {
      const res = await onKill(worker_id)
      if (!res.ok) {
        setKillError(`Failed to stop ${worker_id}: ${res.error ?? 'unknown error'}`)
      }
    } catch (e) {
      setKillError(`Failed to stop ${worker_id}: ${(e as Error).message ?? String(e)}`)
    } finally {
      setKilling(prev => {
        const next = new Set(prev)
        next.delete(worker_id)
        return next
      })
    }
  }, [onKill])

  if (!workers || workers.length === 0) return null

  const running = workers.filter(w => w.status === 'running')
  const exited = workers.filter(w => w.status !== 'running')
  const succeeded = exited.filter(w => w.status === 'completed').length
  const failed = exited.length - succeeded

  const ordered = [
    ...running,
    ...exited.slice().sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0)),
  ]
  const visible = ordered.slice(0, MAX_VISIBLE)
  const overflow = ordered.length - visible.length
  const nowMs = Date.now()

  return (
    <div className="px-3 pt-2">
      <div className="rounded-md border border-border bg-card/60 p-2.5">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Workers</span>
          <span aria-hidden>·</span>
          <span><span className="text-tweetBlue">{running.length}</span> running</span>
          <span aria-hidden>·</span>
          <span><span className="text-successGreen">{succeeded}</span> done</span>
          {failed > 0 ? (
            <>
              <span aria-hidden>·</span>
              <span><span className="text-errorRed">{failed}</span> failed</span>
            </>
          ) : null}
        </div>
        {killError ? <div className="mt-1 text-[11px] text-errorRed">{killError}</div> : null}
        <ul className="mt-1.5 space-y-1">
          {visible.map(w => {
            const elapsed = formatElapsed(liveElapsedMs(w, nowMs))
            const tail = w.status === 'running' ? elapsed : `${w.status} · ${elapsed}`
            return (
              <li key={w.worker_id} className="flex items-center gap-2 text-xs leading-5">
                <span
                  className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotClass(w)}`}
                  aria-hidden
                />
                <span className="text-muted-foreground font-mono">{w.worker_id}</span>
                <span
                  className={`min-w-0 truncate ${w.status === 'running' ? 'text-foreground' : 'text-muted-foreground'}`}
                  title={w.task}
                >
                  {shortTask(w.task)}
                </span>
                <span className="ml-auto text-muted-foreground whitespace-nowrap">{tail}</span>
                {onKill && w.status === 'running' ? (
                  <button
                    type="button"
                    disabled={killing.has(w.worker_id)}
                    onClick={() => handleKill(w.worker_id)}
                    className="text-muted-foreground hover:text-errorRed transition-colors px-1.5 py-0.5 rounded hover:bg-accent disabled:opacity-50 disabled:cursor-not-allowed"
                    title={`Stop worker ${w.worker_id}`}
                    aria-label={`Stop worker ${w.worker_id}`}
                  >
                    {killing.has(w.worker_id) ? '…' : '✕'}
                  </button>
                ) : null}
              </li>
            )
          })}
        </ul>
        {overflow > 0 ? (
          <div className="mt-1 text-[11px] text-muted-foreground">… +{overflow} more</div>
        ) : null}
      </div>
    </div>
  )
}
