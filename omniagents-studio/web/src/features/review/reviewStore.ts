import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { api } from '../../lib/api'
import type { AnalysisData, FailureCategory } from '../../types'

interface AnalysisStats {
  totalAnalyzed: number
  acceptable: number
  unacceptable: number
}

interface AnalysisGoals {
  tracesRequired: number // Step 1: conversations to review
  badTracesRequired: number // Step 2: bad conversations to find
  badTracesRequiringNotes: number // Step 3: bad conversations needing notes
  badTracesRequiringCategories: number // Step 4: bad conversations needing a category
}

interface EvaluationResultLookup {
  passed: boolean
  measureCount: number
}

/**
 * Error-analysis state for reviewing conversations: judgments, notes and
 * failure categories, stored per project under each conversation's review
 * key (see `reviewKey`).
 */
interface ReviewStore {
  /** The trace project whose analysis database holds the reviews. */
  selectedProject: string | null
  /** Review keys of the conversations in view, for the progress stats. */
  reviewKeys: string[]
  selectedReviewKey: string | null
  conversationAnalysis: Record<string, AnalysisData>
  isAnalysisLoading: boolean
  analysisError: string | null
  failureCategories: FailureCategory[]
  categoriesLoading: boolean
  categoriesError: string | null
  analysisGoals: AnalysisGoals
  /** Evaluation outcomes by the conversation (session) they ran in. */
  evaluationResults: Record<string, EvaluationResultLookup>

  setSelectedProject: (project: string | null) => void
  setReviewKeys: (keys: string[]) => void
  setSelectedReviewKey: (key: string | null) => void
  loadProjectAnalysis: (project: string) => Promise<void>
  setConversationAnalysis: (key: string, analysis: AnalysisData) => Promise<void>
  getAnalysisStats: () => AnalysisStats
  /** The project's failure categories, with how many reviews use each. */
  loadFailureCategories: () => Promise<void>
  createFailureCategory: (name: string, description?: string) => Promise<FailureCategory>
  deleteFailureCategory: (categoryId: string) => Promise<void>
  updateAnalysisCategories: (key: string, categoryIds: string[]) => Promise<void>
  setAnalysisGoals: (goals: AnalysisGoals) => void
  loadEvaluationResults: () => Promise<void>
}

let analysisRequestToken = 0

