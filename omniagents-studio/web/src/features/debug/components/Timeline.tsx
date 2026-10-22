import React, { useMemo, useState } from 'react'
import type { Span } from '../../../types'
import { getSpanColor } from '../../../lib/colors'
import { formatDuration } from '../../../lib/utils'
import { cn } from '../../../lib/utils'
import { 
  ChevronRight, 
  ChevronDown, 
  AlertCircle, 
  Activity,
  Cpu,
  MessageSquare,
  Shuffle,
  Shield,
  Send,
  Hash,
  Mic,
  Volume2,
  Wrench,
  Copy,
  Check
} from 'lucide-react'

interface TimelineProps {
  spans: Span[]
  onSpanSelect: (span: Span) => void
  selectedSpan: Span | null
}

// Icon mapping for span types
const spanIcons: Record<string, React.ReactNode> = {
  agent: <Activity className="w-3 h-3" />,
  function: <Hash className="w-3 h-3" />,
  generation: <MessageSquare className="w-3 h-3" />,
  handoff: <Shuffle className="w-3 h-3" />,
  guardrail: <Shield className="w-3 h-3" />,
  response: <Send className="w-3 h-3" />,
  custom: <Cpu className="w-3 h-3" />,
  transcription: <Mic className="w-3 h-3" />,
  speech: <Volume2 className="w-3 h-3" />,
  'speech-group': <Volume2 className="w-3 h-3" />,
  mcp_tools: <Wrench className="w-3 h-3" />
}

