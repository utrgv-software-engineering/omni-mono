import { Monitor, Moon, Sun } from 'lucide-react'
import { useTheme } from '../theme/useTheme'

const preferenceOrder: Array<'light' | 'dark' | 'system'> = ['light', 'dark', 'system']

const preferenceLabels: Record<'light' | 'dark' | 'system', string> = {
  light: 'Switch to dark mode',
  dark: 'Follow system theme',
  system: 'Switch to light mode',
}

export function ThemeToggle() {
  const { theme, preference, setPreference } = useTheme()

  const handleClick = () => {
    const index = preferenceOrder.indexOf(preference)
    const next = preferenceOrder[(index + 1) % preferenceOrder.length]
    setPreference(next)
  }

  const icon = preference === 'system'
    ? <Monitor className="h-4 w-4" />
    : theme === 'dark'
      ? <Moon className="h-4 w-4" />
      : <Sun className="h-4 w-4" />

  const label = preferenceLabels[preference]

  return (
    <button
      type="button"
      onClick={handleClick}
      className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
      aria-label={label}
      title={label}
    >
      {icon}
    </button>
  )
}
