import { useCallback, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { StudioPage } from '../../shared/components/StudioPage'
import { AgentHeader } from '../../shared/components/AgentHeader'
import { AgentsView } from '../../features/agents/AgentsView'
import type { PlaygroundContext } from '../../features/playground/PlaygroundView'
import { ROUTES } from '../routes'
import { buildRoutePath } from '../../lib/routing'

const initialContext: PlaygroundContext = {
  projectName: '',
  sessionId: '',
  statusMessage: '',
  agentKey: null,
  agentLabel: '',
}

export function AgentDetailPage() {
  const params = useParams()
  const navigate = useNavigate()
  const agentKey = params.agentKey ?? ''
  const [agentContext, setAgentContext] = useState<PlaygroundContext>(initialContext)

  const handleBack = useCallback(() => {
    setAgentContext(initialContext)
    navigate(ROUTES.agents)
  }, [navigate, setAgentContext])

  const handleSelectAgent = useCallback(
    (nextAgentKey: string) => {
      navigate(buildRoutePath('agentDetail', { pathParams: { agentKey: nextAgentKey } }))
    },
    [navigate],
  )

  const handleUpdateContext = useCallback((next: PlaygroundContext) => {
    setAgentContext((prev) => ({ ...prev, ...next }))
  }, [])

  const headerLabel = agentContext.agentLabel && agentContext.agentLabel.trim().length > 0
    ? agentContext.agentLabel
    : agentKey || 'Agent'

  return (
    <StudioPage
      section="agents"
      header={
        <AgentHeader
          onBack={handleBack}
          agentLabel={headerLabel}
          projectName={agentContext.projectName}
          statusMessage={agentContext.statusMessage}
        />
      }
    >
      <AgentsView
        selectedAgent={agentKey}
        onSelectAgent={handleSelectAgent}
        onClearAgent={handleBack}
        onUpdateContext={handleUpdateContext}
      />
    </StudioPage>
  )
}
