import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'

export function useStudioProject() {
  return useQuery({
    queryKey: ['studio', 'project'],
    queryFn: () => api.getStudioProject(),
    staleTime: 60_000,
  })
}
