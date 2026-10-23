import { lazy, Suspense } from 'react'

// Logs renders conversations with the shared chat UI, most of Studio's
// code; only the pages that show conversations load it.
const LogsWorkspace = lazy(() =>
  import('../../features/logs/LogsWorkspace').then((module) => ({ default: module.LogsWorkspace })),
)

export function LogsPage() {
  return (
    <Suspense fallback={null}>
      <LogsWorkspace />
    </Suspense>
  )
}
