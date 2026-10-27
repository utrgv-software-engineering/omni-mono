import { useQuery } from '@tanstack/react-query'
import { useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { CriteriaTab } from '../../features/evaluation/criteria/CriteriaTab'
import { JudgesTab } from '../../features/evaluation/judges/JudgesTab'
import { OptimizeTab } from '../../features/evaluation/optimize/OptimizeTab'
import { RunsTab } from '../../features/evaluation/runs/RunsTab'
import { ScenariosTab } from '../../features/evaluation/scenarios/ScenariosTab'
import { api } from '../../lib/api'
import { buildRoutePath } from '../../lib/routing'
import { EvaluationHeader } from '../../shared/components/EvaluationHeader'
import { EVALUATION_TABS, type EvaluationTab } from '../../shared/components/evaluationTabs'
import { StudioPage } from '../../shared/components/StudioPage'
import { useStudioProject } from '../../shared/hooks/useStudioProject'
import { QUERY_PARAMS, ROUTES } from '../routes'

function tabOf(value: string | null): EvaluationTab {
  return EVALUATION_TABS.find((tab) => tab.key === value)?.key ?? 'scenarios'
}

/** The project's evaluation: the same files and runs `omniagents eval` uses. */
export function EvaluationPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: studioProject, isLoading } = useStudioProject()
  const agents = useQuery({ queryKey: ['agents', 'list'], queryFn: () => api.getAgents() })
  const tab = tabOf(searchParams.get(QUERY_PARAMS.tab))
  const agent = searchParams.get(QUERY_PARAMS.agent)

  const update = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(searchParams)
      if (value === null) next.delete(key)
      else next.set(key, value)
      setSearchParams(next)
    },
    [searchParams, setSearchParams],
  )

  /** Review a run: the conversation it ran in, with the agent it ran. */
  const openConversation = useCallback(
    (agentKey: string | null, threadId: string) =>
      navigate(buildRoutePath('logs', { query: { [QUERY_PARAMS.agent]: agentKey, [QUERY_PARAMS.thread]: threadId } })),
    [navigate],
  )

  const header = (
    <EvaluationHeader
      activeTab={tab}
      onTabChange={(next) => update(QUERY_PARAMS.tab, next === 'scenarios' ? null : next)}
      onBack={() => navigate(ROUTES.home)}
      projectName={studioProject?.project ?? null}
      isLoading={isLoading}
      agents={agents.data?.agents ?? []}
      agent={agent}
      onAgentChange={(next) => update(QUERY_PARAMS.agent, next)}
    />
  )

  return (
    <StudioPage section="evaluation" header={header}>
      {!isLoading && !studioProject?.available ? (
        <p className="p-6 text-sm text-muted-foreground">Launch Studio from a project to evaluate its agents.</p>
      ) : tab === 'scenarios' ? (
        <ScenariosTab agent={agent} />
      ) : tab === 'runs' ? (
        <RunsTab agent={agent} onOpenConversation={openConversation} />
      ) : tab === 'criteria' ? (
        <CriteriaTab />
      ) : tab === 'judges' ? (
        <JudgesTab />
      ) : (
        <OptimizeTab agent={agent} />
      )}
    </StudioPage>
  )
}
