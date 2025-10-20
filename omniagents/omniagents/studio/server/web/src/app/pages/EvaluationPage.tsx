import { StudioPage } from '../../shared/components/StudioPage'
import { EvaluationHeader } from '../../shared/components/EvaluationHeader'

export function EvaluationPage() {
  return (
    <StudioPage
      section="evaluation"
      header={
        <EvaluationHeader
          activeTab="scenarios"
          onTabChange={() => {}}
          onBack={() => {}}
          projectName={null}
          isLoading={false}
        />
      }
    >
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <p>Evaluation view pending migration.</p>
      </div>
    </StudioPage>
  )
}
