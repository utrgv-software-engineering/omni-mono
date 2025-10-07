import React, { useCallback, useMemo } from 'react'
import { FixedSizeList as List } from 'react-window'
import { Activity } from 'lucide-react'
import type { Trace } from '../../../types'
import { useTraceStore } from '../store/traceStore'
import { TraceListItem } from './TraceListItem'
import { TraceFilters } from './TraceFilters'

interface TraceListProps {
  traces: Trace[]
  onTraceSelect: (trace: Trace) => void
}

export function TraceList({ traces, onTraceSelect }: TraceListProps) {
  const { selectedTrace, searchQuery, selectedProject } = useTraceStore()
  
  // Filter traces based on search
  const filteredTraces = useMemo(() => {
    if (!searchQuery) return traces
    
    const query = searchQuery.toLowerCase()
    return traces.filter(trace => 
      trace.workflow_name?.toLowerCase().includes(query) ||
      trace.trace_id.toLowerCase().includes(query)
    )
  }, [traces, searchQuery])
  
  const Row = useCallback(({ index, style }: { index: number; style: React.CSSProperties }) => {
    const trace = filteredTraces[index]
    const isSelected = selectedTrace?.trace_id === trace.trace_id
    
    return (
      <div style={style}>
        <TraceListItem
          trace={trace}
          isSelected={isSelected}
          showProject={!selectedProject}
          onClick={onTraceSelect}
        />
      </div>
    )
  }, [filteredTraces, selectedTrace, selectedProject, onTraceSelect])
  
  return (
    <div className="h-full flex flex-col">
      <TraceFilters />
      
      {/* Trace List */}
      <div className="flex-1">
        {filteredTraces.length > 0 ? (
          <List
            height={window.innerHeight - 200}
            itemCount={filteredTraces.length}
            itemSize={80}
            width="100%"
          >
            {Row}
          </List>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            <div className="text-center">
              <Activity className="w-12 h-12 mx-auto mb-2 opacity-20" />
              <p className="text-sm">No traces found</p>
            </div>
          </div>
        )}
      </div>
      
      {/* Footer */}
      <div className="border-t border-border p-2 text-xs text-muted-foreground text-center">
        {filteredTraces.length} trace{filteredTraces.length !== 1 ? 's' : ''}
      </div>
    </div>
  )
}