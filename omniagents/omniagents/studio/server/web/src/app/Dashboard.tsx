import { useState, useEffect } from 'react'
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
    loadProjectAnalysis
  } = useTraceStore()

  const [viewMode, setViewMode] = useState<'debug' | 'review'>('review')
  const [activeSection, setActiveSection] = useState<'home' | 'logs' | 'playground' | 'agents' | 'evaluation'>('home')
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
  const isLogs = activeSection === 'logs'
  const isPlayground = activeSection === 'playground'
  const isAgents = activeSection === 'agents'
  const isEvaluation = activeSection === 'evaluation'
  const [evaluationTab, setEvaluationTab] = useState<'dimensions' | 'scenarios' | 'datasets'>('dimensions')

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
    if (conversationsData?.conversations) {
      setConversations(conversationsData.conversations)
    }
  }, [isLogs, conversationsData, setConversations])

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
      if (viewMode === 'review' && conversationTracesData.traces.length > 0 && !selectedTrace) {
        const lastTrace = conversationTracesData.traces[conversationTracesData.traces.length - 1]
        setSelectedTrace(lastTrace)
      }
    }
  }, [isLogs, conversationTracesData, setConversationTraces, viewMode, selectedTrace, setSelectedTrace])

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

  const handleOpenLogs = () => {
    setActiveSection('logs')
  }

  const handleOpenPlayground = () => {
    setActiveSection('playground')
  }

  const handleOpenAgents = () => {
    setActiveSection('agents')
  }

  const handleOpenEvaluation = () => {
    setActiveSection('evaluation')
  }

  const handleBackToHome = () => {
    setActiveSection('home')
    setSelectedConversation(null)
    setSelectedTrace(null)
    setSpans([])
    setPlaygroundContext({ projectName: '', sessionId: '', statusMessage: '' })
    setSelectedAgent(null)
    setAgentContext({ projectName: '', sessionId: '', statusMessage: '', agentKey: null, agentLabel: '' })
    setEvaluationTab('dimensions')
  }

  const handleSelectAgent = (agentKey: string) => {
    setActiveSection('agents')
    setSelectedAgent(agentKey)
    setAgentContext({ projectName: '', sessionId: '', statusMessage: '', agentKey, agentLabel: '' })
  }

  const handleClearAgent = () => {
    setSelectedAgent(null)
    setAgentContext({ projectName: '', sessionId: '', statusMessage: '', agentKey: null, agentLabel: '' })
  }

  const handleConversationSelect = (conversation: Conversation) => {
    setSelectedConversation(conversation)
    setSelectedTrace(null)
    setViewMode('review')
  }

  const handleTraceSelect = (trace: Trace) => {
    setSelectedTrace(trace)
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