export const useReviewStore = create<ReviewStore>()(
  persist(
    (set, get) => ({
      selectedProject: null,
      reviewKeys: [],
      selectedReviewKey: null,
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
        badTracesRequiringCategories: 20,
      },
      evaluationResults: {},

      setSelectedProject: (project) => set({ selectedProject: project }),
      setReviewKeys: (keys) => set({ reviewKeys: keys }),
      setSelectedReviewKey: (key) => set({ selectedReviewKey: key }),

      loadProjectAnalysis: async (project: string) => {
        const token = ++analysisRequestToken
        set({ isAnalysisLoading: true, analysisError: null })
        try {
          const analysis = await api.getAllAnalysis(project)
          if (token !== analysisRequestToken) return
          const settled = await Promise.all(
            Object.entries(analysis).map(async ([key, data]): Promise<[string, AnalysisData]> => {
              try {
                const { categoryIds } = await api.getAnalysisCategories(project, key)
                return [key, { ...data, categories: categoryIds }]
              } catch {
                return [key, { ...data, categories: [] }]
              }
            }),
          )
          if (token !== analysisRequestToken) return
          set({ conversationAnalysis: Object.fromEntries(settled), isAnalysisLoading: false })
        } catch (error) {
          if (token !== analysisRequestToken) return
          console.error('Failed to load analysis:', error)
          set({
            analysisError: error instanceof Error ? error.message : 'Failed to load analysis',
            isAnalysisLoading: false,
          })
        }
      },

      setConversationAnalysis: async (key: string, analysis: AnalysisData) => {
        const project = get().selectedProject
        if (!project) {
          console.error('No project selected')
          return
        }
        const previous = get().conversationAnalysis
        set({ conversationAnalysis: { ...previous, [key]: analysis } })
        try {
          await api.updateAnalysis(project, key, analysis)
        } catch (error) {
          console.error('Failed to save analysis:', error)
          set({
            conversationAnalysis: previous,
            analysisError: error instanceof Error ? error.message : 'Failed to save analysis',
          })
        }
      },

      getAnalysisStats: () => {
        const allowed = new Set(get().reviewKeys)
        const stats: AnalysisStats = { totalAnalyzed: 0, acceptable: 0, unacceptable: 0 }
        for (const [key, item] of Object.entries(get().conversationAnalysis)) {
          if (!allowed.has(key) || !item.markedForAnalysis || !item.judgment) continue
          stats.totalAnalyzed++
          if (item.judgment === 'acceptable') stats.acceptable++
          else if (item.judgment === 'unacceptable') stats.unacceptable++
        }
        return stats
      },

      loadFailureCategories: async () => {
        set({ categoriesLoading: true, categoriesError: null })
        try {
          const { categories } = await api.getCategories()
          set({ failureCategories: categories, categoriesLoading: false })
        } catch (error) {
          console.error('Failed to load categories:', error)
          set({
            categoriesError: error instanceof Error ? error.message : 'Failed to load categories',
            categoriesLoading: false,
          })
        }
      },

      createFailureCategory: async (name: string, description?: string) => {
        const project = get().selectedProject
        if (!project) {
          throw new Error('No project selected')
        }
        const created = await api.addCategory({ name, description })
        set((state) => ({
          failureCategories: [...state.failureCategories, created].sort((a, b) => a.name.localeCompare(b.name)),
        }))
        return created
      },

      deleteFailureCategory: async (categoryId: string) => {
        const project = get().selectedProject
        if (!project) {
          throw new Error('No project selected')
        }
        await api.removeCategory(categoryId)
        set((state) => ({
          failureCategories: state.failureCategories.filter((category) => category.id !== categoryId),
          conversationAnalysis: Object.fromEntries(
            Object.entries(state.conversationAnalysis).map(([key, analysis]) => [
              key,
              analysis.categories?.includes(categoryId)
                ? { ...analysis, categories: analysis.categories.filter((id) => id !== categoryId) }
                : analysis,
            ]),
          ),
        }))
      },

      updateAnalysisCategories: async (key: string, categoryIds: string[]) => {
        const project = get().selectedProject
        if (!project) {
          console.error('No project selected')
          return
        }
        const previous = get().conversationAnalysis
        const current = previous[key]
        if (current) {
          set({ conversationAnalysis: { ...previous, [key]: { ...current, categories: categoryIds } } })
        }
        try {
          await api.updateAnalysisCategories(project, key, categoryIds)
        } catch (error) {
          console.error('Failed to update categories:', error)
          set({ conversationAnalysis: previous })
        }
      },

      setAnalysisGoals: (goals: AnalysisGoals) => set({ analysisGoals: goals }),

      loadEvaluationResults: async () => {
        try {
          const { runs } = await api.getRuns()
          const lookup: Record<string, EvaluationResultLookup> = {}
          for (const entry of runs) {
            try {
              const saved = await api.getRun(entry.path)
              for (const run of saved.runs) {
                if (run.session_id) {
                  lookup[run.session_id] = {
                    passed: Boolean(run.passed),
                    measureCount: run.measures.length,
                  }
                }
              }
            } catch {
              // A run that cannot be read contributes nothing.
            }
          }
          set({ evaluationResults: lookup })
        } catch (error) {
          console.error('Failed to load evaluation results:', error)
        }
      },
    }),
    {
      name: 'studio-ui-state',
      partialize: (state) => ({ analysisGoals: state.analysisGoals }),
    },
  ),
)
