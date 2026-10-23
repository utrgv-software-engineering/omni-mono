import { useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { AgentsView } from '../../features/agents/AgentsView'
import { buildRoutePath } from '../../lib/routing'
import { AgentHeader } from '../../shared/components/AgentHeader'
import { StudioPage } from '../../shared/components/StudioPage'
import { useChatWithAgent } from './useChatWithAgent'
import { ROUTES } from '../routes'

export function AgentDetailPage() {
  const params = useParams()
  const navigate = useNavigate()
  const chat = useChatWithAgent()
  const agentKey = params.agentKey ?? ''
  const handleBack = useCallback(() => navigate(ROUTES.agents), [navigate])

  return (
    <StudioPage section="agents" header={<AgentHeader onBack={handleBack} agentLabel={agentKey || 'Agent'} />}>
      <AgentsView
        selectedAgent={agentKey}
        onSelectAgent={(next) => navigate(buildRoutePath('agentDetail', { pathParams: { agentKey: next } }))}
        onClearAgent={handleBack}
        onChat={chat}
      />
    </StudioPage>
  )
}
