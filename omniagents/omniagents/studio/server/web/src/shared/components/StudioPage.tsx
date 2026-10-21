import type { ReactNode } from 'react'
import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppShell } from './AppShell'
import { StudioSidebar } from './StudioSidebar'
import { ROUTES } from '../../app/routes'

export type StudioSection = 'home' | 'logs' | 'playground' | 'agents' | 'evaluation'

interface StudioPageProps {
  section: StudioSection
  header: ReactNode
  children: ReactNode
  sidebarContent?: ReactNode
  showNavigation?: boolean
  disableSidebar?: boolean
  hasBottomBar?: boolean
}

export function StudioPage({
  section,
  header,
  children,
  sidebarContent,
  showNavigation = true,
  disableSidebar = false,
  hasBottomBar = false,
}: StudioPageProps) {
  const navigate = useNavigate()

  const handleSelect = useCallback(
    (nextSection: StudioSection) => {
      if (nextSection === 'home') {
        navigate(ROUTES.home)
      } else if (nextSection === 'logs') {
        navigate(ROUTES.logs)
      } else if (nextSection === 'playground') {
        navigate(ROUTES.playground)
      } else if (nextSection === 'agents') {
        navigate(ROUTES.agents)
      } else if (nextSection === 'evaluation') {
        navigate(ROUTES.evaluation)
      }
    },
    [navigate],
  )

  const sidebar = disableSidebar
    ? null
    : (
        <StudioSidebar
          activeSection={section}
          onSelect={handleSelect}
          showNavigation={showNavigation}
        >
          {sidebarContent}
        </StudioSidebar>
      )

  return (
    <AppShell header={header} sidebar={sidebar} hasBottomBar={hasBottomBar}>
      {children}
    </AppShell>
  )
}
