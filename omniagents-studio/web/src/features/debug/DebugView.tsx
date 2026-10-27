import { useState } from 'react'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@omniagents/ui/resizable'
import { ArrowLeft } from 'lucide-react'
import type { Span } from '../../types'
import { Timeline } from './components/Timeline'
import { SpanInspector } from './components/SpanInspector'

interface DebugViewProps {
  spans: Span[]
  onBack?: () => void
}

export function DebugView({ spans, onBack }: DebugViewProps) {
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null)
  
  return (
    <div className="h-full flex flex-col">
      {onBack && (
        <div className="border-b border-border px-4 py-2">
          <button
            onClick={onBack}
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Trace List
          </button>
        </div>
      )}
      <ResizablePanelGroup orientation="horizontal" className="flex-1">
        <ResizablePanel defaultSize="70%" minSize="50%">
          <Timeline 
            spans={spans} 
            onSpanSelect={setSelectedSpan}
            selectedSpan={selectedSpan}
          />
        </ResizablePanel>
        
        <ResizableHandle />
        
        <ResizablePanel defaultSize="30%" minSize="20%" maxSize="50%">
          <SpanInspector span={selectedSpan} />
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  )
}