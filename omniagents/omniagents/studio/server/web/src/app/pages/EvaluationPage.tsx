import { useCallback, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { StudioPage } from '../../shared/components/StudioPage'
import { EvaluationHeader } from '../../shared/components/EvaluationHeader'
import { EvaluationView } from '../../features/evaluation/EvaluationView'
import { useStudioProject } from '../../shared/hooks/useStudioProject'
import { ROUTES, QUERY_PARAMS } from '../routes'
import { buildRoutePath } from '../../lib/routing'

const normalizeParam = (value: string | null) => {
  if (!value) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

const isValidEvaluationTab = (
  value: string | null,
): value is 'scenarios' | 'datasets' | 'dimensions' | 'judges' | 'categories' | 'measures' | 'results' =>
  value === 'scenarios' || value === 'datasets' || value === 'dimensions' || value === 'judges' || value === 'categories' || value === 'measures' || value === 'results'

export function EvaluationPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: studioProject, isLoading: studioProjectLoading, isError: studioProjectError } = useStudioProject()

  const rawTab = normalizeParam(searchParams.get(QUERY_PARAMS.tab))
  const activeTab = isValidEvaluationTab(rawTab) ? rawTab : 'scenarios'

  const updateSearch = useCallback(
    (updates: Record<string, string | null>, options?: { replace?: boolean }) => {
      const next = new URLSearchParams(searchParams)
      let changed = false
      for (const [key, value] of Object.entries(updates)) {
        const current = next.get(key)
        if (value === null) {
          if (current !== null) {
            next.delete(key)
            changed = true
          }
        } else if (current !== value) {
          next.set(key, value)
          changed = true
        }
      }
      if (changed) {
        setSearchParams(next, { replace: options?.replace ?? false })
      }
    },
    [searchParams, setSearchParams],
  )

  useEffect(() => {
    if (rawTab !== null && !isValidEvaluationTab(rawTab)) {
      updateSearch({ [QUERY_PARAMS.tab]: null }, { replace: true })
    }
  }, [rawTab, updateSearch])

  const handleTabChange = useCallback(
    (tab: 'scenarios' | 'datasets' | 'dimensions' | 'judges' | 'categories' | 'measures' | 'results') => {
      if (tab === 'scenarios') {
        updateSearch({ [QUERY_PARAMS.tab]: null })
      } else {
        updateSearch({ [QUERY_PARAMS.tab]: tab })
      }
    },
    [updateSearch],
  )

  const handleBack = useCallback(() => {
    navigate(ROUTES.home)
  }, [navigate])

  const handleNavigateToLogs = useCallback(
    (projectName: string | null, workflowName: string | null, conversationId?: string | null) => {
      const trimmedProject = projectName?.trim()
      if (!trimmedProject) {
        navigate(ROUTES.logs)
        return
      }
      const trimmedWorkflow = workflowName?.trim()
      const trimmedConversation = conversationId?.trim()
      navigate(
        buildRoutePath('logs', {
          query: {
            [QUERY_PARAMS.project]: trimmedProject,
            [QUERY_PARAMS.workflow]: trimmedWorkflow ?? null,
            [QUERY_PARAMS.conversation]: trimmedConversation ?? null,
          },
        }),
      )
    },
    [navigate],
  )

  const projectName = studioProject?.project ?? null
  const evaluationAvailable = Boolean(studioProject?.available)

  const header = (
    <EvaluationHeader
      activeTab={activeTab}
      onTabChange={handleTabChange}
      onBack={handleBack}
      projectName={projectName}
      isLoading={studioProjectLoading}
    />
  )

  return (
    <StudioPage section="evaluation" header={header}>
      <EvaluationView
        activeTab={activeTab}
        onTabChange={handleTabChange}
        projectName={projectName}
        isAvailable={evaluationAvailable && !studioProjectError}
        isLoadingMetadata={studioProjectLoading}
        onNavigateToLogs={handleNavigateToLogs}
      />
    </StudioPage>
  )
}
