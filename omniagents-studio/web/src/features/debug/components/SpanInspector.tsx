import { useState } from 'react'
import type { Span } from '../../../types'
import { formatTimestamp, formatDuration, calculateDuration } from '../../../lib/utils'
import { getSpanColor } from '../../../lib/colors'
import { Clock, AlertCircle, Copy, FileJson, Info, Bug } from 'lucide-react'
import { cn } from '../../../lib/utils'

interface SpanInspectorProps {
  span: Span | null
}

type TabType = 'overview' | 'data' | 'error'

export function SpanInspector({ span }: SpanInspectorProps) {
  const [activeTab, setActiveTab] = useState<TabType>('overview')
  
  if (!span) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center">
          <Info className="w-12 h-12 mx-auto mb-2 opacity-20" />
          <p className="text-sm">Select a span to inspect</p>
        </div>
      </div>
    )
  }
  
  const duration = calculateDuration(span.started_at, span.ended_at)
  
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
  }
  
  const renderOverview = () => (
    <div className="p-4 space-y-4">
      {/* Span Type Badge */}
      <div className="flex items-center gap-2">
        <div
          className="w-3 h-3 rounded-full"
          style={{ backgroundColor: getSpanColor(span.span_type) }}
        />
        <span className="text-sm font-medium">{span.span_type}</span>
      </div>
      
      {/* Span Name */}
      {span.span_name && (
        <div>
          <div className="text-xs text-muted-foreground mb-1">Name</div>
          <div className="text-sm font-mono bg-muted/50 p-2 rounded-sm">
            {span.span_name}
          </div>
        </div>
      )}
      
      {/* Span ID */}
      <div>
        <div className="text-xs text-muted-foreground mb-1">Span ID</div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono bg-muted/50 px-2 py-1 rounded-sm flex-1 truncate">
            {span.span_id}
          </span>
          <button
            onClick={() => copyToClipboard(span.span_id)}
            className="p-1 hover:bg-muted rounded-sm"
          >
            <Copy className="w-3 h-3" />
          </button>
        </div>
      </div>
      
      {/* Timing */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm">
          <Clock className="w-4 h-4 text-muted-foreground" />
          <span className="text-muted-foreground">Started:</span>
          <span className="font-mono">
            {span.started_at ? formatTimestamp(span.started_at) : 'N/A'}
          </span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Clock className="w-4 h-4 text-muted-foreground" />
          <span className="text-muted-foreground">Ended:</span>
          <span className="font-mono">
            {span.ended_at ? formatTimestamp(span.ended_at) : 'In Progress'}
          </span>
        </div>
        {duration !== undefined && (
          <div className="flex items-center gap-2 text-sm">
            <Clock className="w-4 h-4 text-muted-foreground" />
            <span className="text-muted-foreground">Duration:</span>
            <span className="font-mono font-medium">{formatDuration(duration)}</span>
          </div>
        )}
      </div>
      
      {/* Additional Info based on span type */}
      {span.span_type === 'generation' && span.data?.span_data?.usage && (
        <div>
          <div className="text-xs text-muted-foreground mb-1">Token Usage</div>
          <div className="bg-muted/50 p-2 rounded-sm space-y-1 text-sm">
            <div>Input: {span.data.span_data.usage.input_tokens || 0}</div>
            <div>Output: {span.data.span_data.usage.output_tokens || 0}</div>
            <div>Total: {span.data.span_data.usage.total_tokens || 0}</div>
          </div>
        </div>
      )}
      
      {span.span_type === 'function' && span.data?.span_data && (
        <div>
          <div className="text-xs text-muted-foreground mb-1">Function</div>
          <div className="bg-muted/50 p-2 rounded-sm text-sm font-mono">
            {span.data.span_data.name || 'Unknown'}
          </div>
        </div>
      )}
      
      {span.span_type === 'agent' && span.data?.span_data && (
        <div className="space-y-3">
          {span.data.span_data.tools && span.data.span_data.tools.length > 0 && (
            <div>
              <div className="text-xs text-muted-foreground mb-1">Available Tools</div>
              <div className="bg-muted/50 p-2 rounded-sm">
                <div className="flex flex-wrap gap-1">
                  {span.data.span_data.tools.map((tool: string, idx: number) => (
                    <span key={idx} className="px-2 py-1 bg-background rounded-sm text-xs font-mono">
                      {tool}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
          
          {span.data.span_data.handoffs && span.data.span_data.handoffs.length > 0 && (
            <div>
              <div className="text-xs text-muted-foreground mb-1">Handoffs</div>
              <div className="bg-muted/50 p-2 rounded-sm">
                <div className="flex flex-wrap gap-1">
                  {span.data.span_data.handoffs.map((handoff: string, idx: number) => (
                    <span key={idx} className="px-2 py-1 bg-background rounded-sm text-xs font-mono">
                      {handoff}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
          
          {span.data.span_data.output_type && (
            <div>
              <div className="text-xs text-muted-foreground mb-1">Output Type</div>
              <div className="bg-muted/50 p-2 rounded-sm text-sm font-mono">
                {span.data.span_data.output_type}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
  
  const renderData = () => (
    <div className="p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-muted-foreground">Raw Data</span>
        <button
          onClick={() => copyToClipboard(JSON.stringify(span.data, null, 2))}
          className="flex items-center gap-1 px-2 py-1 text-xs hover:bg-muted rounded-sm"
        >
          <Copy className="w-3 h-3" />
          Copy
        </button>
      </div>
      <pre className="text-xs font-mono bg-muted/50 p-3 rounded-sm overflow-auto">
        {JSON.stringify(span.data, null, 2)}
      </pre>
    </div>
  )
  
  const renderError = () => (
    <div className="p-4">
      {span.error ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-destructive">
            <AlertCircle className="w-4 h-4" />
            <span className="font-medium">Error Details</span>
          </div>
          <pre className="text-xs font-mono bg-destructive/10 text-destructive p-3 rounded-sm overflow-auto">
            {JSON.stringify(span.error, null, 2)}
          </pre>
        </div>
      ) : (
        <div className="flex items-center justify-center h-32 text-muted-foreground">
          <div className="text-center">
            <Bug className="w-8 h-8 mx-auto mb-2 opacity-20" />
            <p className="text-sm">No errors</p>
          </div>
        </div>
      )}
    </div>
  )
  
  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="border-b border-border p-3">
        <h2 className="text-lg font-semibold">Inspector</h2>
      </div>
      
      {/* Tabs */}
      <div className="border-b border-border flex">
        <button
          className={cn(
            "px-4 py-2 text-sm font-medium transition-colors",
            activeTab === 'overview' 
              ? "border-b-2 border-primary text-primary" 
              : "text-muted-foreground hover:text-foreground"
          )}
          onClick={() => setActiveTab('overview')}
        >
          <div className="flex items-center gap-1">
            <Info className="w-3 h-3" />
            Overview
          </div>
        </button>
        <button
          className={cn(
            "px-4 py-2 text-sm font-medium transition-colors",
            activeTab === 'data' 
              ? "border-b-2 border-primary text-primary" 
              : "text-muted-foreground hover:text-foreground"
          )}
          onClick={() => setActiveTab('data')}
        >
          <div className="flex items-center gap-1">
            <FileJson className="w-3 h-3" />
            Data
          </div>
        </button>
        <button
          className={cn(
            "px-4 py-2 text-sm font-medium transition-colors relative",
            activeTab === 'error' 
              ? "border-b-2 border-primary text-primary" 
              : "text-muted-foreground hover:text-foreground"
          )}
          onClick={() => setActiveTab('error')}
        >
          <div className="flex items-center gap-1">
            <Bug className="w-3 h-3" />
            Errors
            {span.error && (
              <span className="ml-1 w-2 h-2 bg-destructive rounded-full" />
            )}
          </div>
        </button>
      </div>
      
      {/* Tab Content */}
      <div className="flex-1 overflow-auto">
        {activeTab === 'overview' && renderOverview()}
        {activeTab === 'data' && renderData()}
        {activeTab === 'error' && renderError()}
      </div>
    </div>
  )
}