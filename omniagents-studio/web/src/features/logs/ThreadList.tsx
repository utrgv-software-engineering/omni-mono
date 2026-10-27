import type { OrganizationThread } from '@omniagents/client/rpc/conversation-organization'
import { formatDistanceToNow } from 'date-fns'
import { Archive, ChevronDown, ChevronRight, Circle, Edit3, FlaskConical, MessageSquare, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../../lib/api'
import type { AgentSummary } from '../../types'
import { useReviewStore } from '../review/reviewStore'
import { dimensionsOf, evaluationOf, reviewKey, threadTitle } from './threads'
import { useThreads } from './useThreads'

type StatusFilter = 'all' | 'unanalyzed' | 'good' | 'bad' | 'bad-no-notes' | 'sample' | 'suggested'
type KindFilter = 'all' | 'conversations' | 'evaluations'

const statusLabels: Record<StatusFilter, string> = {
  all: 'All',
  unanalyzed: 'Unanalyzed',
  good: 'Good',
  bad: 'Bad',
  'bad-no-notes': 'Needs notes',
  sample: 'Sample',
  suggested: 'Suggested',
}

interface ThreadListProps {
  agents: AgentSummary[]
  agentKey: string
  onSelectAgent: (agentKey: string) => void
  selectedThreadId: string | null
  onSelect: (thread: OrganizationThread) => void
  /** The threads the filters leave in view, in order. */
  onVisibleChange: (threads: OrganizationThread[]) => void
}

/**
 * An agent's conversations from the conversation store, with the review
 * filters: status (judgment and notes), kind (evaluation runs or other
 * conversations) and the scenario dimensions evaluation runs exercised.
 * ↑↓ move, Enter opens, ← → open the previous or next.
 */
export function ThreadList({ agents, agentKey, onSelectAgent, selectedThreadId, onSelect, onVisibleChange }: ThreadListProps) {
  const [query, setQuery] = useState('')
  const { threads, previews, loading, error, hasMore, loadMore } = useThreads(query)
  const conversationAnalysis = useReviewStore((state) => state.conversationAnalysis)
  const evaluationResults = useReviewStore((state) => state.evaluationResults)
  const loadEvaluationResults = useReviewStore((state) => state.loadEvaluationResults)
  const [status, setStatus] = useState<StatusFilter>('all')
  const [kind, setKind] = useState<KindFilter>('all')
  const [dimensionFilter, setDimensionFilter] = useState<Record<string, string[]>>({})
  const [openDimensions, setOpenDimensions] = useState<Record<string, boolean>>({})
  const [focused, setFocused] = useState(0)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  // The review assistant: a sample of unreviewed evaluation answers to read
  // next, and answers it suggests may show a failure mode.
  const queryClient = useQueryClient()
  const assist = useQuery({ queryKey: ['evaluation', 'review-assist'], queryFn: () => api.getReviewAssist(), retry: false })
  const draw = useMutation({
    mutationFn: () => api.drawReviewSample(20, Math.floor(Math.random() * 1e6)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evaluation', 'review-assist'] })
      setStatus('sample')
    },
  })
  const sampleOrder = useMemo(
    () => new Map((assist.data?.sample?.items ?? []).map((item, index) => [item.group_id, index])),
    [assist.data],
  )
  const suggested = useMemo(
    () => new Set((assist.data?.suggestions ?? []).filter((s) => s.status === 'pending').map((s) => s.group_id)),
    [assist.data],
  )

  useEffect(() => {
    void loadEvaluationResults()
  }, [loadEvaluationResults, agentKey])

  // The dimensions and values the loaded evaluation runs exercised.
  const dimensions = useMemo(() => {
    const values = new Map<string, Set<string>>()
    for (const thread of threads) {
      for (const [name, value] of Object.entries(dimensionsOf(thread))) {
        if (!values.has(name)) values.set(name, new Set())
        values.get(name)!.add(value)
      }
    }
    return [...values].map(([name, set]) => ({ name, values: [...set].sort() })).sort((a, b) => a.name.localeCompare(b.name))
  }, [threads])

  const visible = useMemo(
    () =>
      threads
        .filter((thread) => {
        const evaluation = evaluationOf(thread)
        if (kind === 'evaluations' && !evaluation) return false
        if (kind === 'conversations' && evaluation) return false
        const exercised = dimensionsOf(thread)
        for (const [name, wanted] of Object.entries(dimensionFilter)) {
          if (wanted.length && !wanted.includes(exercised[name] ?? '')) return false
        }
        if (status === 'all') return true
        const analysis = conversationAnalysis[reviewKey(thread)]
        const hasNotes = !!analysis?.notes?.trim()
        switch (status) {
          case 'unanalyzed':
            return !analysis?.markedForAnalysis && !analysis?.judgment && !hasNotes
          case 'good':
            return analysis?.judgment === 'acceptable'
          case 'bad':
            return analysis?.judgment === 'unacceptable'
          case 'bad-no-notes':
            return analysis?.judgment === 'unacceptable' && !hasNotes
          case 'sample':
            return sampleOrder.has(reviewKey(thread)) && !analysis?.judgment
          case 'suggested':
            return suggested.has(reviewKey(thread))
        }
      })
        // A sample is read in the order it was drawn.
        .sort((a, b) =>
          status === 'sample' ? (sampleOrder.get(reviewKey(a)) ?? 0) - (sampleOrder.get(reviewKey(b)) ?? 0) : 0,
        ),
    [threads, kind, dimensionFilter, status, conversationAnalysis, sampleOrder, suggested],
  )

  useEffect(() => onVisibleChange(visible), [visible, onVisibleChange])

  useEffect(() => {
    const index = selectedThreadId ? visible.findIndex((thread) => thread.thread_id === selectedThreadId) : 0
    setFocused(index >= 0 ? index : 0)
  }, [selectedThreadId, visible])

  useEffect(() => {
    itemRefs.current[focused]?.scrollIntoView({ block: 'nearest' })
  }, [focused])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      ) {
        return
      }
      const count = visible.length
      if (count === 0) return
      const open = (index: number) => {
        setFocused(index)
        const thread = visible[index]
        if (thread) onSelect(thread)
      }
      switch (event.key) {
        case 'ArrowUp':
          event.preventDefault()
          setFocused((index) => Math.max(0, index - 1))
          break
        case 'ArrowDown':
          event.preventDefault()
          setFocused((index) => Math.min(count - 1, index + 1))
          break
        case 'ArrowLeft':
          if (!selectedThreadId) return
          event.preventDefault()
          open(focused > 0 ? focused - 1 : count - 1)
          break
        case 'ArrowRight':
          if (!selectedThreadId) return
          event.preventDefault()
          open(focused < count - 1 ? focused + 1 : 0)
          break
        case 'Enter':
          event.preventDefault()
          open(focused)
          break
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [visible, focused, onSelect, selectedThreadId])

  const filtered = status !== 'all' || kind !== 'all' || Object.values(dimensionFilter).some((values) => values.length > 0)

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-3 border-b border-border p-3">
        {agents.length > 1 ? (
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Agent</span>
            <select
              value={agentKey}
              onChange={(event) => onSelectAgent(event.target.value)}
              className="rounded-sm border border-border bg-muted/40 px-2 py-1 text-xs outline-hidden focus:bg-muted"
            >
              {agents.map((agent) => (
                <option key={agent.key} value={agent.key}>
                  {agent.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <input
          type="search"
          placeholder="Search conversations…"
          aria-label="Search conversations"
          className="w-full rounded-md bg-muted/50 px-3 py-2 text-sm outline-hidden focus:bg-muted"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Status</span>
            {filtered ? (
              <button
                type="button"
                onClick={() => {
                  setStatus('all')
                  setKind('all')
                  setDimensionFilter({})
                }}
                className="text-[11px] text-muted-foreground hover:text-foreground"
              >
                Reset all
              </button>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-1">
            {(Object.keys(statusLabels) as StatusFilter[]).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setStatus(value)}
                className={`rounded-sm border px-2 py-1 text-xs ${status === value ? 'border-border bg-background font-medium' : 'border-border bg-muted/40 hover:bg-muted'}`}
              >
                {statusLabels[value]}
              </button>
            ))}
          </div>
          {status === 'sample' || status === 'suggested' ? (
            <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
              <span>
                {status === 'sample'
                  ? assist.data?.sample
                    ? `Unreviewed evaluation answers from a sample of ${assist.data.sample.items.length}: cluster representatives and random picks`
                    : 'No sample drawn yet'
                  : 'Answers the review assistant thinks may show a failure mode'}
              </span>
              {status === 'sample' ? (
                <button
                  type="button"
                  className="shrink-0 rounded-sm border border-border px-2 py-0.5 hover:bg-muted"
                  disabled={draw.isPending}
                  onClick={() => draw.mutate()}
                >
                  {draw.isPending ? 'Drawing…' : 'Draw a sample'}
                </button>
              ) : null}
            </div>
          ) : null}
          {draw.error ? <p className="mt-1 text-[11px] text-destructive">{(draw.error as Error).message}</p> : null}
        </div>
        <div className="flex gap-1">
          {(['all', 'conversations', 'evaluations'] as KindFilter[]).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setKind(value)}
              className={`rounded-sm border px-2 py-1 text-xs capitalize ${kind === value ? 'border-border bg-background font-medium' : 'border-border bg-muted/40 hover:bg-muted'}`}
            >
              {value}
            </button>
          ))}
        </div>
        {dimensions.length > 0 ? (
          <div className="space-y-1.5">
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Dimensions</span>
            {dimensions.map((dimension) => {
              const selected = dimensionFilter[dimension.name] ?? []
              const open = !!openDimensions[dimension.name]
              return (
                <div key={dimension.name} className="rounded-md border border-border">
                  <button
                    type="button"
                    onClick={() => setOpenDimensions((current) => ({ ...current, [dimension.name]: !open }))}
                    className="flex w-full items-center justify-between rounded-t-md bg-muted/30 px-2 py-1 text-xs"
                  >
                    <span>
                      {dimension.name}
                      {selected.length ? ` · ${selected.length}` : ''}
                    </span>
                    {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                  </button>
                  {open ? (
                    <div className="grid grid-cols-2 gap-1.5 p-2">
                      {dimension.values.map((value) => (
                        <label
                          key={value}
                          className="inline-flex cursor-pointer items-center gap-1 rounded-sm border border-border bg-muted/30 px-1.5 py-1 text-[11px] select-none hover:bg-muted"
                        >
                          <input
                            type="checkbox"
                            checked={selected.includes(value)}
                            onChange={() =>
                              setDimensionFilter((current) => {
                                const values = current[dimension.name] ?? []
                                return {
                                  ...current,
                                  [dimension.name]: values.includes(value)
                                    ? values.filter((entry) => entry !== value)
                                    : [...values, value],
                                }
                              })
                            }
                            className="h-3 w-3 rounded-sm border-input"
                          />
                          <span className="truncate">{value}</span>
                        </label>
                      ))}
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : null}
      </div>

      <div className="flex-1 overflow-y-auto">
        {error ? (
          <p className="p-4 text-sm text-destructive">{error}</p>
        ) : visible.length > 0 ? (
          <div className="space-y-1 p-2">
            {visible.map((thread, index) => {
              const selected = thread.thread_id === selectedThreadId
              const analysis = conversationAnalysis[reviewKey(thread)]
              const evaluation = evaluationOf(thread)
              const result = evaluationResults[thread.thread_id]
              const hasNotes = !!analysis?.notes?.trim()
              const preview = previews[thread.thread_id]
              return (
                <button
                  key={thread.thread_id}
                  type="button"
                  ref={(element) => {
                    itemRefs.current[index] = element
                  }}
                  data-thread-id={thread.thread_id}
                  className={`w-full rounded-lg border p-3 text-left transition-colors outline-hidden ${
                    selected
                      ? 'border-primary/40 bg-primary/10'
                      : index === focused
                        ? 'border-border bg-muted'
                        : 'border-transparent hover:bg-muted/50'
                  }`}
                  onClick={() => {
                    setFocused(index)
                    onSelect(thread)
                  }}
                  onMouseEnter={() => setFocused(index)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{threadTitle(thread)}</div>
                      {preview ? <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{preview}</div> : null}
                      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                        {evaluation ? <FlaskConical className="h-3 w-3 shrink-0" /> : <MessageSquare className="h-3 w-3 shrink-0" />}
                        <span>
                          {thread.turn_count} turn{thread.turn_count === 1 ? '' : 's'} ·{' '}
                          {formatDistanceToNow(new Date(thread.updated_at * 1000), { addSuffix: true })}
                        </span>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      {thread.status === 'archived' ? (
                        <span title="Archived">
                          <Archive className="h-3.5 w-3.5 text-muted-foreground" />
                        </span>
                      ) : null}
                      {result ? (
                        <span
                          className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold ${
                            result.passed ? 'bg-success/10 text-success' : 'bg-destructive/10 text-destructive'
                          }`}
                        >
                          {result.passed ? 'PASS' : 'FAIL'}
                        </span>
                      ) : null}
                      {analysis?.judgment === 'acceptable' ? (
                        <ThumbsUp className="h-3.5 w-3.5 text-success" />
                      ) : analysis?.judgment === 'unacceptable' ? (
                        <ThumbsDown className="h-3.5 w-3.5 text-destructive" />
                      ) : analysis?.markedForAnalysis ? (
                        <Circle className="h-3.5 w-3.5 text-muted-foreground/60" />
                      ) : null}
                      {hasNotes ? <Edit3 className="h-3.5 w-3.5 text-primary" /> : null}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <div className="text-center">
              <MessageSquare className="mx-auto mb-2 h-12 w-12 opacity-20" />
              <p className="text-sm">{loading ? 'Loading conversations…' : 'No conversations found'}</p>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center justify-center gap-3 border-t border-border p-2 text-xs text-muted-foreground">
        <span>
          {visible.length} conversation{visible.length === 1 ? '' : 's'}
        </span>
        {hasMore ? (
          <button type="button" onClick={loadMore} disabled={loading} className="hover:text-foreground disabled:opacity-50">
            Load more
          </button>
        ) : null}
      </div>
    </div>
  )
}
