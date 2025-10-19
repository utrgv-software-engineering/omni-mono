import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Trace, Span, SpanType, Conversation, AnalysisData, FailureCategory } from '../../../types'
import { api } from '../../../lib/api'

interface AnalysisStats {
  totalAnalyzed: number
  acceptable: number
  unacceptable: number
}

interface AnalysisGoals {
  tracesRequired: number  // Step 1: Total traces to analyze
  badTracesRequired: number  // Step 2: Bad traces to find
  badTracesRequiringNotes: number  // Step 3: Bad traces needing documentation
  badTracesRequiringCategories: number  // Step 4: Bad traces needing categorization
}

interface TraceStore {
  // Data
  conversations: Conversation[]
  selectedConversation: Conversation | null
  conversationTraces: Trace[]  // Traces in selected conversation
  traces: Trace[]
  selectedTrace: Trace | null
  spans: Span[]
  selectedSpan: Span | null
  
  // UI State
  searchQuery: string
  selectedProject: string | null  // null means "All Projects"
  selectedWorkflow: string | null
  filters: {
    spanTypes: SpanType[]
    hasErrors: boolean
    dateRange: [Date, Date] | null
    syntheticDims: Record<string, string[]>
  }
  
  // Analysis
  conversationAnalysis: Record<string, AnalysisData>  // groupId -> analysis data
  isAnalysisLoading: boolean
  analysisError: string | null
  
  // Failure Categories
  failureCategories: FailureCategory[]
  categoriesLoading: boolean
  categoriesError: string | null
  
  // Analysis Goals Configuration
  analysisGoals: AnalysisGoals
  
  // Actions
  setConversations: (conversations: Conversation[]) => void
  setSelectedConversation: (conversation: Conversation | null) => void
  setConversationTraces: (traces: Trace[]) => void
  setTraces: (traces: Trace[]) => void
  setSelectedTrace: (trace: Trace | null) => void
  setSpans: (spans: Span[]) => void
  setSelectedSpan: (span: Span | null) => void
  setSearchQuery: (query: string) => void
  setSelectedProject: (project: string | null) => void
  setSelectedWorkflow: (workflow: string | null) => void
  updateFilters: (filters: Partial<TraceStore['filters']>) => void
  addTrace: (trace: Trace) => void
  addSpan: (span: Span) => void
  loadProjectAnalysis: (project: string, workflowName?: string) => Promise<void>
  setConversationAnalysis: (groupId: string, analysis: AnalysisData) => Promise<void>
  getAnalysisStats: () => AnalysisStats
  
  // Category Actions
  loadFailureCategories: (project: string) => Promise<void>
  createFailureCategory: (name: string, description?: string) => Promise<FailureCategory>
  deleteFailureCategory: (categoryId: number) => Promise<void>
  updateAnalysisCategories: (groupId: string, categoryIds: number[]) => Promise<void>
  
  // Goals Actions
  setAnalysisGoals: (goals: AnalysisGoals) => void
}

