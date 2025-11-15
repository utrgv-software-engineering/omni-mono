import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { StudioPage } from '../../shared/components/StudioPage'
import { LogsHeader } from '../../shared/components/LogsHeader'
import { ConversationList } from '../../features/traces/components/ConversationList'
import { TraceTable } from '../../features/traces/components/TraceTable'
import { ReviewView } from '../../features/review/ReviewView'
import { DebugView } from '../../features/debug/DebugView'
import { useTraceStore } from '../../features/traces/store/traceStore'
import { useConversations, useConversationTraces, useTrace, useProjects, useWorkflows } from '../../features/traces/hooks/useTraces'
import { useWebSocket } from '../../shared/hooks/useWebSocket'
import { useStudioProject } from '../../shared/hooks/useStudioProject'
import type { Conversation, Trace } from '../../types'
import { ROUTES, QUERY_PARAMS } from '../routes'

const normalizeParam = (value: string | null) => {
  if (!value) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

const isValidMode = (value: string | null): value is 'debug' | 'review' => value === 'debug' || value === 'review'

export function LogsPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const projectParam = normalizeParam(searchParams.get(QUERY_PARAMS.project))
  const workflowParam = normalizeParam(searchParams.get(QUERY_PARAMS.workflow))
  const conversationParam = normalizeParam(searchParams.get(QUERY_PARAMS.conversation))
  const traceParam = normalizeParam(searchParams.get(QUERY_PARAMS.trace))
  const modeParam = normalizeParam(searchParams.get(QUERY_PARAMS.mode))
  const viewMode: 'debug' | 'review' = isValidMode(modeParam) ? modeParam : 'review'

  const {
    setConversations,
    setConversationTraces,
    conversationTraces,
    setSpans,
    selectedConversation,
    setSelectedConversation,
    selectedTrace,
    setSelectedTrace,
    setSelectedProject,
    setSelectedWorkflow,
    loadProjectAnalysis,
  } = useTraceStore()

  const { data: studioProject } = useStudioProject()
  const manifestProject = normalizeParam(studioProject?.project ?? null)

  const { data: projectsData } = useProjects()
  const { data: workflowsData } = useWorkflows(projectParam)
  const { data: conversationsData } = useConversations({
    project: projectParam,
    workflow: workflowParam,
  })
  const { data: conversationTracesData } = useConversationTraces(projectParam, conversationParam)
  const { data: traceData } = useTrace(projectParam, traceParam)

  useWebSocket()

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
    if (!projectParam && manifestProject) {
      updateSearch({ [QUERY_PARAMS.project]: manifestProject }, { replace: true })
    }
  }, [projectParam, manifestProject, updateSearch])

  useEffect(() => {
    setSelectedProject(projectParam)
  }, [projectParam, setSelectedProject])

  useEffect(() => {
    setSelectedWorkflow(workflowParam)
  }, [workflowParam, setSelectedWorkflow])

  useEffect(() => {
    if (conversationsData?.conversations) {
      setConversations(conversationsData.conversations)
    }
  }, [conversationsData, setConversations])

  useEffect(() => {
    if (!conversationParam) {
      setSelectedConversation(null)
      setSelectedTrace(null)
      return
    }
    const found = conversationsData?.conversations?.find((item) => item.group_id === conversationParam) || null
    setSelectedConversation(found)
  }, [conversationParam, conversationsData, setSelectedConversation, setSelectedTrace])

  useEffect(() => {
    if (conversationTracesData?.traces) {
      setConversationTraces(conversationTracesData.traces)
    } else {
      setConversationTraces([])
    }
  }, [conversationTracesData, setConversationTraces])

  useEffect(() => {
    if (!traceParam) {
      setSelectedTrace(null)
      return
    }
    const found = conversationTraces.find((item) => item.trace_id === traceParam) || null
    setSelectedTrace(found)
  }, [traceParam, conversationTraces, setSelectedTrace])

  useEffect(() => {
    if (traceData?.spans) {
      setSpans(traceData.spans)
    } else {
      setSpans([])
    }
  }, [traceData, setSpans])

  useEffect(() => {
    if (projectParam && workflowParam) {
      loadProjectAnalysis(projectParam, workflowParam)
    }
  }, [projectParam, workflowParam, loadProjectAnalysis])

  useEffect(() => {
    if (viewMode !== 'review') {
      return
    }
    if (!conversationParam) {
      return
    }
    if (traceParam) {
      return
    }
    if (!conversationTracesData?.traces?.length) {
      return
    }
    const lastTrace = conversationTracesData.traces[conversationTracesData.traces.length - 1]
    updateSearch({ [QUERY_PARAMS.trace]: lastTrace.trace_id }, { replace: true })
  }, [viewMode, conversationParam, traceParam, conversationTracesData, updateSearch])

  const previousViewModeRef = useRef(viewMode)

  useEffect(() => {
    const previousMode = previousViewModeRef.current
    if (viewMode === 'debug' && previousMode !== 'debug') {
      updateSearch({ [QUERY_PARAMS.trace]: null })
    }
    previousViewModeRef.current = viewMode
  }, [viewMode, updateSearch])

  const handleBackToHome = () => {
    navigate(ROUTES.home)
  }

  const handleConversationSelect = (conversation: Conversation) => {
    updateSearch({
      [QUERY_PARAMS.conversation]: conversation.group_id,
      [QUERY_PARAMS.trace]: null,
      [QUERY_PARAMS.mode]: viewMode === 'debug' ? null : viewMode,
    })
  }

  const handleTraceSelect = (trace: Trace) => {
    updateSearch({
      [QUERY_PARAMS.trace]: trace.trace_id,
      [QUERY_PARAMS.mode]: viewMode === 'debug' ? 'debug' : 'review',
    })
  }

  const handleViewModeChange = (mode: 'debug' | 'review') => {
    if (mode === 'review') {
      updateSearch({ [QUERY_PARAMS.mode]: null })
    } else {
      updateSearch({ [QUERY_PARAMS.mode]: mode, [QUERY_PARAMS.trace]: null })
    }
  }

  const handleProjectChange = (project: string | null) => {
    updateSearch({
      [QUERY_PARAMS.project]: project,
      [QUERY_PARAMS.workflow]: null,
      [QUERY_PARAMS.conversation]: null,
      [QUERY_PARAMS.trace]: null,
    })
  }

  const handleWorkflowChange = (workflow: string | null) => {
    updateSearch({
      [QUERY_PARAMS.workflow]: workflow,
      [QUERY_PARAMS.conversation]: null,
      [QUERY_PARAMS.trace]: null,
    })
  }

  const viewContent = useMemo(() => {
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

    if (viewMode === 'review') {
      return <ReviewView spans={traceData?.spans || []} />
    }

    if (selectedTrace) {
      return <DebugView spans={traceData?.spans || []} onBack={() => updateSearch({ [QUERY_PARAMS.trace]: null })} />
    }

    return (
      <TraceTable
        traces={conversationTraces}
        onTraceSelect={handleTraceSelect}
        selectedTraceId={null}
      />
    )
  }, [conversationTraces, selectedConversation, selectedTrace, traceData?.spans, viewMode, updateSearch])

  const showShortcutsBar = Boolean(selectedConversation && viewMode === 'review')

  const sidebarContent = (
    <ConversationList
      conversations={conversationsData?.conversations || []}
      onConversationSelect={handleConversationSelect}
      selectedConversationId={conversationParam}
    />
  )

  const header = (
    <LogsHeader
      viewMode={viewMode}
      onViewModeChange={handleViewModeChange}
      showViewToggle={!!selectedConversation}
      showBackButton
      onBack={handleBackToHome}
      projects={projectsData?.projects || []}
      workflows={workflowsData?.workflows || []}
      selectedProject={projectParam}
      selectedWorkflow={workflowParam}
      onProjectChange={handleProjectChange}
      onWorkflowChange={handleWorkflowChange}
    />
  )

  return (
    <StudioPage
      section="logs"
      header={header}
      sidebarContent={sidebarContent}
      showNavigation={false}
      hasBottomBar={showShortcutsBar}
    >
      <div className="relative h-full">
        {viewContent}
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
      </div>
    </StudioPage>
  )
}
