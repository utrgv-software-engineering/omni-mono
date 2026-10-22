import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { StudioPage } from '../../shared/components/StudioPage'
import { AgentsHeader } from '../../shared/components/AgentsHeader'
import { AgentsView } from '../../features/agents/AgentsView'
import { ROUTES } from '../routes'
import { buildRoutePath } from '../../lib/routing'
import type { PlaygroundContext } from '../../features/playground/PlaygroundView'

const initialContext: PlaygroundContext = {
  projectName: '',
  sessionId: '',
  statusMessage: '',
  agentKey: null,
  agentLabel: '',
}

export function AgentsPage() {
  const navigate = useNavigate()
  const [agentContext, setAgentContext] = useState<PlaygroundContext>(initialContext)

  const handleBack = useCallback(() => {
    navigate(ROUTES.home)
  }, [navigate])

  const handleSelectAgent = useCallback(
    (agentKey: string) => {
      navigate(buildRoutePath('agentDetail', { pathParams: { agentKey } }))
    },
    [navigate],
  )

  const handleUpdateContext = useCallback((next: PlaygroundContext) => {
    setAgentContext((prev) => ({ ...prev, ...next }))
  }, [])

  return (
    <StudioPage
      section="agents"
      header={<AgentsHeader onBack={handleBack} projectName={agentContext.projectName} />}
    >
      <AgentsView
        selectedAgent={null}
        onSelectAgent={handleSelectAgent}
        onClearAgent={() => setAgentContext(initialContext)}
        onUpdateContext={handleUpdateContext}
      />
    </StudioPage>
  )
}
