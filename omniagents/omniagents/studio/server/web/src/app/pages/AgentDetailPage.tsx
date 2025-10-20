import { useParams } from 'react-router-dom'
import { StudioPage } from '../../shared/components/StudioPage'
import { AgentHeader } from '../../shared/components/AgentHeader'

export function AgentDetailPage() {
  const params = useParams()
  const agentKey = params.agentKey ?? ''

  return (
    <StudioPage
      section="agents"
      header={<AgentHeader onBack={() => {}} agentLabel={agentKey || 'Agent'} projectName="" statusMessage="" />}
    >
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <p>Agent detail view pending migration.</p>
      </div>
    </StudioPage>
  )
}
