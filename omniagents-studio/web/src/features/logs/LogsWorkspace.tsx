import { App } from '@omniagents/chat-ui/App'
import type { OrganizationThread } from '@omniagents/client/rpc/conversation-organization'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@omniagents/ui/resizable'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { QUERY_PARAMS, ROUTES } from '../../app/routes'
import { api } from '../../lib/api'
import { LogsHeader } from '../../shared/components/LogsHeader'
import { StudioPage } from '../../shared/components/StudioPage'
import { useStudioProject } from '../../shared/hooks/useStudioProject'
import type { AgentSummary } from '../../types'
import { DebugView } from '../debug/DebugView'
import { AnalysisPanel } from '../review/components/AnalysisPanel'
import { useReviewStore } from '../review/reviewStore'
import { AgentRuntimeProvider } from '../runtime/AgentRuntimeProvider'
import { TraceTable } from '../traces/components/TraceTable'
import { useConversationTraces, useProjects, useTrace } from '../traces/hooks/useTraces'
import { evaluationOf, reviewKey } from './threads'
import { ThreadList } from './ThreadList'

type ViewMode = 'review' | 'timing'

/**
 * Logs: the project agents' conversations from the conversation store.
 * Review shows a conversation as the shared chat UI renders it, beside the
 * error-analysis panel; Timing shows the traces its runs recorded, when
 * tracing was on.
 */
export function LogsWorkspace() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: agentsData } = useQuery<{ agents: AgentSummary[] }>({
    queryKey: ['agents', 'list'],
    queryFn: () => api.getAgents(),
    staleTime: 5 * 60 * 1000,
  })
  const agents = useMemo(() => agentsData?.agents ?? [], [agentsData])
  const requestedAgent = searchParams.get(QUERY_PARAMS.agent)
  const agentKey =
    agents.find((agent) => agent.key === requestedAgent)?.key ??
    agents.find((agent) => agent.is_entry)?.key ??
    agents[0]?.key ??
    null
  const { data: studioProject } = useStudioProject()
  const { data: projectsData } = useProjects()
  const project = searchParams.get(QUERY_PARAMS.project) || studioProject?.project || null
  const mode: ViewMode = searchParams.get(QUERY_PARAMS.mode) === 'timing' ? 'timing' : 'review'

  const update = useCallback(
    (updates: Record<string, string | null>, replace = false) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(updates)) {
        if (value === null) next.delete(key)
        else next.set(key, value)
      }
      setSearchParams(next, { replace })
    },
    [searchParams, setSearchParams],
  )

  const setSelectedProject = useReviewStore((state) => state.setSelectedProject)
  const loadProjectAnalysis = useReviewStore((state) => state.loadProjectAnalysis)
  useEffect(() => {
    setSelectedProject(project)
    if (project) void loadProjectAnalysis(project)
  }, [project, setSelectedProject, loadProjectAnalysis])

  const header = (
    <LogsHeader
      viewMode={mode}
      onViewModeChange={(next) => update({ [QUERY_PARAMS.mode]: next === 'timing' ? 'timing' : null, [QUERY_PARAMS.trace]: null })}
      showViewToggle={!!searchParams.get(QUERY_PARAMS.thread)}
      showBackButton
      onBack={() => navigate(ROUTES.home)}
      projects={projectsData?.projects ?? []}
      selectedProject={project}
      onProjectChange={(next) => update({ [QUERY_PARAMS.project]: next })}
    />
  )

  if (!agentKey) {
    return (
      <StudioPage section="logs" header={header} showNavigation={false}>
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Launch Studio from a project to review its agents&apos; conversations.
        </div>
      </StudioPage>
    )
  }

  return (
    <AgentRuntimeProvider key={agentKey} agentKey={agentKey}>
      <AgentLogs
        header={header}
        agents={agents}
        agentKey={agentKey}
        project={project}
        mode={mode}
        threadId={searchParams.get(QUERY_PARAMS.thread)}
        traceId={searchParams.get(QUERY_PARAMS.trace)}
        update={update}
      />
    </AgentRuntimeProvider>
  )
}

interface AgentLogsProps {
  header: React.ReactNode
  agents: AgentSummary[]
  agentKey: string
  project: string | null
  mode: ViewMode
  threadId: string | null
  traceId: string | null
  update: (updates: Record<string, string | null>, replace?: boolean) => void
}

