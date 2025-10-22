import { ArrowLeft, BarChart3 } from 'lucide-react'
import { cn } from '../../lib/utils'
import { ThemeToggle } from './ThemeToggle'

type EvaluationTab = 'scenarios' | 'datasets' | 'dimensions' | 'judges' | 'categories' | 'measures'

interface EvaluationHeaderProps {
  activeTab: EvaluationTab
  onTabChange: (tab: EvaluationTab) => void
  onBack: () => void
  projectName?: string | null
  isLoading?: boolean
}

const tabs: { key: EvaluationTab; label: string }[] = [
  { key: 'scenarios', label: 'Scenarios' },
  { key: 'datasets', label: 'Datasets' },
  { key: 'dimensions', label: 'Dimensions' },
  { key: 'judges', label: 'Judges' },
  { key: 'categories', label: 'Categories' },
  { key: 'measures', label: 'Measures' },
]

export function EvaluationHeader({ activeTab, onTabChange, onBack, projectName, isLoading }: EvaluationHeaderProps) {
  return (
    <div className="border-b border-border bg-background">
      <div className="flex items-center justify-between px-4 h-14">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-600 text-white">
            <BarChart3 className="w-4 h-4" />
          </div>
          <div className="flex flex-col">
            <span className="text-base font-semibold">Evaluation</span>
            <span className="text-xs text-muted-foreground">
              {isLoading ? 'Loading project…' : projectName ? `Project: ${projectName}` : 'Project unavailable'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <ThemeToggle />
          <div className="flex items-center gap-1 p-1 bg-muted rounded-md">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => onTabChange(tab.key)}
                className={cn(
                  'px-3 py-1 text-xs font-medium rounded-md transition-colors',
                  activeTab === tab.key ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
