import { uuidv4 } from '@omniagents/client/util/uuid'
import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense, useCallback, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { api } from '../../lib/api'
import { PlaygroundHeader } from '../../shared/components/PlaygroundHeader'
import { StudioPage } from '../../shared/components/StudioPage'
import type { AgentSummary } from '../../types'
import { QUERY_PARAMS, ROUTES } from '../routes'

// The chat UI is most of Studio's code; only the Playground loads it.
const PlaygroundChat = lazy(() =>
  import('../../features/playground/PlaygroundChat').then((module) => ({ default: module.PlaygroundChat })),
)

/** Chat with a project agent through its real runtime. The address names
 * the agent and the conversation, so a reload or a link reopens both. */
export function PlaygroundPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data, isLoading, isError } = useQuery<{ agents: AgentSummary[] }>({
    queryKey: ['agents', 'list'],
    queryFn: () => api.getAgents(),
    staleTime: 5 * 60 * 1000,
  })
  const agents = data?.agents ?? []
  const requested = searchParams.get(QUERY_PARAMS.agent)
  const agentKey =
    agents.find((agent) => agent.key === requested)?.key ??
    agents.find((agent) => agent.is_entry)?.key ??
    agents[0]?.key ??
    null
  const sessionId = searchParams.get(QUERY_PARAMS.session)

  const show = useCallback(
    (agent: string, session: string, replace = false) => {
      const next = new URLSearchParams(searchParams)
      next.set(QUERY_PARAMS.agent, agent)
      next.set(QUERY_PARAMS.session, session)
      setSearchParams(next, { replace })
    },
    [searchParams, setSearchParams],
  )

  // Every Playground view has an agent and a conversation id from the start.
  useEffect(() => {
    if (agentKey && (requested !== agentKey || !sessionId)) {
      show(agentKey, sessionId ?? uuidv4(), true)
    }
  }, [agentKey, requested, sessionId, show])

  return (
    <StudioPage
      section="playground"
      header={
        <PlaygroundHeader
          onBack={() => navigate(ROUTES.home)}
          agents={agents}
          agentKey={agentKey}
          onSelectAgent={(key) => show(key, uuidv4())}
        />
      }
      disableSidebar
    >
      {isLoading ? (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading agents…</div>
      ) : isError ? (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Unable to load agents.</div>
      ) : !agentKey ? (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Launch Studio from a project to chat with its agents.
        </div>
      ) : sessionId ? (
        <Suspense fallback={null}>
          <PlaygroundChat
            key={agentKey}
            agentKey={agentKey}
            sessionId={sessionId}
            onSessionChange={(id) => show(agentKey, id ?? uuidv4())}
          />
        </Suspense>
      ) : null}
    </StudioPage>
  )
}
