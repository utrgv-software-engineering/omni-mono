import { useQuery } from '@tanstack/react-query'
import { api } from '../../../lib/api'
import { useTraceStore } from '../store/traceStore'

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
    staleTime: 1000 * 60 * 5,
  })
}

interface UseConversationsArgs {
  project: string | null
  workflow: string | null
}

export function useConversations({ project, workflow }: UseConversationsArgs) {
  const { filters, searchQuery } = useTraceStore()

  return useQuery({
    queryKey: ['conversations', filters, searchQuery, project, workflow],
    queryFn: () => {
      if (!project || !workflow) {
        return { conversations: [], total: 0 }
      }
      const dims = Object.entries(filters.syntheticDims || {})
        .flatMap(([k, arr]) => (Array.isArray(arr) ? arr : []).filter(Boolean).map((v) => `${k}:${v}`))
      return api.getConversations({
        project,
        limit: 100,
        search: searchQuery || undefined,
        has_errors: filters.hasErrors || undefined,
        workflow_name: workflow || undefined,
        dims: dims.length ? dims : undefined,
      })
    },
    refetchInterval: 5000,
    enabled: !!(project && workflow),
  })
}

export function useConversationTraces(project: string | null, groupId: string | null) {
  return useQuery({
    queryKey: ['conversation-traces', project, groupId],
    queryFn: () => {
      if (!project || !groupId) {
        return { traces: [], group_id: '', total: 0 }
      }
      return api.getConversationTraces(project, groupId)
    },
    enabled: !!(project && groupId),
  })
}

export function useTraces(project: string | null) {
  const { filters, searchQuery } = useTraceStore()

  return useQuery({
    queryKey: ['traces', filters, searchQuery, project],
    queryFn: () => {
      if (!project) {
        return { traces: [], total: 0 }
      }
      return api.getTraces({
        project,
        limit: 100,
        search: searchQuery || undefined,
        has_errors: filters.hasErrors || undefined,
        span_type: filters.spanTypes[0] || undefined,
      })
    },
    refetchInterval: 5000,
    enabled: !!project,
  })
}

export function useTrace(project: string | null, traceId: string | null) {
  return useQuery({
    queryKey: ['trace', project, traceId],
    queryFn: () => (project && traceId ? api.getTrace(project, traceId) : null),
    enabled: !!(project && traceId),
  })
}

export function useWorkflows(project: string | null) {
  return useQuery({
    queryKey: ['workflows', project],
    queryFn: () => (project ? api.getWorkflows(project) : { workflows: [] }),
    enabled: !!project,
    staleTime: 1000 * 5,
    refetchInterval: 1000 * 5,
  })
}

export function useSyntheticDimensions(project: string | null, workflow: string | null) {
  return useQuery({
    queryKey: ['synthetic-dimensions', project, workflow],
    queryFn: () =>
      project && workflow ? api.getSyntheticDimensions(project, workflow) : { dimensions: [] },
    enabled: !!(project && workflow),
    staleTime: 1000 * 60 * 5,
  })
}
