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
  
  // Fetch conversations
  const { data: conversationsData } = useConversations()
  
  // Fetch traces for selected conversation
  const { data: conversationTracesData } = useConversationTraces(selectedConversation?.group_id || null)
  
  
  // Fetch selected trace details
  const { data: traceData } = useTrace(selectedProject, selectedTrace?.trace_id || null)
  
  // Setup WebSocket for real-time updates
  useWebSocket()
  
  // Update store when conversations are fetched
  useEffect(() => {
    if (conversationsData?.conversations) {
      setConversations(conversationsData.conversations)
    }
  }, [conversationsData, setConversations])
  
  // Load analysis data when project changes
  useEffect(() => {
    if (selectedProject && selectedWorkflow) {
      loadProjectAnalysis(selectedProject, selectedWorkflow)
    }
  }, [selectedProject, selectedWorkflow, loadProjectAnalysis])
  
  // Update store when conversation traces are fetched
  useEffect(() => {
    if (conversationTracesData?.traces) {
      setConversationTraces(conversationTracesData.traces)
      // In review mode, automatically select the last trace to show full history
      if (viewMode === 'review' && conversationTracesData.traces.length > 0 && !selectedTrace) {
        const lastTrace = conversationTracesData.traces[conversationTracesData.traces.length - 1]
        setSelectedTrace(lastTrace)
      }
    }
  }, [conversationTracesData, setConversationTraces, viewMode, selectedTrace, setSelectedTrace])
  
  // Update spans when trace is selected
  useEffect(() => {
    if (traceData?.spans) {
      setSpans(traceData.spans)
    } else {
      setSpans([])
    }
  }, [traceData, setSpans])
  
  const handleConversationSelect = (conversation: Conversation) => {
    setSelectedConversation(conversation)
    setSelectedTrace(null) // Clear trace selection when conversation changes
    setViewMode('review') // Default to review mode when selecting a conversation
  }
  
  
  
  
  
  const handleTraceSelect = (trace: Trace) => {
    setSelectedTrace(trace)
  }
  
  // Determine what to show in main area
  const renderMainContent = () => {
    if (!selectedConversation) {
      // No conversation selected
      return (
        <div className="h-full flex items-center justify-center text-muted-foreground">
          <div className="text-center">
            <p className="text-lg">Select a conversation to get started</p>
            <p className="text-sm mt-2">Choose from the list on the left</p>
          </div>
        </div>
      )
    }
    
    // Conversation selected - show review or debug mode
    return (
      <div className="h-full">
        {viewMode === 'review' ? (
            // Review mode - show the last trace which contains full conversation history
            <ReviewView spans={traceData?.spans || []} />
          ) : (
            // Debug mode - show trace table, then timeline when trace selected
            selectedTrace ? (
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
            )
          )}
      </div>
    )
  }
  
  const handleViewModeChange = (mode: 'debug' | 'review') => {
    setViewMode(mode)
    // Clear trace selection when switching to debug mode
    if (mode === 'debug') {
      setSelectedTrace(null)
    }
    // Auto-select last trace when switching to review mode
    if (mode === 'review' && conversationTraces.length > 0) {
      const lastTrace = conversationTraces[conversationTraces.length - 1]
      setSelectedTrace(lastTrace)
    }
  }
  
  const showShortcutsBar = !!(selectedConversation && viewMode === 'review')
  
  return (
    <AppShell
      header={
        <AppHeader 
          viewMode={viewMode}
          onViewModeChange={handleViewModeChange}
          showViewToggle={!!selectedConversation}
        />
      }
      sidebar={
        <ConversationList
          conversations={conversationsData?.conversations || []}
          onConversationSelect={handleConversationSelect}
        />
      }
      hasBottomBar={showShortcutsBar}
    >
      {renderMainContent()}
      {/* Keyboard shortcuts hint bar */}
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