function AgentLogs({ header, agents, agentKey, project, mode, threadId, traceId, update }: AgentLogsProps) {
  const [visible, setVisible] = useState<OrganizationThread[]>([])
  const setReviewKeys = useReviewStore((state) => state.setReviewKeys)
  const setSelectedReviewKey = useReviewStore((state) => state.setSelectedReviewKey)
  const selectedThread = visible.find((thread) => thread.thread_id === threadId) ?? null
  // A conversation opened by link may not be in the filtered list; its
  // review key then falls back to the conversation's own group.
  const selectedKey = selectedThread ? reviewKey(selectedThread) : threadId ? `session_${threadId}` : null

  useEffect(() => setReviewKeys(visible.map(reviewKey)), [visible, setReviewKeys])
  useEffect(() => setSelectedReviewKey(selectedKey), [selectedKey, setSelectedReviewKey])

  const onSelect = useCallback(
    (thread: OrganizationThread) => update({ [QUERY_PARAMS.thread]: thread.thread_id, [QUERY_PARAMS.trace]: null }),
    [update],
  )

  const sidebar = (
    <ThreadList
      agents={agents}
      agentKey={agentKey}
      onSelectAgent={(key) => update({ [QUERY_PARAMS.agent]: key, [QUERY_PARAMS.thread]: null, [QUERY_PARAMS.trace]: null })}
      selectedThreadId={threadId}
      onSelect={onSelect}
      onVisibleChange={setVisible}
    />
  )

  return (
    <StudioPage section="logs" header={header} sidebarContent={sidebar} showNavigation={false}>
      {!threadId ? (
        <div className="flex h-full items-center justify-center text-muted-foreground">
          <div className="text-center">
            <p className="text-lg">Select a conversation to review</p>
            <p className="mt-2 text-sm">↑↓ to move, Enter to open, ← → for the previous or next</p>
          </div>
        </div>
      ) : mode === 'timing' ? (
        <ThreadTiming
          project={project}
          groupId={selectedKey}
          traceId={traceId}
          onTrace={(id) => update({ [QUERY_PARAMS.trace]: id })}
        />
      ) : (
        <ResizablePanelGroup orientation="horizontal" className="h-full">
          <ResizablePanel defaultSize="70%" minSize="45%">
            <div className="flex h-full flex-col">
              {selectedThread && evaluationOf(selectedThread) ? <EvaluationSummary thread={selectedThread} /> : null}
              <div className="min-h-0 flex-1">
                <App key={threadId} sessionId={threadId} readOnly />
              </div>
            </div>
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel defaultSize="30%" minSize="20%" maxSize="50%">
            <AnalysisPanel />
          </ResizablePanel>
        </ResizablePanelGroup>
      )}
    </StudioPage>
  )
}

/** What an evaluation run was asked to do, above its transcript. */
function EvaluationSummary({ thread }: { thread: OrganizationThread }) {
  const evaluation = evaluationOf(thread)
  if (!evaluation) return null
  const mapping = evaluation.synthetic?.selection?.mapping ?? {}
  const expected = evaluation.expected
  return (
    <div className="space-y-1 border-b border-border px-4 py-3 text-xs">
      <div className="font-medium">
        Evaluation scenario: {evaluation.scenario?.name ?? evaluation.scenario?.id ?? 'unnamed'}
        {evaluation.scenario_type ? <span className="ml-2 text-muted-foreground">{evaluation.scenario_type}</span> : null}
      </div>
      {evaluation.scenario?.goal ? <div className="text-muted-foreground">Goal: {evaluation.scenario.goal}</div> : null}
      {Object.keys(mapping).length > 0 ? (
        <div className="text-muted-foreground">
          {Object.entries(mapping)
            .map(([name, value]) => `${name}: ${String(value)}`)
            .join(' · ')}
        </div>
      ) : null}
      {expected !== undefined ? (
        <div className="text-muted-foreground">
          Expected: {typeof expected === 'string' ? expected : JSON.stringify(expected)}
        </div>
      ) : null}
    </div>
  )
}

/** The traces a conversation's runs recorded, when tracing was on. */
function ThreadTiming({
  project,
  groupId,
  traceId,
  onTrace,
}: {
  project: string | null
  groupId: string | null
  traceId: string | null
  onTrace: (traceId: string | null) => void
}) {
  const { data: tracesData, isLoading } = useConversationTraces(project, groupId)
  const { data: traceData } = useTrace(project, traceId)
  if (traceId) {
    return <DebugView spans={traceData?.spans ?? []} onBack={() => onTrace(null)} />
  }
  const traces = tracesData?.traces ?? []
  if (!isLoading && traces.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
        No traces were recorded for this conversation. Turn tracing on in the project (`tracing: {'{'}provider: studio{'}'}`) to
        see run timing.
      </div>
    )
  }
  return <TraceTable traces={traces} onTraceSelect={(trace) => onTrace(trace.trace_id)} selectedTraceId={null} />
}
