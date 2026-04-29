import React, { useState, useEffect, useRef } from 'react'
import { MessageSquare, AlertCircle, Edit3, ThumbsUp, ThumbsDown, Circle, Filter, ChevronDown, ChevronRight, Archive, Lock, User } from 'lucide-react'
import type { Conversation } from '../../../types'
import { useTraceStore } from '../store/traceStore'
import { formatDistanceToNow } from 'date-fns'
import { useSyntheticDimensions, useProjectAgents } from '../hooks/useTraces'

interface ConversationListProps {
  conversations: Conversation[]
  onConversationSelect: (conversation: Conversation) => void
  selectedConversationId?: string | null
}

type FilterType = 'all' | 'unanalyzed' | 'good' | 'bad' | 'annotated' | 'bad-no-notes'

export function ConversationList({ conversations, onConversationSelect, selectedConversationId }: ConversationListProps) {
  const { selectedConversation, searchQuery, conversationAnalysis, filters, updateFilters, selectedProject, selectedWorkflow, selectedAgent, setSelectedAgent, evaluationResults, loadEvaluationResults } = useTraceStore()
  const [activeFilter, setActiveFilter] = useState<FilterType>('all')
  const [focusedIndex, setFocusedIndex] = useState<number>(0)
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  const { data: syntheticDimsData } = useSyntheticDimensions(selectedProject, selectedWorkflow)
  const syntheticDimensions = syntheticDimsData?.dimensions || []
  const { data: projectAgentsData } = useProjectAgents(selectedProject)
  const projectAgents = projectAgentsData?.agents || []
  const [showFilters, setShowFilters] = useState(false)
  const [expandedDims, setExpandedDims] = useState<Record<string, boolean>>({})
  
  // Auto-select first project if none selected
  // Auto-selection handled in header now

  // Load evaluation results on mount and when project changes
  useEffect(() => {
    loadEvaluationResults()
  }, [loadEvaluationResults, selectedProject])

  const activeConversationId = selectedConversationId ?? selectedConversation?.group_id ?? null

  // Filter conversations based on search and analysis status
  const filteredConversations = React.useMemo(() => {
    let filtered = conversations
    
    // Apply search filter
    if (searchQuery) {
      const query = searchQuery.toLowerCase()
      filtered = filtered.filter(conv => 
        conv.first_input?.toLowerCase().includes(query) ||
        conv.workflow_name?.toLowerCase().includes(query) ||
        conv.group_id.toLowerCase().includes(query)
      )
    }
    
    // Apply analysis status filter
    if (activeFilter !== 'all') {
      filtered = filtered.filter(conv => {
        const analysis = conversationAnalysis[conv.group_id]
        const hasNotes = analysis?.notes && analysis.notes.trim().length > 0
        const judgment = analysis?.judgment
        const isMarked = !!analysis?.markedForAnalysis
        
        switch (activeFilter) {
          case 'unanalyzed':
            return !isMarked && !judgment && !hasNotes
          case 'good':
            return judgment === 'acceptable'
          case 'bad':
            return judgment === 'unacceptable'
          case 'annotated':
            return hasNotes
          case 'bad-no-notes':
            return judgment === 'unacceptable' && !hasNotes
          default:
            return true
        }
      })
    }
    
    return filtered
  }, [conversations, searchQuery, activeFilter, conversationAnalysis])

  // Synthetic dimensions loaded from API
  
  // Update focused index when selected conversation changes or filteredConversations change
  useEffect(() => {
    const index = activeConversationId
      ? filteredConversations.findIndex(c => c.group_id === activeConversationId)
      : 0
    setFocusedIndex(index !== -1 ? index : 0)
  }, [activeConversationId, filteredConversations])
  
  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Only handle navigation when not in an input field
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return
      }
      
      const conversationCount = filteredConversations.length
      if (conversationCount === 0) return
      
      switch (e.key) {
        case 'ArrowUp':
          e.preventDefault()
          setFocusedIndex(prev => Math.max(0, prev - 1))
          break
        case 'ArrowDown':
          e.preventDefault()
          setFocusedIndex(prev => Math.min(conversationCount - 1, prev + 1))
          break
        case 'ArrowLeft': {
          e.preventDefault()
          if (conversationCount === 0 || !activeConversationId) return
          const prevIndex = focusedIndex > 0 ? focusedIndex - 1 : conversationCount - 1
          setFocusedIndex(prevIndex)
          const target = filteredConversations[prevIndex]
          if (target) onConversationSelect(target)
          break
        }
        case 'ArrowRight': {
          e.preventDefault()
          if (conversationCount === 0 || !activeConversationId) return
          const nextIndex = focusedIndex < conversationCount - 1 ? focusedIndex + 1 : 0
          setFocusedIndex(nextIndex)
          const target = filteredConversations[nextIndex]
          if (target) onConversationSelect(target)
          break
        }
        case 'Enter':
          e.preventDefault()
          if (filteredConversations[focusedIndex]) {
            onConversationSelect(filteredConversations[focusedIndex])
          }
          break
      }
    }
    
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [filteredConversations, focusedIndex, onConversationSelect, activeConversationId])
  
  // Scroll focused item into view
  useEffect(() => {
    const focusedItem = itemRefs.current[focusedIndex]
    if (focusedItem) {
      focusedItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [focusedIndex])
  
  return (
    <div className="h-full flex flex-col">
      
      
      {/* Search and Filters */}
      <div className="p-3 border-b border-border space-y-2">
        <input
          type="text"
          placeholder="Search conversations..."
          className="w-full px-3 py-2 text-sm bg-muted/50 rounded-md outline-none focus:bg-muted"
          value={searchQuery}
          onChange={(e) => useTraceStore.getState().setSearchQuery(e.target.value)}
        />
        <div className="flex flex-wrap gap-1.5">
          {activeFilter !== 'all' && (
            <button
              onClick={() => setActiveFilter('all')}
              className="px-2 py-0.5 text-xs rounded-full bg-muted border border-border hover:bg-muted/70"
            >
              Status: {activeFilter.replace('-', ' ')} ×
            </button>
          )}
          {filters.hasErrors && (
            <button
              onClick={() => updateFilters({ hasErrors: false })}
              className="px-2 py-0.5 text-xs rounded-full bg-muted border border-border hover:bg-muted/70"
            >
              Errors only ×
            </button>
          )}
          {selectedAgent && (
            <button
              onClick={() => setSelectedAgent(null)}
              className="px-2 py-0.5 text-xs rounded-full bg-muted border border-border hover:bg-muted/70"
            >
              Agent: {selectedAgent} ×
            </button>
          )}
          {syntheticDimensions.flatMap(dim => {
            const selected = filters.syntheticDims[dim.name] || []
            return selected.map(val => {
              const v = dim.values.find(x => x.name === val)
              return (
                <button
                  key={`${dim.name}:${val}`}
                  onClick={() => {
                    const curr = filters.syntheticDims
                    const next = (curr[dim.name] || []).filter(x => x !== val)
                    const clone = { ...curr }
                    if (next.length) clone[dim.name] = next; else delete clone[dim.name]
                    updateFilters({ syntheticDims: clone })
                  }}
                  className="px-2 py-0.5 text-xs rounded-full bg-muted border border-border hover:bg-muted/70"
                >
                  {dim.label}: {v?.label || val} ×
                </button>
              )
            })
          })}
        </div>

        <button
          onClick={() => setShowFilters(v => !v)}
          className="mt-1 w-full flex items-center gap-2 px-2 py-1 text-xs bg-muted/50 rounded-md border border-border hover:bg-muted"
        >
          <Filter className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="flex-1 text-left">Filters</span>
          {showFilters ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        </button>

        {showFilters && (
          <div className="space-y-3 pt-1">
            <div className="flex items-center gap-1">
              {(['all','unanalyzed','good','bad','bad-no-notes'] as FilterType[]).map(f => (
                <button
                  key={f}
                  onClick={() => setActiveFilter(f)}
                  className={`px-2 py-1 text-xs rounded border ${activeFilter === f ? 'bg-background border-border' : 'bg-muted/40 border-border hover:bg-muted'}`}
                >
                  {f === 'bad-no-notes' ? 'Needs Notes' : f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={filters.hasErrors}
                onChange={(e) => updateFilters({ hasErrors: e.target.checked })}
                className="h-3 w-3 rounded border-input"
              />
              Errors only
            </label>
            {projectAgents.length > 0 && (
              <label className="flex items-center gap-2 text-xs">
                <span className="text-muted-foreground">Agent</span>
                <select
                  value={selectedAgent ?? ''}
                  onChange={(e) => setSelectedAgent(e.target.value || null)}
                  className="flex-1 px-2 py-1 text-xs bg-muted/40 rounded border border-border outline-none focus:bg-muted"
                >
                  <option value="">All agents</option>
                  {projectAgents.map(a => (
                    <option key={a.agent_slug} value={a.agent_slug}>
                      {a.agent_slug}
                      {typeof a.session_count === 'number' ? ` (${a.session_count})` : ''}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {syntheticDimensions.length > 0 && (
              <div className="space-y-2">
                {syntheticDimensions.map(dim => {
                  const selected = filters.syntheticDims[dim.name] || []
                  const isOpen = !!expandedDims[dim.name]
                  const selectedCount = selected.length
                  return (
                    <div key={dim.name} className="border border-border rounded-md">
                      <button
                        onClick={() => setExpandedDims(s => ({ ...s, [dim.name]: !isOpen }))}
                        className="w-full flex items-center justify-between px-2 py-1 text-xs bg-muted/40"
                      >
                        <span>{dim.label}{selectedCount ? ` (${selectedCount} selected)` : ''}</span>
                        {isOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                      </button>
                      {isOpen && (
                        <div className="p-2">
                          {selectedCount > 0 && (
                            <button
                              onClick={() => {
                                const curr = { ...filters.syntheticDims }
                                delete curr[dim.name]
                                updateFilters({ syntheticDims: curr })
                              }}
                              className="mb-2 text-[10px] text-muted-foreground hover:text-foreground"
                            >
                              Clear
                            </button>
                          )}
                          <div className="grid grid-cols-2 gap-1.5">
                            {dim.values.map(v => {
                              const checked = selected.includes(v.name)
                              return (
                                <label key={v.name} className="inline-flex items-center gap-1 px-1.5 py-1 text-[11px] rounded border cursor-pointer select-none bg-muted/30 border-border hover:bg-muted">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => {
                                      const curr = filters.syntheticDims
                                      const currForDim = curr[dim.name] || []
                                      const nextForDim = currForDim.includes(v.name)
                                        ? currForDim.filter(x => x !== v.name)
                                        : [...currForDim, v.name]
                                      updateFilters({ syntheticDims: { ...curr, [dim.name]: nextForDim } })
                                    }}
                                    className="h-3 w-3 rounded border-input"
                                  />
                                  <span className="truncate">{v.label}</span>
                                </label>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>
      
      {/* Conversation List */}
      <div className="flex-1 overflow-y-auto" ref={listRef}>
        {filteredConversations.length > 0 ? (
          <div className="p-2 space-y-1 pb-2">
            {filteredConversations.map((conversation, index) => {
              const isSelected = activeConversationId === conversation.group_id
              const isFocused = index === focusedIndex
              const timeAgo = formatDistanceToNow(new Date(conversation.created_at), { addSuffix: true })
              const analysis = conversationAnalysis[conversation.group_id]
              const evalResult = evaluationResults[conversation.group_id]

              // Simple status check
              const hasNotes = analysis?.notes && analysis.notes.trim().length > 0
              const judgment = analysis?.judgment
              const isMarked = !!analysis?.markedForAnalysis
              
              return (
                <button
                  key={conversation.group_id}
                  ref={el => { itemRefs.current[index] = el }}
                  className={`w-full text-left p-3 rounded-lg transition-colors border outline-none focus:outline-none ${
                    isSelected 
                      ? 'bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800' 
                      : isFocused
                      ? 'bg-muted border-border'
                      : 'border-transparent hover:bg-muted/50'
                  }`}
                  onClick={() => {
                    setFocusedIndex(index)
                    onConversationSelect(conversation)
                  }}
                  onMouseEnter={() => setFocusedIndex(index)}
                >
                  <div className="relative">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-sm truncate">
                          {conversation.first_input || conversation.workflow_name || 'No input'}
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                          <MessageSquare className="w-3 h-3 flex-shrink-0" />
                          <span>{conversation.trace_count} trace{conversation.trace_count !== 1 ? 's' : ''} • {timeAgo}</span>
                        </div>
                        {conversation.session?.user_id && (
                          <div className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground truncate">
                            <User className="w-3 h-3 flex-shrink-0" />
                            <span className="truncate">{conversation.session.user_id}</span>
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        {conversation.session?.hold && (
                          <span title="Legal hold">
                            <Lock className="w-3.5 h-3.5 text-amber-500" />
                          </span>
                        )}
                        {conversation.session?.archived && (
                          <span title="Archived">
                            <Archive className="w-3.5 h-3.5 text-muted-foreground" />
                          </span>
                        )}
                        {evalResult && (
                          <span
                            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                              evalResult.passed
                                ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                                : 'bg-red-500/10 text-red-600 dark:text-red-400'
                            }`}
                          >
                            {evalResult.passed ? 'PASS' : 'FAIL'}
                          </span>
                        )}
                        {conversation.has_errors && (
                          <AlertCircle className="w-4 h-4 text-destructive" />
                        )}
                      </div>
                    </div>

                    {/* Analysis status indicator in bottom right */}
                    {(hasNotes || judgment || isMarked) && (
                      <div className="absolute bottom-0 right-0 flex items-center gap-0.5">
                        {judgment === 'acceptable' ? (
                          <ThumbsUp className="w-3.5 h-3.5 text-green-500/80" />
                        ) : judgment === 'unacceptable' ? (
                          <ThumbsDown className="w-3.5 h-3.5 text-red-500/80" />
                        ) : isMarked ? (
                          <Circle className="w-3.5 h-3.5 text-muted-foreground/60" />
                        ) : null}
                        {hasNotes && (
                          <Edit3 className="w-3.5 h-3.5 text-blue-500/80" />
                        )}
                      </div>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            <div className="text-center">
              <MessageSquare className="w-12 h-12 mx-auto mb-2 opacity-20" />
              <p className="text-sm">No conversations found</p>
            </div>
          </div>
        )}
      </div>
      
      {/* Footer */}
      <div className="border-t border-border p-2 text-xs text-muted-foreground text-center">
        {filteredConversations.length} conversation{filteredConversations.length !== 1 ? 's' : ''}
      </div>
    </div>
  )
}
