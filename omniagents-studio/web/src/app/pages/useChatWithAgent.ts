import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'

import { QUERY_PARAMS, ROUTES } from '../routes'

/** Open a new Playground conversation with the agent. */
export function useChatWithAgent() {
  const navigate = useNavigate()
  return useCallback(
    (agentKey: string) => navigate(`${ROUTES.playground}?${QUERY_PARAMS.agent}=${encodeURIComponent(agentKey)}`),
    [navigate],
  )
}
