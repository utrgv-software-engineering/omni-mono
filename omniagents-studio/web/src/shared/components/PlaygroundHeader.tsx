import { ArrowLeft, Sparkles } from 'lucide-react'

import type { AgentSummary } from '../../types'
import { ThemeToggle } from './ThemeToggle'

interface PlaygroundHeaderProps {
  onBack: () => void
  agents: AgentSummary[]
  agentKey: string | null
  onSelectAgent: (agentKey: string) => void
}

export function PlaygroundHeader({ onBack, agents, agentKey, onSelectAgent }: PlaygroundHeaderProps) {
  return (
    <div className="border-b border-border bg-background">
      <div className="flex h-14 items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            aria-label="Back"
            className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Sparkles className="h-4 w-4" />
            </div>
            <div className="text-sm font-semibold">Playground</div>
          </div>
          {agents.length > 0 && agentKey ? (
            <select
              aria-label="Agent"
              value={agentKey}
              onChange={(event) => onSelectAgent(event.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 text-sm"
            >
              {agents.map((agent) => (
                <option key={agent.key} value={agent.key}>
                  {agent.label}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
        </div>
      </div>
    </div>
  )
}