export function Timeline({ spans, onSpanSelect, selectedSpan }: TimelineProps) {
  const [collapsedSpans, setCollapsedSpans] = useState<Set<string>>(new Set())
  const [copied, setCopied] = useState(false)
  
  // Build span hierarchy and calculate timeline bounds
  const { rootSpans, minTime } = useMemo(() => {
    const map: Record<string, Span> = {}
    const roots: Span[] = []
    let min = Infinity
    
    // First pass: create map and find time bounds
    spans.forEach(span => {
      map[span.span_id] = { ...span, children: [] }
      
      if (span.started_at) {
        const startTime = new Date(span.started_at).getTime()
        min = Math.min(min, startTime)
      }
    })
    
    // Second pass: build hierarchy
    spans.forEach(span => {
      if (span.parent_id && map[span.parent_id]) {
        map[span.parent_id].children = map[span.parent_id].children || []
        map[span.parent_id].children!.push(map[span.span_id])
      } else {
        roots.push(map[span.span_id])
      }
    })
    
    // Sort by sequence number
    roots.sort((a, b) => a.sequence_number - b.sequence_number)
    
    return {
      rootSpans: roots,
      spanMap: map,
      minTime: min
    }
  }, [spans])
  
  const toggleCollapse = (spanId: string) => {
    setCollapsedSpans(prev => {
      const next = new Set(prev)
      if (next.has(spanId)) {
        next.delete(spanId)
      } else {
        next.add(spanId)
      }
      return next
    })
  }
  
  const copyTraceData = async () => {
    try {
      // Create a comprehensive data object with all trace information
      const traceData = {
        trace_id: spans.length > 0 ? spans[0].trace_id : null,
        total_spans: spans.length,
        spans: spans.map(span => ({
          ...span,
          // Ensure we include all nested data
          data: span.data,
          children: span.children ? span.children.map(c => c.span_id) : []
        }))
      }
      
      await navigator.clipboard.writeText(JSON.stringify(traceData, null, 2))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy trace data:', err)
    }
  }
  
  const renderSpan = (span: Span, depth: number = 0): React.ReactNode => {
    const hasChildren = span.children && span.children.length > 0
    const isCollapsed = collapsedSpans.has(span.span_id)
    const isSelected = selectedSpan?.span_id === span.span_id
    
    const startTime = span.started_at ? new Date(span.started_at).getTime() : minTime
    const endTime = span.ended_at ? new Date(span.ended_at).getTime() : startTime
    const duration = endTime - startTime
    
    // Calculate the total trace duration (from first span to last span end)
    const totalTraceDuration = rootSpans.reduce((max, root) => {
      const calcMaxTime = (s: Span): number => {
        let maxTime = s.ended_at ? new Date(s.ended_at).getTime() : 0
        if (s.children) {
          s.children.forEach(child => {
            maxTime = Math.max(maxTime, calcMaxTime(child))
          })
        }
        return maxTime
      }
      return Math.max(max, calcMaxTime(root))
    }, minTime) - minTime
    
    // Calculate position and width as percentages of the total timeline
    const offset = totalTraceDuration > 0 ? ((startTime - minTime) / totalTraceDuration) * 100 : 0
    const width = totalTraceDuration > 0 ? (duration / totalTraceDuration) * 100 : 0
    
    const icon = spanIcons[span.span_type] || <Hash className="w-3 h-3" />
    
    return (
      <div key={span.span_id}>
        <div
          className={cn(
            "group flex items-center h-10 hover:bg-muted/30 cursor-pointer transition-colors border-b border-border/50",
            isSelected && "bg-primary/10"
          )}
          onClick={() => onSpanSelect(span)}
        >
          {/* Left section with name */}
          <div className="flex items-center min-w-[280px] max-w-[350px]" style={{ paddingLeft: `${depth * 24 + 12}px` }}>
            {/* Expand/Collapse */}
            <div className="w-5 mr-2 flex-shrink-0">
              {hasChildren && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    toggleCollapse(span.span_id)
                  }}
                  className="p-0.5 hover:bg-muted rounded"
                >
                  {isCollapsed ? (
                    <ChevronRight className="w-3 h-3" />
                  ) : (
                    <ChevronDown className="w-3 h-3" />
                  )}
                </button>
              )}
            </div>
            
            {/* Icon */}
            <div className="mr-2 text-muted-foreground flex-shrink-0" style={{ color: getSpanColor(span.span_type) }}>
              {icon}
            </div>
            
            {/* Span Name */}
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span className="text-sm truncate">
                {span.span_name || span.span_type}
              </span>
              {span.error && (
                <AlertCircle className="w-3 h-3 text-destructive flex-shrink-0" />
              )}
            </div>
          </div>
          
          {/* Duration column */}
          <div className="text-xs text-muted-foreground w-[80px] text-right pr-4">
            {duration > 0 ? formatDuration(duration) : '0 ms'}
          </div>
          
          {/* Timeline visualization - maintains relative positioning */}
          <div className="flex-1 pr-4">
            <div className="relative h-5 bg-muted/5 rounded-sm">
              <div
                className="absolute h-full rounded-sm transition-all"
                style={{
                  left: `${offset}%`,
                  width: `${Math.max(width, 2)}%`,
                  backgroundColor: span.error ? 'hsl(var(--destructive))' : getSpanColor(span.span_type),
                  opacity: 0.8
                }}
              />
            </div>
          </div>
        </div>
        
        {/* Children */}
        {hasChildren && !isCollapsed && (
          <div>
            {span.children!.sort((a, b) => a.sequence_number - b.sequence_number).map(child => 
              renderSpan(child, depth + 1)
            )}
          </div>
        )}
      </div>
    )
  }
  
  if (spans.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center">
          <Activity className="w-12 h-12 mx-auto mb-2 opacity-20" />
          <p className="text-sm">Select a trace to view timeline</p>
        </div>
      </div>
    )
  }
  
  const totalDuration = minTime !== Infinity ? 
    spans.reduce((max, span) => {
      if (span.started_at && span.ended_at) {
        const start = new Date(span.started_at).getTime()
        const end = new Date(span.ended_at).getTime()
        return Math.max(max, end - start)
      }
      return max
    }, 0) : 0
  
  return (
    <div className="h-full flex flex-col bg-background">
      {/* Header */}
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Timeline</h2>
          <div className="flex items-center gap-3">
            <button
              onClick={copyTraceData}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded hover:bg-muted transition-colors"
              title="Copy trace data to clipboard"
            >
              {copied ? (
                <>
                  <Check className="w-3 h-3 text-green-500" />
                  <span>Copied!</span>
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" />
                  <span>Copy Trace Data</span>
                </>
              )}
            </button>
            <div className="text-xs text-muted-foreground">
              Total: {formatDuration(totalDuration)}
            </div>
          </div>
        </div>
      </div>
      
      {/* Timeline */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        {/* Spans */}
        <div>
          {rootSpans.map(span => renderSpan(span))}
        </div>
      </div>
    </div>
  )
}