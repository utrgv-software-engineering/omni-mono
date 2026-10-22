import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import type { Span } from '../../types'
import { ConversationFlow } from './components/ConversationFlow'
import { AnalysisPanel } from './components/AnalysisPanel'

interface ReviewViewProps {
  spans: Span[]
}

export function ReviewView({ spans }: ReviewViewProps) {
  return (
    <PanelGroup direction="horizontal" className="h-full">
      <Panel defaultSize={70} minSize={50}>
        <ConversationFlow spans={spans} />
      </Panel>
      
      <PanelResizeHandle className="w-1 bg-border hover:bg-primary/20 transition-colors" />
      
      <Panel defaultSize={30} minSize={20} maxSize={50}>
        <AnalysisPanel />
      </Panel>
    </PanelGroup>
  )
}