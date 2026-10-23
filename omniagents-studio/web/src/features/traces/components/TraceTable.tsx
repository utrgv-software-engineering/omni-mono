import { Activity, AlertCircle, Clock, MessageSquare, ArrowRight } from 'lucide-react'
import type { Trace } from '../../../types'
import { formatDistanceToNow } from 'date-fns'
import { formatDuration } from '../../../lib/utils'

interface TraceTableProps {
  traces: Trace[]
  onTraceSelect: (trace: Trace) => void
  selectedTraceId: string | null
}

// Helper to extract input/output from trace metadata
function extractInputOutput(trace: Trace): { input: string; output: string } {
  const metadata = trace.metadata || {}
  
  // Try to get input - could be in various places
  let input = metadata.input || 
              metadata.user_input || 
              metadata.prompt || 
              metadata.message || 
              metadata.query || 
              ''
  
  // Try to get output - could be in various places  
  let output = metadata.output ||
               metadata.response ||
               metadata.result ||
               metadata.completion ||
               metadata.answer ||
               ''
  
  // Convert to string if needed
  if (typeof input !== 'string') input = input === null ? '' : JSON.stringify(input)
  if (typeof output !== 'string') output = output === null ? '' : JSON.stringify(output)
  
  // Truncate if too long
  const maxLength = 100
  if (input.length > maxLength) input = input.substring(0, maxLength) + '...'
  if (output.length > maxLength) output = output.substring(0, maxLength) + '...'
  
  return { input, output }
}

export function TraceTable({ traces, onTraceSelect, selectedTraceId }: TraceTableProps) {
  if (traces.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center">
          <Activity className="w-12 h-12 mx-auto mb-2 opacity-20" />
          <p className="text-sm">Select a conversation to view traces</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Traces</h2>
        <p className="text-sm text-muted-foreground mt-1">
          {traces.length} trace{traces.length !== 1 ? 's' : ''} in this conversation
        </p>
      </div>
      
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-background border-b border-border">
            <tr>
              <th className="text-left px-4 py-3 text-sm font-medium text-muted-foreground w-[35%]">Input</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-muted-foreground w-[35%]">Output</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-muted-foreground">Duration</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-muted-foreground">Time</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-muted-foreground">Status</th>
            </tr>
          </thead>
          <tbody>
            {traces.map((trace) => {
              const isSelected = selectedTraceId === trace.trace_id
              const timeAgo = formatDistanceToNow(new Date(trace.created_at), { addSuffix: true })
              const duration = trace.duration || 0
              const hasErrors = trace.has_errors || false
              const { input, output } = extractInputOutput(trace)
              
              return (
                <tr
                  key={trace.trace_id}
                  className={`border-b border-border/50 cursor-pointer transition-colors ${
                    isSelected 
                      ? 'bg-primary/10' 
                      : 'hover:bg-muted/30'
                  }`}
                  onClick={() => onTraceSelect(trace)}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-start gap-2">
                      <MessageSquare className="w-4 h-4 text-muted-foreground shrink-0 mt-0.5" />
                      <span className="text-sm text-foreground/90 wrap-break-word line-clamp-2">
                        {input || <span className="text-muted-foreground italic">No input</span>}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-start gap-2">
                      <ArrowRight className="w-4 h-4 text-muted-foreground shrink-0 mt-0.5" />
                      <span className="text-sm text-foreground/90 wrap-break-word line-clamp-2">
                        {output || <span className="text-muted-foreground italic">No output</span>}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1">
                      <Clock className="w-3 h-3 text-muted-foreground" />
                      <span className="text-sm">
                        {duration > 0 ? formatDuration(duration) : '-'}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-sm text-muted-foreground">
                      {timeAgo}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {hasErrors ? (
                      <div className="flex items-center gap-1">
                        <AlertCircle className="w-4 h-4 text-destructive" />
                        <span className="text-sm text-destructive">Error</span>
                      </div>
                    ) : (
                      <span className="text-sm text-green-600">Success</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
