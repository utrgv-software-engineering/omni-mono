import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { StudioPage } from '../../shared/components/StudioPage'
import { PlaygroundHeader } from '../../shared/components/PlaygroundHeader'
import { PlaygroundView, type PlaygroundContext } from '../../features/playground/PlaygroundView'
import { ROUTES, QUERY_PARAMS } from '../routes'

const normalizeParam = (value: string | null) => {
  if (!value) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export function PlaygroundPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [context, setContext] = useState<PlaygroundContext>({
    projectName: '',
    sessionId: '',
    statusMessage: '',
    agentKey: null,
    agentLabel: '',
  })

  const projectParam = normalizeParam(searchParams.get(QUERY_PARAMS.project))
  const sessionParam = normalizeParam(searchParams.get(QUERY_PARAMS.session))

  useEffect(() => {
    const nextProject = projectParam ?? ''
    const nextSession = sessionParam ?? ''
    setContext((prev) => {
      if (prev.projectName === nextProject && prev.sessionId === nextSession) {
        return prev
      }
      return { ...prev, projectName: nextProject, sessionId: nextSession }
    })
  }, [projectParam, sessionParam])

  const updateSearch = useCallback(
    (updates: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams)
      let changed = false
      for (const [key, value] of Object.entries(updates)) {
        const current = next.get(key)
        if (value === null) {
          if (current !== null) {
            next.delete(key)
            changed = true
          }
        } else if (current !== value) {
          next.set(key, value)
          changed = true
        }
      }
      if (changed) {
        setSearchParams(next, { replace: true })
      }
    },
    [searchParams, setSearchParams],
  )

  const handleContextUpdate = useCallback(
    (next: PlaygroundContext) => {
      setContext((prev) => ({ ...prev, ...next }))

      const desiredProject = normalizeParam(next.projectName) ?? null
      const desiredSession = normalizeParam(next.sessionId) ?? null

      const updates: Record<string, string | null> = {}
      const currentProject = projectParam ?? null
      const currentSession = sessionParam ?? null

      if ((currentProject ?? null) !== (desiredProject ?? null)) {
        updates[QUERY_PARAMS.project] = desiredProject
      }
      if ((currentSession ?? null) !== (desiredSession ?? null)) {
        updates[QUERY_PARAMS.session] = desiredSession
      }

      if (Object.keys(updates).length > 0) {
        updateSearch(updates)
      }
    },
    [projectParam, sessionParam, updateSearch],
  )

  const handleBack = useCallback(() => {
    updateSearch({ [QUERY_PARAMS.project]: null, [QUERY_PARAMS.session]: null })
    navigate(ROUTES.home)
  }, [navigate, updateSearch])

  return (
    <StudioPage
      section="playground"
      header={<PlaygroundHeader onBack={handleBack} projectName={context.projectName} />}
      disableSidebar
    >
      <PlaygroundView onUpdateContext={handleContextUpdate} />
    </StudioPage>
  )
}
