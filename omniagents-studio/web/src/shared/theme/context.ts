import { createContext } from 'react'

export type Theme = 'light' | 'dark'
export type ThemePreference = Theme | 'system'

export interface ThemeContextValue {
  theme: Theme
  preference: ThemePreference
  setPreference: (preference: ThemePreference) => void
  toggleTheme: () => void
}

export const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)
