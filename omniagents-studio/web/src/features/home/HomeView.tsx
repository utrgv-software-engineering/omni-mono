import { ArrowRight } from 'lucide-react'

interface HomeViewProps {
  onOpenLogs: () => void
  onOpenPlayground: () => void
  onOpenAgents: () => void
  onOpenEvaluation: () => void
}

export function HomeView({ onOpenLogs, onOpenPlayground, onOpenAgents, onOpenEvaluation }: HomeViewProps) {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <div className="space-y-8 text-center">
        <div className="space-y-2">
          <h2 className="text-3xl font-semibold">Welcome to OmniAgents Studio</h2>
          <p className="text-sm text-muted-foreground">
            Choose a workspace to get started with agents, logs, or the interactive playground.
          </p>
        </div>
        <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <button
            type="button"
            onClick={onOpenPlayground}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <span>Launch Playground</span>
            <ArrowRight className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onOpenEvaluation}
            className="inline-flex items-center gap-2 rounded-md border border-border px-5 py-3 text-sm font-medium transition-colors hover:bg-muted"
          >
            <span>Manage Evaluation</span>
            <ArrowRight className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onOpenAgents}
            className="inline-flex items-center gap-2 rounded-md border border-border px-5 py-3 text-sm font-medium transition-colors hover:bg-muted"
          >
            <span>Browse Agents</span>
            <ArrowRight className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onOpenLogs}
            className="inline-flex items-center gap-2 rounded-md border border-border px-5 py-3 text-sm font-medium transition-colors hover:bg-muted"
          >
            <span>View Logs</span>
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
