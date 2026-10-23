import { NativeSelect, NativeSelectOption } from '@omniagents/ui/native-select'
import { ArrowLeft, BarChart3 } from 'lucide-react'

import { cn } from '../../lib/utils'
import type { AgentSummary } from '../../types'
import { EVALUATION_TABS, type EvaluationTab } from './evaluationTabs'
import { ThemeToggle } from './ThemeToggle'


interface EvaluationHeaderProps {
  activeTab: EvaluationTab
  onTabChange: (tab: EvaluationTab) => void
  onBack: () => void
  projectName?: string | null
  isLoading?: boolean
  agents: AgentSummary[]
  agent: string | null
  onAgentChange: (agent: string | null) => void
}

export function EvaluationHeader({
  activeTab,
  onTabChange,
  onBack,
  projectName,
  isLoading,
  agents,
  agent,
  onAgentChange,
}: EvaluationHeaderProps) {
  return (
    <div className="border-b border-border bg-background">
      <div className="flex h-14 items-center justify-between gap-3 px-4">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-600 text-white">
            <BarChart3 className="h-4 w-4" />
          </div>
          <div className="flex flex-col">
            <span className="text-base font-semibold">Evaluation</span>
            <span className="text-xs text-muted-foreground">
              {isLoading ? 'Loading project…' : projectName ? `Project: ${projectName}` : 'Project unavailable'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Agent
            <NativeSelect size="sm" value={agent ?? ''} onChange={(e) => onAgentChange(e.target.value || null)}>
              <NativeSelectOption value="">Entrypoint</NativeSelectOption>
              {agents.map((a) => (
                <NativeSelectOption key={a.key} value={a.key}>
                  {a.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </label>
          <ThemeToggle />
          <div className="flex items-center gap-1 rounded-md bg-muted p-1">
            {EVALUATION_TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => onTabChange(tab.key)}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-medium transition-colors',
                  activeTab === tab.key ? 'bg-background shadow-xs' : 'text-muted-foreground hover:text-foreground',
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
