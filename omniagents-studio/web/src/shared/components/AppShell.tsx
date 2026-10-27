import type { ReactNode } from 'react'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@omniagents/ui/resizable'
import { ToastContainer } from './ToastContainer'

interface AppShellProps {
  header?: ReactNode
  sidebar?: ReactNode
  children: ReactNode
  hasBottomBar?: boolean
}

export function AppShell({ header, sidebar, children, hasBottomBar = false }: AppShellProps) {
  if (!sidebar) {
    return (
      <div className="h-screen w-full flex flex-col bg-background">
        <ToastContainer />
        {header}
        <main className={`flex-1 h-full flex flex-col overflow-hidden ${hasBottomBar ? 'pb-10' : ''}`}>
          {children}
        </main>
      </div>
    )
  }

  return (
    <div className="h-screen w-full flex flex-col bg-background">
      <ToastContainer />
      {header}
      <ResizablePanelGroup orientation="horizontal" className="flex-1">
        <ResizablePanel defaultSize="20%" minSize="15%" maxSize="30%">
          <aside className={`h-full border-r border-border flex flex-col ${hasBottomBar ? 'pb-10' : ''}`}>
            {sidebar}
          </aside>
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel defaultSize="80%">
          <main className={`h-full flex flex-col overflow-hidden ${hasBottomBar ? 'pb-10' : ''}`}>
            {children}
          </main>
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  )
}
