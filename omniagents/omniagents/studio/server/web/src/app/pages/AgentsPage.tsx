import { StudioPage } from '../../shared/components/StudioPage'
import { AgentsHeader } from '../../shared/components/AgentsHeader'

export function AgentsPage() {
  return (
    <StudioPage section="agents" header={<AgentsHeader onBack={() => {}} projectName="" />}>
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <p>Agents view pending migration.</p>
      </div>
    </StudioPage>
  )
}
