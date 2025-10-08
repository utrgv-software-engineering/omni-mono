import type { ReactNode } from 'react'
import { Home, LayoutPanelLeft, Sparkles, Bot, BarChart3 } from 'lucide-react'
import { cn } from '../../lib/utils'

type StudioSection = 'home' | 'logs' | 'playground' | 'agents' | 'evaluation'

interface StudioSidebarProps {
  activeSection: StudioSection
  onSelect: (section: StudioSection) => void
  children?: ReactNode
  showNavigation?: boolean
}

export function StudioSidebar({ activeSection, onSelect, children, showNavigation = true }: StudioSidebarProps) {
  return (
    <div className="flex h-full flex-col">
      {showNavigation && (
        <div className="px-3 py-4 space-y-2">
          <SidebarButton
            icon={Home}
            label="Home"
            isActive={activeSection === 'home'}
            onClick={() => onSelect('home')}
          />
          <SidebarButton
            icon={Sparkles}
            label="Playground"
            isActive={activeSection === 'playground'}
            onClick={() => onSelect('playground')}
          />
          <SidebarButton
            icon={Bot}
            label="Agents"
            isActive={activeSection === 'agents'}
            onClick={() => onSelect('agents')}
          />
          <SidebarButton
            icon={LayoutPanelLeft}
            label="Logs"
            isActive={activeSection === 'logs'}
            onClick={() => onSelect('logs')}
          />
          <SidebarButton
            icon={BarChart3}
            label="Evaluation"
            isActive={activeSection === 'evaluation'}
            onClick={() => onSelect('evaluation')}
          />
        </div>
      )}
      <div className="flex-1 overflow-hidden">
        {showNavigation ? (activeSection === 'logs' ? children : null) : children}
      </div>
    </div>
  )
}

interface SidebarButtonProps {
  icon: React.ComponentType<{ className?: string }>
  label: string
  isActive: boolean
  onClick: () => void
  variant?: 'default' | 'ghost'
}

function SidebarButton({ icon: Icon, label, isActive, onClick, variant = 'default' }: SidebarButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
        variant === 'ghost' ? 'text-muted-foreground hover:bg-muted/60' : 'text-muted-foreground hover:bg-muted',
        isActive && 'bg-muted text-foreground'
      )}
    >
      <Icon className="h-4 w-4" />
      <span>{label}</span>
    </button>
  )
}
