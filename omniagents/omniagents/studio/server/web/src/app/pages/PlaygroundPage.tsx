import { StudioPage } from '../../shared/components/StudioPage'
import { PlaygroundHeader } from '../../shared/components/PlaygroundHeader'

export function PlaygroundPage() {
  return (
    <StudioPage section="playground" header={<PlaygroundHeader onBack={() => {}} projectName="" />} disableSidebar>
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <p>Playground view pending migration.</p>
      </div>
    </StudioPage>
  )
}
