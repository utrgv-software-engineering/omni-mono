import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, ChevronRight } from 'lucide-react'
import { api } from '../../lib/api'
import type { AgentSummary } from '../../types'
import { AgentWorkbench, type PlaygroundContext } from '../playground/PlaygroundView'

interface AgentsViewProps {
  selectedAgent: string | null
  onSelectAgent: (agentKey: string) => void
  onClearAgent: () => void
  onUpdateContext: (context: PlaygroundContext) => void
}

export function AgentsView({ selectedAgent, onSelectAgent, onClearAgent, onUpdateContext }: AgentsViewProps) {
  const { data, isLoading, isError, refetch } = useQuery<{ agents: AgentSummary[] }>({
    queryKey: ['agents', 'list'],
    queryFn: () => api.getAgents(),
    staleTime: 5 * 60 * 1000,
  })

  const agents = data?.agents ?? []
  const activeAgent = useMemo(() => {
    if (!selectedAgent) {
      return null
    }
    return agents.find((agent) => agent.key === selectedAgent) ?? null
  }, [agents, selectedAgent])

  if (selectedAgent) {
    if (!activeAgent && !isLoading && !isError && agents.length > 0) {
      return (
        <div className="h-full w-full overflow-y-auto p-6">
          <div className="mx-auto flex h-full max-w-4xl flex-col items-center justify-center gap-4 text-center">
            <div className="text-sm text-muted-foreground">The selected agent is no longer available.</div>
            <button
              type="button"
              onClick={onClearAgent}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
            >
              Return to agents
            </button>
          </div>
        </div>
      )
    }
    const agentLabel = activeAgent?.label ?? selectedAgent
    const agentDescription = activeAgent?.description
    return (
      <div className="h-full w-full overflow-y-auto p-6">
        <div className="mx-auto flex h-full max-w-6xl flex-col gap-4">
          {agentDescription ? (
            <div className="rounded-md border border-border bg-muted/40 p-4 text-sm text-muted-foreground">
              {agentDescription}
            </div>
          ) : null}
          <AgentWorkbench
            key={selectedAgent}
            metadataQueryKey={['agents', selectedAgent, 'metadata']}
            metadataFetcher={() => api.getAgentMetadata(selectedAgent)}
            configureSession={(payload) => api.createAgentSession(selectedAgent, payload)}
            onUpdateContext={onUpdateContext}
            heading={`Configure ${agentLabel}`}
            agentKey={selectedAgent}
            agentLabel={agentLabel}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="h-full w-full overflow-y-auto p-6">
      <div className="mx-auto flex h-full max-w-6xl flex-col gap-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold">Agents</h2>
            <p className="text-sm text-muted-foreground">Select an agent to configure its defaults, tools, and guardrails.</p>
          </div>
        </div>
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Loading agents…
          </div>
        ) : isError ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
            <span>Unable to load agents.</span>
            <button
              type="button"
              onClick={() => refetch()}
              className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
            >
              Retry
            </button>
          </div>
        ) : agents.length === 0 ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            No agents discovered in this project.
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <button
                key={agent.key}
                type="button"
                onClick={() => onSelectAgent(agent.key)}
                className="flex h-full flex-col rounded-md border border-border bg-background p-4 text-left transition-shadow hover:shadow"
              >
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
                      <Bot className="h-4 w-4" />
                    </div>
                    <div className="text-sm font-semibold">{agent.label}</div>
                  </div>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </div>
                {agent.description ? (
                  <p className="mb-3 text-xs text-muted-foreground">{agent.description}</p>
                ) : null}
                <div className="mt-auto text-xs text-muted-foreground">
                  {agent.model ? `Default model: ${agent.model}` : 'No default model specified'}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
