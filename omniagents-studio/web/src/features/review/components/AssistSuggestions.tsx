import { Button } from '@omniagents/ui/button'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Sparkles, X } from 'lucide-react'

import { api } from '../../../lib/api'
import { useReviewStore } from '../reviewStore'

/**
 * Failure modes the review assistant thinks this answer may show. They are
 * not labels: accepting one labels the answer with the category (and marks
 * it bad if nobody judged it yet), dismissing one keeps it from being
 * suggested again.
 */
export function AssistSuggestions({ reviewKey }: { reviewKey: string | null }) {
  const queryClient = useQueryClient()
  const selectedProject = useReviewStore((state) => state.selectedProject)
  const loadProjectAnalysis = useReviewStore((state) => state.loadProjectAnalysis)
  const assist = useQuery({ queryKey: ['evaluation', 'review-assist'], queryFn: () => api.getReviewAssist(), retry: false })
  const categories = useQuery({ queryKey: ['evaluation', 'categories'], queryFn: () => api.getCategories(), retry: false })
  const decide = useMutation({
    mutationFn: ({ id, accept }: { id: string; accept: boolean }) => api.decideSuggestions([id], accept),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evaluation'] })
      if (selectedProject) void loadProjectAnalysis(selectedProject)
    },
  })
  const pending = (assist.data?.suggestions ?? []).filter((s) => s.group_id === reviewKey && s.status === 'pending')
  if (!reviewKey || pending.length === 0) return null
  const nameOf = (id: string) => categories.data?.categories.find((c) => c.id === id)?.name ?? id

  return (
    <div className="space-y-2 rounded-md border border-dashed border-violet-400/60 bg-violet-500/5 p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-violet-700 dark:text-violet-300">
        <Sparkles className="h-3.5 w-3.5" /> Suggested by the review assistant
      </div>
      {pending.map((suggestion) => (
        <div key={suggestion.id} className="space-y-1 text-xs">
          <div className="font-medium">{nameOf(suggestion.category)}</div>
          {suggestion.quote ? <blockquote className="border-l-2 border-violet-300 pl-2 italic">{suggestion.quote}</blockquote> : null}
          {suggestion.reason ? <p className="text-muted-foreground">{suggestion.reason}</p> : null}
          <div className="flex gap-2 pt-1">
            <Button
              size="xs"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: suggestion.id, accept: true })}
            >
              <Check /> Accept
            </Button>
            <Button
              size="xs"
              variant="ghost"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: suggestion.id, accept: false })}
            >
              <X /> Dismiss
            </Button>
          </div>
        </div>
      ))}
      {decide.error ? <p className="text-xs text-destructive">{(decide.error as Error).message}</p> : null}
    </div>
  )
}
