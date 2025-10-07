import { useQuery } from '@tanstack/react-query'
import { api } from '../../../lib/api'
import { useTraceStore } from '../store/traceStore'

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
    staleTime: 1000 * 60 * 5, // Consider data fresh for 5 minutes
  })
}

export function useConversations() {
  const { filters, searchQuery, selectedProject, selectedWorkflow } = useTraceStore()
  
  return useQuery({
    queryKey: ['conversations', filters, searchQuery, selectedProject, selectedWorkflow],
    queryFn: () => {
      if (!selectedProject || !selectedWorkflow) {
        return { conversations: [], total: 0 }
      }
      const dims = Object.entries(filters.syntheticDims || {})
        .flatMap(([k, arr]) => (Array.isArray(arr) ? arr : []).filter(Boolean).map(v => `${k}:${v}`))
      return api.getConversations({
        project: selectedProject,
        limit: 100,
        search: searchQuery || undefined,
        has_errors: filters.hasErrors || undefined,
        workflow_name: selectedWorkflow || undefined,
        dims: dims.length ? dims : undefined,
      })
    },
    refetchInterval: 5000, // Refetch every 5 seconds
    enabled: !!(selectedProject && selectedWorkflow),
  })
}

export function useConversationTraces(groupId: string | null) {
  const { selectedProject } = useTraceStore()
  
  return useQuery({
    queryKey: ['conversation-traces', selectedProject, groupId],
    queryFn: () => {
      if (!selectedProject || !groupId) {
        return { traces: [], group_id: '', total: 0 }
      }
      return api.getConversationTraces(selectedProject, groupId)
    },
    enabled: !!(selectedProject && groupId),
  })
}

export function useTraces() {
  const { filters, searchQuery, selectedProject } = useTraceStore()
  
  return useQuery({
    queryKey: ['traces', filters, searchQuery, selectedProject],
    queryFn: () => {
      if (!selectedProject) {
        return { traces: [], total: 0 }  // No project selected
      }
      return api.getTraces({
        project: selectedProject,
        limit: 100,
        search: searchQuery || undefined,
        has_errors: filters.hasErrors || undefined,
        span_type: filters.spanTypes[0] || undefined // TODO: support multiple
      })
    },
    refetchInterval: 5000, // Refetch every 5 seconds
    enabled: !!selectedProject,  // Only fetch when a project is selected
  })
}

export function useTrace(project: string | null, traceId: string | null) {
  return useQuery({
    queryKey: ['trace', project, traceId],
    queryFn: () => (project && traceId) ? api.getTrace(project, traceId) : null,
    enabled: !!(project && traceId),
  })
}

export function useWorkflows() {
  const { selectedProject } = useTraceStore()
  return useQuery({
    queryKey: ['workflows', selectedProject],
    queryFn: () => selectedProject ? api.getWorkflows(selectedProject) : { workflows: [] },
    enabled: !!selectedProject,
    staleTime: 1000 * 60 * 5,
  })
}

export function useSyntheticDimensions() {
  const { selectedProject, selectedWorkflow } = useTraceStore()
  return useQuery({
    queryKey: ['synthetic-dimensions', selectedProject, selectedWorkflow],
    queryFn: () => selectedProject && selectedWorkflow
      ? api.getSyntheticDimensions(selectedProject, selectedWorkflow)
      : { dimensions: [] },
    enabled: !!(selectedProject && selectedWorkflow),
    staleTime: 1000 * 60 * 5,
  })
}