export const useTraceStore = create<TraceStore>()(
  persist(
    (set, get) => ({
  // Initial state
  conversations: [],
  selectedConversation: null,
  conversationTraces: [],
  traces: [],
  selectedTrace: null,
  spans: [],
  selectedSpan: null,
  searchQuery: '',
  selectedProject: null,  // null = All Projects
  selectedWorkflow: null,
  filters: {
    spanTypes: [],
    hasErrors: false,
    dateRange: null,
    syntheticDims: {},
  },
  conversationAnalysis: {},
  isAnalysisLoading: false,
  analysisError: null,
  failureCategories: [],
  categoriesLoading: false,
  categoriesError: null,
  analysisGoals: {
    tracesRequired: 100,
    badTracesRequired: 20,
    badTracesRequiringNotes: 20,
    badTracesRequiringCategories: 20
  },

  // Actions
  setConversations: (conversations) => set({ conversations }),
  setSelectedConversation: (conversation) => set({ selectedConversation: conversation }),
  setConversationTraces: (traces) => set({ conversationTraces: traces }),
  setTraces: (traces) => set({ traces }),
  setSelectedTrace: (trace) => set({ selectedTrace: trace }),
  setSpans: (spans) => set({ spans }),
  setSelectedSpan: (span) => set({ selectedSpan: span }),
  setSearchQuery: (query) => set({ searchQuery: query }),
  setSelectedProject: (project) => set({ 
    selectedProject: project,
    selectedWorkflow: null,
    selectedConversation: null,
    selectedTrace: null,
    conversationTraces: [],
    spans: []
  }),
  setSelectedWorkflow: (workflow) => set({
    selectedWorkflow: workflow,
    selectedConversation: null,
    selectedTrace: null,
    conversationTraces: [],
    spans: []
  }),
  updateFilters: (filters) => set((state) => ({
    filters: { ...state.filters, ...filters }
  })),
  addTrace: (trace) => set((state) => ({
    traces: [trace, ...state.traces]
  })),
  addSpan: (span) => set((state) => ({
    spans: [...state.spans, span].sort((a, b) => a.sequence_number - b.sequence_number)
  })),
  
  loadProjectAnalysis: async (project: string, workflowName?: string) => {
    set({ isAnalysisLoading: true, analysisError: null })
    try {
      const wf = workflowName ?? get().selectedWorkflow ?? undefined
      const analysis = await api.getAllAnalysis(project, wf)
      
      // Load categories for each analysis
      const analysisWithCategories: Record<string, AnalysisData> = {}
      for (const [groupId, data] of Object.entries(analysis)) {
        try {
          const { categoryIds } = await api.getAnalysisCategories(project, groupId)
          analysisWithCategories[groupId] = { ...data, categories: categoryIds }
        } catch {
          // If categories fail to load, just use the analysis without them
          analysisWithCategories[groupId] = { ...data, categories: [] }
        }
      }
      
      set({ conversationAnalysis: analysisWithCategories, isAnalysisLoading: false })
    } catch (error) {
      console.error('Failed to load analysis:', error)
      set({ 
        analysisError: error instanceof Error ? error.message : 'Failed to load analysis',
        isAnalysisLoading: false 
      })
    }
  },
  
  setConversationAnalysis: async (groupId: string, analysis: AnalysisData) => {
    const project = get().selectedProject
    if (!project) {
      console.error('No project selected')
      return
    }
    
    // Optimistically update local state
    set((state) => ({
      conversationAnalysis: { ...state.conversationAnalysis, [groupId]: analysis }
    }))
    
    try {
      // Persist to backend
      await api.updateAnalysis(project, groupId, analysis)
    } catch (error) {
      console.error('Failed to save analysis:', error)
      // Revert on error (optional - could also show a toast)
      const previousAnalysis = get().conversationAnalysis
      delete previousAnalysis[groupId]
      set({ 
        conversationAnalysis: previousAnalysis,
        analysisError: error instanceof Error ? error.message : 'Failed to save analysis'
      })
    }
  },
  
  getAnalysisStats: () => {
    const analysis = get().conversationAnalysis
    const conversations = get().conversations
    const allowed = new Set(conversations.map(c => c.group_id))
    const stats: AnalysisStats = {
      totalAnalyzed: 0,
      acceptable: 0,
      unacceptable: 0
    }
    
    Object.entries(analysis).forEach(([groupId, item]) => {
      if (!allowed.has(groupId)) return
      if (item.markedForAnalysis && item.judgment) {
        stats.totalAnalyzed++
        if (item.judgment === 'acceptable') {
          stats.acceptable++
        } else if (item.judgment === 'unacceptable') {
          stats.unacceptable++
        }
      }
    })
    
    return stats
  },
  
  loadFailureCategories: async (project: string) => {
    set({ categoriesLoading: true, categoriesError: null })
    try {
      const categories = await api.getFailureCategories(project)
      set({ failureCategories: categories, categoriesLoading: false })
    } catch (error) {
      console.error('Failed to load categories:', error)
      set({ 
        categoriesError: error instanceof Error ? error.message : 'Failed to load categories',
        categoriesLoading: false 
      })
    }
  },
  
  createFailureCategory: async (name: string, description?: string) => {
    const project = get().selectedProject
    if (!project) {
      throw new Error('No project selected')
    }
    
    try {
      const newCategory = await api.createFailureCategory(project, { name, description })
      set((state) => ({
        failureCategories: [...state.failureCategories, newCategory].sort((a, b) => 
          a.name.localeCompare(b.name)
        )
      }))
      return newCategory
    } catch (error) {
      console.error('Failed to create category:', error)
      throw error
    }
  },
  
  deleteFailureCategory: async (categoryId: number) => {
    const project = get().selectedProject
    if (!project) {
      throw new Error('No project selected')
    }
    
    try {
      await api.deleteFailureCategory(project, categoryId)
      
      // Remove from local state
      set((state) => ({
        failureCategories: state.failureCategories.filter(cat => cat.id !== categoryId)
      }))
      
      // Also remove from any conversations that had this category
      set((state) => {
        const updatedAnalysis = { ...state.conversationAnalysis }
        Object.keys(updatedAnalysis).forEach(groupId => {
          const analysis = updatedAnalysis[groupId]
          if (analysis.categories && analysis.categories.includes(categoryId)) {
            updatedAnalysis[groupId] = {
              ...analysis,
              categories: analysis.categories.filter(id => id !== categoryId)
            }
          }
        })
        return { conversationAnalysis: updatedAnalysis }
      })
    } catch (error) {
      console.error('Failed to delete category:', error)
      throw error
    }
  },
  
  updateAnalysisCategories: async (groupId: string, categoryIds: number[]) => {
    const project = get().selectedProject
    if (!project) {
      console.error('No project selected')
      return
    }
    
    // Update local state immediately
    set((state) => {
      const currentAnalysis = state.conversationAnalysis[groupId]
      if (currentAnalysis) {
        return {
          conversationAnalysis: {
            ...state.conversationAnalysis,
            [groupId]: { ...currentAnalysis, categories: categoryIds }
          }
        }
      }
      return state
    })
    
    try {
      await api.updateAnalysisCategories(project, groupId, categoryIds)
    } catch (error) {
      console.error('Failed to update categories:', error)
      // Revert on error
      const previousAnalysis = get().conversationAnalysis
      set({ conversationAnalysis: previousAnalysis })
    }
  },
  
  setAnalysisGoals: (goals: AnalysisGoals) => {
    set({ analysisGoals: goals })
  }
}),
    {
      name: 'agentio-ui-state', // localStorage key
      partialize: (state) => ({ 
        // Persist selected workflow and analysis goals
        selectedWorkflow: state.selectedWorkflow,
        analysisGoals: state.analysisGoals
      }),
    }
  )
)
