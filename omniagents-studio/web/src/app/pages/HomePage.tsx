import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { StudioPage } from '../../shared/components/StudioPage'
import { StudioHomeHeader } from '../../shared/components/StudioHomeHeader'
import { HomeView } from '../../features/home/HomeView'
import { ROUTES } from '../routes'

export function HomePage() {
  const navigate = useNavigate()

  const goToLogs = useCallback(() => {
    navigate(ROUTES.logs)
  }, [navigate])

  const goToPlayground = useCallback(() => {
    navigate(ROUTES.playground)
  }, [navigate])

  const goToAgents = useCallback(() => {
    navigate(ROUTES.agents)
  }, [navigate])

  const goToEvaluation = useCallback(() => {
    navigate(ROUTES.evaluation)
  }, [navigate])

  return (
    <StudioPage section="home" header={<StudioHomeHeader />}>
      <HomeView
        onOpenLogs={goToLogs}
        onOpenPlayground={goToPlayground}
        onOpenAgents={goToAgents}
        onOpenEvaluation={goToEvaluation}
      />
    </StudioPage>
  )
}
