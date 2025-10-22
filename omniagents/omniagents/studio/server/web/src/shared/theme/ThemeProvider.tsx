import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ThemeContext, type Theme, type ThemeContextValue, type ThemePreference } from './context'
const STORAGE_KEY = 'omniagents-studio-theme'

const getSystemTheme = (): Theme => {
  if (typeof window === 'undefined') {
    return 'light'
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

const readStoredPreference = (): ThemePreference => {
  if (typeof window === 'undefined') {
    return 'system'
  }
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') {
      return stored
    }
    return 'system'
  } catch (error) {
    console.error('Failed to read stored theme preference:', error)
    return 'system'
  }
}

const writeStoredPreference = (preference: ThemePreference) => {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, preference)
  } catch (error) {
    console.error('Failed to store theme preference:', error)
  }
}

const applyTheme = (theme: Theme) => {
  if (typeof document === 'undefined') {
    return
  }
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.style.setProperty('color-scheme', theme)
}

const resolveTheme = (preference: ThemePreference): Theme => {
  if (preference === 'system') {
    return getSystemTheme()
  }
  return preference
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<ThemePreference>(() => readStoredPreference())
  const [theme, setTheme] = useState<Theme>(() => resolveTheme(readStoredPreference()))

  useLayoutEffect(() => {
    applyTheme(theme)
  }, [theme])

  useEffect(() => {
    writeStoredPreference(preference)
  }, [preference])

  useEffect(() => {
    if (preference !== 'system') {
      return
    }
    if (typeof window === 'undefined') {
      return
    }
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const listener = () => {
      setTheme(media.matches ? 'dark' : 'light')
    }
    listener()
    media.addEventListener('change', listener)
    return () => media.removeEventListener('change', listener)
  }, [preference])

  useEffect(() => {
    setTheme(resolveTheme(preference))
  }, [preference])

  const toggleTheme = useCallback(() => {
    setPreference((current) => {
      if (current === 'system') {
        return theme === 'dark' ? 'light' : 'dark'
      }
      return current === 'dark' ? 'light' : 'dark'
    })
  }, [theme])

  const value = useMemo<ThemeContextValue>(() => ({
    theme,
    preference,
    setPreference,
    toggleTheme,
  }), [theme, preference, toggleTheme])

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  )
}
