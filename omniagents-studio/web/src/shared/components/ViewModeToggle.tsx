import { cn } from '../../lib/utils'

interface ViewModeToggleProps {
  mode: 'debug' | 'review'
  onModeChange: (mode: 'debug' | 'review') => void
}

export function ViewModeToggle({ mode, onModeChange }: ViewModeToggleProps) {
  return (
    <div className="border-b border-border px-4 py-2 bg-muted/30 flex justify-end">
      <div className="flex items-center gap-1 p-1 bg-background rounded-lg w-fit">
        <button
          onClick={() => onModeChange('review')}
          className={cn(
            "px-3 py-1.5 text-sm font-medium rounded-md transition-colors",
            mode === 'review'
              ? 'bg-blue-500 text-white dark:bg-blue-600' 
              : 'text-muted-foreground hover:text-foreground hover:bg-muted'
          )}
        >
          Review Mode
        </button>
        <button
          onClick={() => onModeChange('debug')}
          className={cn(
            "px-3 py-1.5 text-sm font-medium rounded-md transition-colors",
            mode === 'debug'
              ? 'bg-blue-500 text-white dark:bg-blue-600' 
              : 'text-muted-foreground hover:text-foreground hover:bg-muted'
          )}
        >
          Debug Mode
        </button>
      </div>
    </div>
  )
}