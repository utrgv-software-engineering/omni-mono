import { useQuery } from '@tanstack/react-query'

import { api } from '../../../lib/api'

/** The trace projects Studio has databases for (reviews live there too). */
export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
    staleTime: 1000 * 60 * 5,
  })
}

/** The traces recorded under one trace group (a conversation's runs). */
export function useConversationTraces(project: string | null, groupId: string | null) {
  return useQuery({
    queryKey: ['conversation-traces', project, groupId],
    queryFn: () => {
      if (!project || !groupId) {
        return { traces: [], group_id: '', total: 0 }
      }
      return api.getConversationTraces(project, groupId, { includePreview: true })
    },
    enabled: !!(project && groupId),
  })
}

export function useTrace(project: string | null, traceId: string | null) {
  return useQuery({
    queryKey: ['trace', project, traceId],
    queryFn: () => (project && traceId ? api.getTrace(project, traceId) : null),
    enabled: !!(project && traceId),
  })
}
