import { ArrowLeft, Bot } from 'lucide-react'
import { ThemeToggle } from './ThemeToggle'

interface AgentHeaderProps {
  onBack: () => void
  agentLabel?: string
}

export function AgentHeader({ onBack, agentLabel }: AgentHeaderProps) {
  const subtitle = 'What this agent runs with'

  return (
    <div className="border-b border-border bg-background">
      <div className="flex h-14 items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Bot className="h-4 w-4" />
            </div>
            <div>
              <div className="text-sm font-semibold">{agentLabel ?? 'Agent'}</div>
              <div className="text-xs text-muted-foreground">{subtitle}</div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
        </div>
      </div>
    </div>
  )
}
