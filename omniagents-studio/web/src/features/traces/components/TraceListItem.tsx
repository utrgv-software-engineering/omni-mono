import React from 'react'
import { Activity, Clock } from 'lucide-react'
import type { Trace } from '../../../types'
import { cn, formatTimestamp } from '../../../lib/utils'

interface TraceListItemProps {
  trace: Trace
  isSelected: boolean
  showProject: boolean
  onClick: (trace: Trace) => void
}

export const TraceListItem = React.memo(({ 
  trace, 
  isSelected, 
  showProject,
  onClick 
}: TraceListItemProps) => {
  return (
    <div
      className={cn(
        "px-3 py-2 border-b border-border cursor-pointer transition-colors",
        "hover:bg-muted/50",
        isSelected && "bg-primary/10 border-l-2 border-l-primary"
      )}
      onClick={() => onClick(trace)}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Activity className="w-3 h-3 text-muted-foreground shrink-0" />
            <span className="text-sm font-medium truncate">
              {trace.workflow_name || 'Unnamed Trace'}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <Clock className="w-3 h-3 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">
              {formatTimestamp(trace.created_at)}
            </span>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1">
            <span className="font-mono">{trace.trace_id.substring(0, 8)}...</span>
            {showProject && (
              <span className="px-1.5 py-0.5 bg-muted rounded-sm text-xs">
                {trace.project}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
})

TraceListItem.displayName = 'TraceListItem'