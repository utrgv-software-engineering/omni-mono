import { useRPCClient, useRPCConnected } from '@omniagents/chat-ui/rpc-context'
import {
  ConversationOrganizationClient,
  type OrganizationThread,
} from '@omniagents/client/rpc/conversation-organization'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const PAGE = 100

interface ThreadsState {
  threads: OrganizationThread[]
  /** Search hits' matching text, by thread. */
  previews: Record<string, string>
  cursor: string | null
  loading: boolean
  error: string | null
}

const initial: ThreadsState = { threads: [], previews: {}, cursor: null, loading: true, error: null }

/**
 * The agent's conversations from the conversation store, through its
 * runtime: newest first, or ranked by `query` (titles and transcript text).
 * Another client renaming or archiving one refreshes the list.
 */
export function useThreads(query: string) {
  const client = useRPCClient()
  const connected = useRPCConnected()
  const organization = useMemo(() => new ConversationOrganizationClient(client), [client])
  useEffect(() => () => organization.dispose(), [organization])
  const [state, setState] = useState<ThreadsState>(initial)
  const generation = useRef(0)

  const fetchPage = useCallback(
    async (cursor: string | null) => {
      const trimmed = query.trim()
      if (trimmed) {
        const page = await organization.searchThreads(trimmed, { limit: PAGE, cursor: cursor ?? undefined })
        return {
          threads: page.results.map((hit) => hit.thread),
          previews: Object.fromEntries(page.results.map((hit) => [hit.thread_id, hit.preview])),
          cursor: page.has_more ? page.next_cursor : null,
        }
      }
      const page = await organization.listThreads({ limit: PAGE, cursor: cursor ?? undefined })
      return { threads: page.threads, previews: {}, cursor: page.has_more ? page.next_cursor : null }
    },
    [organization, query],
  )

  const reload = useCallback(() => {
    const current = ++generation.current
    setState((previous) => ({ ...previous, loading: true, error: null }))
    fetchPage(null).then(
      (page) => current === generation.current && setState({ ...page, loading: false, error: null }),
      (error: unknown) =>
        current === generation.current &&
        setState((previous) => ({ ...previous, loading: false, error: error instanceof Error ? error.message : String(error) })),
    )
  }, [fetchPage])

  const loadMore = useCallback(() => {
    const cursor = state.cursor
    if (!cursor || state.loading) {
      return
    }
    const current = generation.current
    setState((previous) => ({ ...previous, loading: true }))
    fetchPage(cursor).then(
      (page) =>
        current === generation.current &&
        setState((previous) => ({
          threads: [...previous.threads, ...page.threads],
          previews: { ...previous.previews, ...page.previews },
          cursor: page.cursor,
          loading: false,
          error: null,
        })),
      (error: unknown) =>
        current === generation.current &&
        setState((previous) => ({ ...previous, loading: false, error: error instanceof Error ? error.message : String(error) })),
    )
  }, [fetchPage, state.cursor, state.loading])

  // A new query waits for typing to pause; the first load does not.
  const loaded = useRef(false)
  useEffect(() => {
    if (!connected) {
      return
    }
    if (!loaded.current) {
      loaded.current = true
      reload()
      return
    }
    const timer = window.setTimeout(reload, 250)
    return () => window.clearTimeout(timer)
  }, [connected, reload])

  useEffect(() => organization.onThreadUpdated(() => reload()), [organization, reload])

  return { ...state, hasMore: state.cursor !== null, loadMore, reload }
}
