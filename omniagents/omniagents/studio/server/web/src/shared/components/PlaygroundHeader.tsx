import { ArrowLeft, Sparkles } from 'lucide-react'

interface PlaygroundHeaderProps {
  onBack: () => void
  projectName?: string
}

export function PlaygroundHeader({ onBack, projectName }: PlaygroundHeaderProps) {
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
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <div className="text-sm font-semibold">Playground</div>
              <div className="text-xs text-muted-foreground">
                {projectName ? `Project: ${projectName}` : 'Launch from a project to enable tooling'}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
