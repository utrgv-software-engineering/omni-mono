import { useState, useEffect, useRef } from 'react'
import { matchPath, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { AppShell } from '../shared/components/AppShell'
import { AppHeader } from '../shared/components/AppHeader'
import { ConversationList } from '../features/traces/components/ConversationList'
import { TraceTable } from '../features/traces/components/TraceTable'
import { DebugView } from '../features/debug/DebugView'
import { ReviewView } from '../features/review/ReviewView'
import { useTraceStore } from '../features/traces/store/traceStore'
import { useConversations, useConversationTraces, useTrace } from '../features/traces/hooks/useTraces'
import { useWebSocket } from '../shared/hooks/useWebSocket'
import type { Conversation, Trace } from '../types'
import { StudioSidebar } from '../shared/components/StudioSidebar'
import { HomeView } from '../features/home/HomeView'
import { StudioHomeHeader } from '../shared/components/StudioHomeHeader'
import { PlaygroundView, type PlaygroundContext } from '../features/playground/PlaygroundView'
import { PlaygroundHeader } from '../shared/components/PlaygroundHeader'
import { AgentsView } from '../features/agents/AgentsView'
import { AgentsHeader } from '../shared/components/AgentsHeader'
import { AgentHeader } from '../shared/components/AgentHeader'
import { EvaluationView } from '../features/evaluation/EvaluationView'
import { EvaluationHeader } from '../shared/components/EvaluationHeader'
import { useStudioProject } from '../shared/hooks/useStudioProject'
import { ROUTES, QUERY_PARAMS } from './routes'
import { buildRoutePath } from '../lib/routing'

type StudioSection = 'home' | 'logs' | 'playground' | 'agents' | 'evaluation'

export function Dashboard() {
  const {
    selectedConversation,
    setSelectedConversation,
    setConversations,
    conversationTraces,
    setConversationTraces,
    selectedTrace,
    setSelectedTrace,
    setSpans,
    selectedProject,
    selectedWorkflow,
    loadProjectAnalysis,
    setSelectedProject: selectTraceProject,
    setSelectedWorkflow: selectTraceWorkflow
  } = useTraceStore()

  const [viewMode, setViewMode] = useState<'debug' | 'review'>('review')
  const [playgroundContext, setPlaygroundContext] = useState<PlaygroundContext>({
    projectName: '',
    sessionId: '',
    statusMessage: '',
  })
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [agentContext, setAgentContext] = useState<PlaygroundContext>({
    projectName: '',
    sessionId: '',
    statusMessage: '',
    agentKey: null,
    agentLabel: '',
  })
  const [evaluationTab, setEvaluationTab] = useState<'scenarios' | 'datasets' | 'dimensions' | 'judges'>('scenarios')

  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const pathname = location.pathname
  const syncingFromUrlRef = useRef(false)

  const normalizeParam = (value: string | null) => {
    if (!value) {
      return null
    }
    const trimmed = value.trim()
    return trimmed.length > 0 ? trimmed : null
  }

  const isValidEvaluationTab = (value: string | null): value is typeof evaluationTab =>
    value === 'scenarios' || value === 'datasets' || value === 'dimensions' || value === 'judges'

  const applyFromUrl = (updater: () => void) => {
    syncingFromUrlRef.current = true
    updater()
    Promise.resolve().then(() => {
      syncingFromUrlRef.current = false
    })
  }

  const logsMatch = matchPath({ path: ROUTES.logs, end: true }, pathname)
  const playgroundMatch = matchPath({ path: ROUTES.playground, end: true }, pathname)
  const agentsMatch = matchPath({ path: ROUTES.agents, end: true }, pathname)
  const agentDetailMatch = matchPath({ path: ROUTES.agentDetail, end: true }, pathname)
  const evaluationMatch = matchPath({ path: ROUTES.evaluation, end: true }, pathname)

  const isLogs = Boolean(logsMatch)
  const isPlayground = Boolean(playgroundMatch)
  const isAgents = Boolean(agentsMatch) || Boolean(agentDetailMatch)
  const isEvaluation = Boolean(evaluationMatch)
  const agentKeyFromRoute = agentDetailMatch?.params?.agentKey ?? null
  const activeSection: StudioSection = isLogs
    ? 'logs'
    : isPlayground
      ? 'playground'
      : isAgents
        ? 'agents'
        : isEvaluation
          ? 'evaluation'
          : 'home'

  const { data: conversationsData } = useConversations()
  const { data: conversationTracesData } = useConversationTraces(selectedConversation?.group_id || null)
  const { data: traceData } = useTrace(selectedProject, selectedTrace?.trace_id || null)
  const { data: studioProject, isLoading: studioProjectLoading, isError: studioProjectError } = useStudioProject()
  const evaluationProjectName = studioProject?.project ?? null
  const evaluationAvailable = Boolean(studioProject?.available)

  useWebSocket()

  useEffect(() => {
    if (!isLogs) {
      return
    }
    const projectParam = normalizeParam(searchParams.get(QUERY_PARAMS.project))
    if ((selectedProject ?? null) !== (projectParam ?? null)) {
      applyFromUrl(() => {
        selectTraceProject(projectParam)
      })
    }
    const workflowParam = normalizeParam(searchParams.get(QUERY_PARAMS.workflow))
    if ((selectedWorkflow ?? null) !== (workflowParam ?? null)) {
      applyFromUrl(() => {
        selectTraceWorkflow(workflowParam)
      })
    }
    const modeParam = normalizeParam(searchParams.get(QUERY_PARAMS.mode))
    if (modeParam === 'debug' || modeParam === 'review') {
      if (viewMode !== modeParam) {
        applyFromUrl(() => {
          setViewMode(modeParam)
        })
      }
    } else if (modeParam) {
      if (viewMode !== 'review') {
        applyFromUrl(() => {
          setViewMode('review')
        })
      }
      const next = new URLSearchParams(searchParams)
      if (next.get(QUERY_PARAMS.mode) !== null) {
        next.delete(QUERY_PARAMS.mode)
        setSearchParams(next, { replace: true })
      }
    }
  }, [isLogs, searchParams, selectedProject, selectedWorkflow, viewMode, selectTraceProject, selectTraceWorkflow, setViewMode, setSearchParams])

  useEffect(() => {
    if (!isLogs) {
      return
    }
    const manifestProject = studioProject?.project?.trim()
    if (!manifestProject) {
      return
    }
    const currentProject = useTraceStore.getState().selectedProject
    if (currentProject === manifestProject) {
      return
    }
    selectTraceProject(manifestProject)
  }, [isLogs, studioProject?.project, selectTraceProject])

  useEffect(() => {
    if (!isLogs) {
      return
    }
    if (conversationsData?.conversations) {
      setConversations(conversationsData.conversations)
    }
  }, [isLogs, conversationsData, setConversations])

  useEffect(() => {
    if (!isLogs) {
      return
    }
    const conversationParam = normalizeParam(searchParams.get(QUERY_PARAMS.conversation))
    if (!conversationParam) {
      if (selectedConversation) {
        applyFromUrl(() => {
          setSelectedConversation(null)
          setSelectedTrace(null)
        })
      }
      return
    }
    const conversationList = conversationsData?.conversations || []
    if (conversationList.length === 0) {
      return
    }
    if (selectedConversation?.group_id === conversationParam) {
      return
    }
    const found = conversationList.find((item) => item.group_id === conversationParam)
    if (found) {
      applyFromUrl(() => {
        setSelectedConversation(found)
        setSelectedTrace(null)
      })
      return
    }
    if (selectedConversation) {
      applyFromUrl(() => {
        setSelectedConversation(null)
        setSelectedTrace(null)
      })
    }
    const next = new URLSearchParams(searchParams)
    const hadConversationParam = next.get(QUERY_PARAMS.conversation) !== null
    const hadTraceParam = next.get(QUERY_PARAMS.trace) !== null
    if (hadConversationParam || hadTraceParam) {
      next.delete(QUERY_PARAMS.conversation)
      next.delete(QUERY_PARAMS.trace)
      setSearchParams(next, { replace: true })
    }
  }, [isLogs, searchParams, conversationsData, selectedConversation, setSelectedConversation, setSelectedTrace, setSearchParams])

  useEffect(() => {
    if (!isLogs) {
      return
    }
    if (selectedProject && selectedWorkflow) {
      loadProjectAnalysis(selectedProject, selectedWorkflow)
    }
  }, [isLogs, selectedProject, selectedWorkflow, loadProjectAnalysis])

  useEffect(() => {
    if (!isLogs) {
      return
    }
    if (conversationTracesData?.traces) {
      setConversationTraces(conversationTracesData.traces)
      const traceParam = normalizeParam(searchParams.get(QUERY_PARAMS.trace))
      if (!traceParam && viewMode === 'review' && conversationTracesData.traces.length > 0 && !selectedTrace) {
        const lastTrace = conversationTracesData.traces[conversationTracesData.traces.length - 1]
        setSelectedTrace(lastTrace)
      }
    }
  }, [isLogs, conversationTracesData, setConversationTraces, viewMode, selectedTrace, setSelectedTrace, searchParams])

  useEffect(() => {
    if (!isLogs) {
      return
    }
    const traceParam = normalizeParam(searchParams.get(QUERY_PARAMS.trace))
    if (!traceParam) {
      if (selectedTrace) {
        applyFromUrl(() => {
          setSelectedTrace(null)
        })
      }
      return
    }
    if (selectedTrace?.trace_id === traceParam) {
      return
    }
    if (!conversationTraces.length) {
      return
    }
    const found = conversationTraces.find((item) => item.trace_id === traceParam)
    if (found) {
      applyFromUrl(() => {
        setSelectedTrace(found)
      })
      return
    }
    if (selectedTrace) {
      applyFromUrl(() => {
        setSelectedTrace(null)
      })
    }
    const next = new URLSearchParams(searchParams)
    if (next.get(QUERY_PARAMS.trace) !== null) {
      next.delete(QUERY_PARAMS.trace)
      setSearchParams(next, { replace: true })
    }
  }, [isLogs, searchParams, conversationTraces, selectedTrace, setSelectedTrace, setSearchParams])

  useEffect(() => {
    if (!isLogs) {
      setSpans([])
      return
    }
    if (traceData?.spans) {
      setSpans(traceData.spans)
    } else {
      setSpans([])
    }
  }, [isLogs, traceData, setSpans])

  useEffect(() => {
    if (!isLogs) {
      return
    }
    if (syncingFromUrlRef.current) {
      return
    }
    const next = new URLSearchParams(searchParams)
    let hasChanges = false

    const updateParam = (key: string, value: string | null) => {
      const current = next.get(key)
      if (value === null) {
        if (current !== null) {
          next.delete(key)
          hasChanges = true
        }
      } else if (current !== value) {
        next.set(key, value)
        hasChanges = true
      }
    }

    const projectValue = selectedProject ? selectedProject : null
    const workflowValue = selectedWorkflow ? selectedWorkflow : null
    const conversationValue = selectedConversation ? selectedConversation.group_id : null
    const traceValue = selectedTrace ? selectedTrace.trace_id : null
    const modeValue = viewMode === 'review' ? null : viewMode

    updateParam(QUERY_PARAMS.project, projectValue)
    updateParam(QUERY_PARAMS.workflow, workflowValue)
    updateParam(QUERY_PARAMS.conversation, conversationValue)
    updateParam(QUERY_PARAMS.trace, traceValue)
    updateParam(QUERY_PARAMS.mode, modeValue)

    if (hasChanges) {
      setSearchParams(next, { replace: true })
    }
  }, [isLogs, searchParams, selectedProject, selectedWorkflow, selectedConversation, selectedTrace, viewMode, setSearchParams])

  useEffect(() => {
    if (!isAgents) {
      if (selectedAgent) {
        setSelectedAgent(null)
        setAgentContext({ projectName: '', sessionId: '', statusMessage: '', agentKey: null, agentLabel: '' })
      }
      return
    }
    if (selectedAgent === agentKeyFromRoute) {
      return
    }
    setSelectedAgent(agentKeyFromRoute)
    setAgentContext({ projectName: '', sessionId: '', statusMessage: '', agentKey: agentKeyFromRoute, agentLabel: '' })
  }, [isAgents, agentKeyFromRoute, selectedAgent, setAgentContext])

  useEffect(() => {
    if (!isEvaluation) {
      return
    }
    const tabParam = normalizeParam(searchParams.get(QUERY_PARAMS.tab))
    if (isValidEvaluationTab(tabParam)) {
      if (evaluationTab !== tabParam) {
        setEvaluationTab(tabParam)
      }
      return
    }
    if (evaluationTab !== 'scenarios') {
      setEvaluationTab('scenarios')
    }
    if (tabParam) {
      const next = new URLSearchParams(searchParams)
      next.delete(QUERY_PARAMS.tab)
      setSearchParams(next, { replace: true })
    }
  }, [isEvaluation, searchParams, evaluationTab, setEvaluationTab, setSearchParams])

  useEffect(() => {
    if (!isEvaluation) {
      return
    }
    const next = new URLSearchParams(searchParams)
    let hasChanges = false
    const current = next.get(QUERY_PARAMS.tab)
    const desired = evaluationTab === 'scenarios' ? null : evaluationTab
    if (desired === null) {
      if (current !== null) {
        next.delete(QUERY_PARAMS.tab)
        hasChanges = true
      }
    } else if (current !== desired) {
      next.set(QUERY_PARAMS.tab, desired)
      hasChanges = true
    }
    if (hasChanges) {
      setSearchParams(next, { replace: true })
    }
  }, [isEvaluation, evaluationTab, searchParams, setSearchParams])

  useEffect(() => {
    if (isPlayground) {
      const projectParam = normalizeParam(searchParams.get(QUERY_PARAMS.project))
      const sessionParam = normalizeParam(searchParams.get(QUERY_PARAMS.session))
      setPlaygroundContext((prev) => {
        const nextProject = projectParam ?? prev.projectName
        const nextSession = sessionParam ?? prev.sessionId
        if (prev.projectName === nextProject && prev.sessionId === nextSession) {
          return prev
        }
        return { ...prev, projectName: nextProject, sessionId: nextSession }
      })
      return
    }
    if (playgroundContext.projectName || playgroundContext.sessionId || playgroundContext.statusMessage) {
      setPlaygroundContext({ projectName: '', sessionId: '', statusMessage: '' })
    }
    const next = new URLSearchParams(searchParams)
    let hasChanges = false
    if (next.get(QUERY_PARAMS.session) !== null) {
      next.delete(QUERY_PARAMS.session)
      hasChanges = true
    }
    if (next.get(QUERY_PARAMS.project) !== null) {
      next.delete(QUERY_PARAMS.project)
      hasChanges = true
    }
    if (hasChanges) {
      setSearchParams(next, { replace: true })
    }
  }, [isPlayground, searchParams, playgroundContext.projectName, playgroundContext.sessionId, playgroundContext.statusMessage, setPlaygroundContext, setSearchParams])

  useEffect(() => {
    if (!isPlayground) {
      return
    }
    const next = new URLSearchParams(searchParams)
    let hasChanges = false

    const updateParam = (key: string, value: string | null) => {
      const current = next.get(key)
      if (value === null) {
        if (current !== null) {
          next.delete(key)
          hasChanges = true
        }
      } else if (current !== value) {
        next.set(key, value)
        hasChanges = true
      }
    }

    const projectValue = playgroundContext.projectName.trim()
    const sessionValue = playgroundContext.sessionId.trim()

    updateParam(QUERY_PARAMS.project, projectValue.length > 0 ? projectValue : null)
    updateParam(QUERY_PARAMS.session, sessionValue.length > 0 ? sessionValue : null)

    if (hasChanges) {
      setSearchParams(next, { replace: true })
    }
  }, [isPlayground, playgroundContext.projectName, playgroundContext.sessionId, searchParams, setSearchParams])

  const handleOpenLogs = () => {
    const query = {
      [QUERY_PARAMS.project]: selectedProject,
      [QUERY_PARAMS.workflow]: selectedWorkflow,
      [QUERY_PARAMS.conversation]: selectedConversation ? selectedConversation.group_id : null,
      [QUERY_PARAMS.trace]: selectedTrace ? selectedTrace.trace_id : null,
      [QUERY_PARAMS.mode]: viewMode === 'review' ? null : viewMode,
    }
    navigate(buildRoutePath('logs', { query }))
  }

  const handleOpenPlayground = () => {
    navigate(ROUTES.playground)
  }

  const handleOpenAgents = () => {
    navigate(ROUTES.agents)
  }

  const handleOpenEvaluation = () => {
    setEvaluationTab('scenarios')
    navigate(ROUTES.evaluation)
  }

  const handleBackToHome = () => {
    navigate(ROUTES.home)
    setSelectedConversation(null)
    setSelectedTrace(null)
    setViewMode('review')
  }

  const handleSelectAgent = (agentKey: string) => {
    setSelectedAgent(agentKey)
    setAgentContext({ projectName: '', sessionId: '', statusMessage: '', agentKey, agentLabel: '' })
    navigate(buildRoutePath('agentDetail', { pathParams: { agentKey } }))
  }

  const handleClearAgent = () => {
    setSelectedAgent(null)
    setAgentContext({ projectName: '', sessionId: '', statusMessage: '', agentKey: null, agentLabel: '' })
    navigate(ROUTES.agents)
  }

  const handleConversationSelect = (conversation: Conversation) => {
    setSelectedConversation(conversation)
    setSelectedTrace(null)
    setViewMode('review')
  }

  const handleTraceSelect = (trace: Trace) => {
    setSelectedTrace(trace)
  }

  const handleNavigateToLogs = (project: string | null, workflow: string | null) => {
    if (!project) {
      return
    }
    const normalizedProject = project.trim()
    if (!normalizedProject) {
      return
    }
    selectTraceProject(normalizedProject)
    const normalizedWorkflow = workflow && workflow.trim()
    if (normalizedWorkflow) {
      selectTraceWorkflow(normalizedWorkflow)
    }
    setSelectedConversation(null)
    setSelectedTrace(null)
    setSpans([])
    setViewMode('review')
    const query = {
      [QUERY_PARAMS.project]: normalizedProject,
      [QUERY_PARAMS.workflow]: normalizedWorkflow ?? null,
    }
    navigate(buildRoutePath('logs', { query }))
  }

  const renderLogsContent = () => {
    if (!selectedConversation) {
      return (
        <div className="h-full flex items-center justify-center text-muted-foreground">
          <div className="text-center">
            <p className="text-lg">Select a conversation to get started</p>
            <p className="text-sm mt-2">Choose from the list on the left</p>
          </div>
        </div>
      )
    }

    return (
      <div className="h-full">
        {viewMode === 'review' ? (
          <ReviewView spans={traceData?.spans || []} />
        ) : selectedTrace ? (
          <DebugView
            spans={traceData?.spans || []}
            onBack={() => setSelectedTrace(null)}
          />
        ) : (
          <TraceTable
            traces={conversationTraces}
            onTraceSelect={handleTraceSelect}
            selectedTraceId={null}
          />
        )}
      </div>
    )
  }

  const handleViewModeChange = (mode: 'debug' | 'review') => {
    setViewMode(mode)
    if (mode === 'debug') {
      setSelectedTrace(null)
    }
    if (mode === 'review' && conversationTraces.length > 0) {
      const lastTrace = conversationTraces[conversationTraces.length - 1]
      setSelectedTrace(lastTrace)
    }
  }

  const showShortcutsBar = !!(isLogs && selectedConversation && viewMode === 'review')

  const sidebar = isPlayground
    ? null
    : (
      <StudioSidebar
        activeSection={activeSection}
        onSelect={(section) => {
          if (section === 'logs') {
            handleOpenLogs()
          } else if (section === 'playground') {
            handleOpenPlayground()
          } else if (section === 'evaluation') {
            handleOpenEvaluation()
          } else if (section === 'agents') {
            handleOpenAgents()
          } else {
            handleBackToHome()
          }
        }}
        showNavigation={!isLogs}
      >
        {isLogs && (
          <ConversationList
            conversations={conversationsData?.conversations || []}
            onConversationSelect={handleConversationSelect}
          />
        )}
      </StudioSidebar>
    )

  const header = isLogs ? (
    <AppHeader
      viewMode={viewMode}
      onViewModeChange={handleViewModeChange}
      showViewToggle={!!selectedConversation}
      showBackButton
      onBack={handleBackToHome}
    />
  ) : isPlayground ? (
    <PlaygroundHeader
      onBack={handleBackToHome}
      projectName={playgroundContext.projectName}
    />
  ) : isAgents ? (
    selectedAgent ? (
      <AgentHeader
        onBack={handleClearAgent}
        agentLabel={agentContext.agentLabel || selectedAgent || 'Agent'}
        projectName={agentContext.projectName}
        statusMessage={agentContext.statusMessage}
      />
    ) : (
      <AgentsHeader
        onBack={handleBackToHome}
        projectName={agentContext.projectName}
      />
    )
  ) : isEvaluation ? (
    <EvaluationHeader
      activeTab={evaluationTab}
      onTabChange={setEvaluationTab}
      onBack={handleBackToHome}
      projectName={evaluationProjectName}
      isLoading={studioProjectLoading}
    />
  ) : (
    <StudioHomeHeader />
  )

  return (
    <AppShell
      header={header}
      sidebar={sidebar}
      hasBottomBar={showShortcutsBar}
    >
      {isLogs && renderLogsContent()}
      {isPlayground && (
        <PlaygroundView onUpdateContext={setPlaygroundContext} />
      )}
      {isAgents && (
        <AgentsView
          selectedAgent={selectedAgent}
          onSelectAgent={handleSelectAgent}
          onClearAgent={handleClearAgent}
          onUpdateContext={setAgentContext}
        />
      )}
      {isEvaluation && (
        <EvaluationView
          activeTab={evaluationTab}
          onTabChange={setEvaluationTab}
          projectName={evaluationProjectName}
          isAvailable={evaluationAvailable && !studioProjectError}
          isLoadingMetadata={studioProjectLoading}
          onNavigateToLogs={handleNavigateToLogs}
        />
      )}
      {activeSection === 'home' && (
        <HomeView
          onOpenLogs={handleOpenLogs}
          onOpenPlayground={handleOpenPlayground}
          onOpenAgents={handleOpenAgents}
          onOpenEvaluation={handleOpenEvaluation}
        />
      )}
      {showShortcutsBar && (
        <div className="fixed bottom-0 left-0 right-0 bg-background/95 backdrop-blur border-t border-border px-4 py-2.5 flex items-center justify-center gap-3 text-xs z-50">
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 bg-muted border border-border rounded text-foreground font-mono">↑↓</kbd>
            <span className="text-muted-foreground">Navigate List</span>
          </div>
          <span className="text-border">•</span>
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 bg-muted border border-border rounded text-foreground font-mono">Enter</kbd>
            <span className="text-muted-foreground">Select</span>
          </div>
          <span className="text-border">•</span>
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 bg-muted border border-border rounded text-foreground font-mono">1</kbd>
            <span className="text-muted-foreground">Good</span>
          </div>
          <span className="text-border">•</span>
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 bg-muted border border-border rounded text-foreground font-mono">2</kbd>
            <span className="text-muted-foreground">Bad</span>
          </div>
          <span className="text-border">•</span>
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 bg-muted border border-border rounded text-foreground font-mono">N</kbd>
            <span className="text-muted-foreground">Notes</span>
          </div>
          <span className="text-border">•</span>
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 bg-muted border border-border rounded text-foreground font-mono">← →</kbd>
            <span className="text-muted-foreground">Previous/Next</span>
          </div>
          <span className="text-border">•</span>
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 bg-muted border border-border rounded text-foreground font-mono">E</kbd>
            <span className="text-muted-foreground">Expand Tools</span>
          </div>
        </div>
      )}
    </AppShell>
  )
}
