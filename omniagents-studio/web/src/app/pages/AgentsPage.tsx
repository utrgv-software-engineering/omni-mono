import { useNavigate } from 'react-router-dom'

import { AgentsView } from '../../features/agents/AgentsView'
import { buildRoutePath } from '../../lib/routing'
import { AgentsHeader } from '../../shared/components/AgentsHeader'
import { StudioPage } from '../../shared/components/StudioPage'
import { useChatWithAgent } from './useChatWithAgent'
import { ROUTES } from '../routes'

export function AgentsPage() {
  const navigate = useNavigate()
  const chat = useChatWithAgent()
  return (
    <StudioPage section="agents" header={<AgentsHeader onBack={() => navigate(ROUTES.home)} />}>
      <AgentsView
        selectedAgent={null}
        onSelectAgent={(agentKey) => navigate(buildRoutePath('agentDetail', { pathParams: { agentKey } }))}
        onClearAgent={() => navigate(ROUTES.agents)}
        onChat={chat}
      />
    </StudioPage>
  )
}
