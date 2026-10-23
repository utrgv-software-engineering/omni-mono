import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, MoreVertical, Trash2, Download, AlertTriangle, Copy, Settings, ScrollText } from 'lucide-react'
import { useTraceStore } from '../../features/traces/store/traceStore'
import { cn } from '../../lib/utils'
import { AnalysisGoalsModal } from './AnalysisGoalsModal'
import { ThemeToggle } from './ThemeToggle'

interface LogsHeaderProps {
  viewMode?: 'debug' | 'review'
  onViewModeChange?: (mode: 'debug' | 'review') => void
  showViewToggle?: boolean
  showBackButton?: boolean
  onBack?: () => void
  projects?: string[]
  selectedProject?: string | null
  onProjectChange?: (project: string | null) => void
}

export function LogsHeader({
  viewMode,
  onViewModeChange,
  showViewToggle = false,
  showBackButton = false,
  onBack,
  projects = [],
  selectedProject = null,
  onProjectChange,
}: LogsHeaderProps) {
  const { setSelectedConversation, setSelectedTrace, conversationAnalysis, failureCategories } = useTraceStore()
  const queryClient = useQueryClient()
  const [showSettingsMenu, setShowSettingsMenu] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showGoalsModal, setShowGoalsModal] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const settingsMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (settingsMenuRef.current && !settingsMenuRef.current.contains(event.target as Node)) {
        setShowSettingsMenu(false)
      }
    }

    if (showSettingsMenu) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [showSettingsMenu])

  const handleDeleteProject = async () => {
    if (!selectedProject) {
      return
    }

    setIsDeleting(true)
    try {
      const response = await fetch(`/api/projects/${selectedProject}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        throw new Error('Failed to delete project')
      }

      setSelectedConversation(null)
      setSelectedTrace(null)
      onProjectChange?.(null)
      await queryClient.invalidateQueries()
    } catch (error) {
      console.error('Error deleting project:', error)
      alert('Failed to delete project. Please try again.')
    } finally {
      setIsDeleting(false)
      setShowDeleteConfirm(false)
    }
  }

  const getAnalysisData = () => {
    return Object.entries(conversationAnalysis).map(([group_id, analysis]) => {
      const categoryNames = (analysis.categories || []).map((categoryId) => {
        const category = failureCategories.find((cat) => cat.id === categoryId)
        return category ? category.name : `Unknown Category (${categoryId})`
      })

      return {
        group_id,
        marked_for_analysis: analysis.markedForAnalysis || false,
        judgment: analysis.judgment || '',
        notes: analysis.notes || '',
        analyzed_at: analysis.analyzedAt || '',
        categories: categoryNames,
      }
    })
  }

  const handleExportAnalysis = () => {
    setShowSettingsMenu(false)

    const analysisData = getAnalysisData()
    const blob = new Blob([JSON.stringify(analysisData, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `analysis_${selectedProject}_${new Date().toISOString().split('T')[0]}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleCopyAnalysis = async () => {
    setShowSettingsMenu(false)

    const analysisData = getAnalysisData()
    const jsonString = JSON.stringify(analysisData, null, 2)

    try {
      await navigator.clipboard.writeText(jsonString)
      console.log('Analysis copied to clipboard')
    } catch (error) {
      console.error('Failed to copy to clipboard:', error)
    }
  }

  return (
    <>
      <div className="border-b border-border bg-background">
        <div className="flex h-14 items-center justify-between px-4">
          <div className="flex items-center gap-3">
            {showBackButton && onBack && (
              <button
                onClick={onBack}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            )}
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                <ScrollText className="h-4 w-4" />
              </div>
              <div className="flex flex-col">
                <span className="text-sm font-semibold">Logs</span>
                <span className="text-xs text-muted-foreground">
                  {selectedProject ? `Project: ${selectedProject}` : 'Review traces and debug runs'}
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={selectedProject || ''}
              onChange={(event) => onProjectChange?.(event.target.value || null)}
              className="hidden sm:block px-2 py-1 text-xs bg-muted/50 border border-border rounded-md outline-hidden focus:bg-muted"
            >
              <option value="">Project</option>
              {projects.map((project) => (
                <option key={project} value={project}>{project}</option>
              ))}
            </select>
            {showViewToggle && onViewModeChange && viewMode && (
              <div className="flex items-center gap-1 p-0.5 bg-muted rounded-md">
                <button
                  onClick={() => onViewModeChange('review')}
                  className={cn(
                    'px-3 py-1 text-xs font-medium rounded-sm transition-colors',
                    viewMode === 'review' ? 'bg-background shadow-xs' : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  Review
                </button>
                <button
                  onClick={() => onViewModeChange('debug')}
                  className={cn(
                    'px-3 py-1 text-xs font-medium rounded-sm transition-colors',
                    viewMode === 'debug' ? 'bg-background shadow-xs' : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  Debug
                </button>
              </div>
            )}
            <ThemeToggle />
            {selectedProject && (
              <div className="relative" ref={settingsMenuRef}>
                <button
                  onClick={() => setShowSettingsMenu(!showSettingsMenu)}
                  className="p-2 hover:bg-muted rounded-md transition-colors"
                  title="Settings"
                >
                  <MoreVertical className="w-4 h-4" />
                </button>

                {showSettingsMenu && (
                  <div className="absolute right-0 mt-1 w-48 bg-popover border border-border rounded-md shadow-lg z-50">
                    <button
                      onClick={handleCopyAnalysis}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors flex items-center gap-2"
                    >
                      <Copy className="w-4 h-4" />
                      Copy Analysis
                    </button>
                    <button
                      onClick={handleExportAnalysis}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors flex items-center gap-2"
                    >
                      <Download className="w-4 h-4" />
                      Export Analysis
                    </button>
                    <button
                      onClick={() => {
                        setShowSettingsMenu(false)
                        setShowGoalsModal(true)
                      }}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors flex items-center gap-2"
                    >
                      <Settings className="w-4 h-4" />
                      Configure Analysis Goals
                    </button>
                    <div className="border-t border-border my-1" />
                    <button
                      onClick={() => {
                        setShowSettingsMenu(false)
                        setShowDeleteConfirm(true)
                      }}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-destructive/10 text-destructive transition-colors flex items-center gap-2"
                    >
                      <Trash2 className="w-4 h-4" />
                      Delete Project
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-background border border-border rounded-lg p-6 max-w-md w-full mx-4">
            <div className="flex items-start gap-3 mb-4">
              <div className="p-2 bg-destructive/10 rounded-full">
                <AlertTriangle className="w-5 h-5 text-destructive" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-semibold mb-2">Delete Project</h3>
                <p className="text-sm text-muted-foreground">
                  Are you sure you want to delete the project <strong className="text-foreground">{selectedProject}</strong>?
                </p>
                <p className="text-sm text-destructive mt-2">
                  This action cannot be undone. All traces and data for this project will be permanently deleted.
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                disabled={isDeleting}
                className="px-4 py-2 text-sm border border-border rounded-md hover:bg-muted/50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteProject}
                disabled={isDeleting}
                className="px-4 py-2 text-sm bg-destructive text-destructive-foreground rounded-md hover:bg-destructive/90 transition-colors disabled:opacity-50"
              >
                {isDeleting ? 'Deleting...' : 'Delete Project'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showGoalsModal && (
        <AnalysisGoalsModal
          isOpen={showGoalsModal}
          onClose={() => setShowGoalsModal(false)}
        />
      )}
    </>
  )
}
