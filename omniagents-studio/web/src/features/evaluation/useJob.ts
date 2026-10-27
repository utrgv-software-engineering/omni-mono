import { type QueryKey, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../lib/api'
import type { Job, JobRequest } from '../../types/evaluation'

const POLL_MS = 2000

const finished = (job: Job) => job.state === 'completed' || job.state === 'failed'

/**
 * Start a job and follow it until it finishes; `refresh` names the queries
 * its result changes, refetched when it completes. The job's socket pushes
 * each change; if the socket cannot be held, the job is asked for instead.
 */
export function useJob(refresh: QueryKey[]) {
  const queryClient = useQueryClient()
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const socket = useRef<WebSocket | null>(null)
  const poll = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh

  const stop = useCallback(() => {
    socket.current?.close()
    socket.current = null
    if (poll.current) clearTimeout(poll.current)
    poll.current = null
  }, [])
  useEffect(() => stop, [stop])

  const show = useCallback(
    (next: Job) => {
      setJob(next)
      if (finished(next)) {
        stop()
        if (next.state === 'completed') {
          refreshRef.current.forEach((queryKey) => void queryClient.invalidateQueries({ queryKey }))
        }
      }
    },
    [queryClient, stop],
  )

  const ask = useCallback(
    (jobId: string) => {
      poll.current = setTimeout(() => {
        api.getJob(jobId).then(
          (next) => {
            show(next)
            if (!finished(next)) ask(jobId)
          },
          (cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause)),
        )
      }, POLL_MS)
    },
    [show],
  )

  const follow = useCallback(
    async (started: Job) => {
      show(started)
      if (finished(started)) return
      try {
        const ws = await api.followJob(started.id)
        socket.current = ws
        let ended = false
        ws.onmessage = (event) => {
          const next = JSON.parse(event.data as string) as Job
          ended = finished(next)
          show(next)
        }
        ws.onclose = () => {
          if (!ended && socket.current === ws) {
            socket.current = null
            ask(started.id)
          }
        }
      } catch {
        ask(started.id)
      }
    },
    [ask, show],
  )

  const start = useCallback(
    async (request: JobRequest) => {
      stop()
      setError(null)
      try {
        await follow(await api.startJob(request))
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    },
    [follow, stop],
  )

  const dismiss = useCallback(() => {
    stop()
    setJob(null)
    setError(null)
  }, [stop])

  return {
    job,
    error,
    start,
    dismiss,
    running: job !== null && !finished(job),
  }
}
