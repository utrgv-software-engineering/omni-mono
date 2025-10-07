import type { ReactNode } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'

interface AppShellProps {
  header?: ReactNode
  sidebar: ReactNode
  children: ReactNode
  hasBottomBar?: boolean
}

export function AppShell({ header, sidebar, children, hasBottomBar = false }: AppShellProps) {
  return (
    <div className="h-screen w-full flex flex-col bg-background">
      {/* Top Header */}
      {header}
      
      {/* Main Content Area */}
      <PanelGroup direction="horizontal" className="flex-1">
        {/* Fixed Sidebar */}
        <Panel defaultSize={20} minSize={15} maxSize={30}>
          <aside className={`h-full border-r border-border flex flex-col ${hasBottomBar ? 'pb-10' : ''}`}>
            {sidebar}
          </aside>
        </Panel>
        
        <PanelResizeHandle className="w-1 bg-border hover:bg-primary/20 transition-colors" />
        
        {/* Main Content Area */}
        <Panel defaultSize={80}>
          <main className={`h-full flex flex-col overflow-hidden ${hasBottomBar ? 'pb-10' : ''}`}>
            {children}
          </main>
        </Panel>
      </PanelGroup>
    </div>
  )
}