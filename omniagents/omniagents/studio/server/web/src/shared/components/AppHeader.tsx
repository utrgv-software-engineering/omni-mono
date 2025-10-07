import { useState, useEffect, useRef } from 'react'
import { MoreVertical, Trash2, Download, AlertTriangle, Copy, Settings } from 'lucide-react'
import { useTraceStore } from '../../features/traces/store/traceStore'
import { useProjects, useWorkflows } from '../../features/traces/hooks/useTraces'
import { useQueryClient } from '@tanstack/react-query'
import { cn } from '../../lib/utils'
import { AnalysisGoalsModal } from './AnalysisGoalsModal'

interface AppHeaderProps {
  viewMode?: 'debug' | 'review'
  onViewModeChange?: (mode: 'debug' | 'review') => void
  showViewToggle?: boolean
}

export function AppHeader({ viewMode, onViewModeChange, showViewToggle = false }: AppHeaderProps) {
  const { selectedProject, setSelectedProject, selectedWorkflow, setSelectedWorkflow, setSelectedConversation, setSelectedTrace, conversationAnalysis, failureCategories } = useTraceStore()
  const { data: projectsData, refetch: refetchProjects } = useProjects()
  const { data: workflowsData } = useWorkflows()
  const queryClient = useQueryClient()
  const [showSettingsMenu, setShowSettingsMenu] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showGoalsModal, setShowGoalsModal] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const settingsMenuRef = useRef<HTMLDivElement>(null)
  
  // Close settings menu on outside click
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

  useEffect(() => {
    if (!selectedProject && projectsData?.projects?.length) {
      setSelectedProject(projectsData.projects[0])
    }
  }, [selectedProject, projectsData, setSelectedProject])

  useEffect(() => {
    if (selectedProject && !selectedWorkflow && workflowsData?.workflows?.length) {
      setSelectedWorkflow(workflowsData.workflows[0])
    }
  }, [selectedProject, selectedWorkflow, workflowsData, setSelectedWorkflow])
  
  const handleDeleteProject = async () => {
    if (!selectedProject) return
    
    setIsDeleting(true)
    try {
      const response = await fetch(`/api/projects/${selectedProject}`, {
        method: 'DELETE',
      })
      
      if (!response.ok) {
        throw new Error('Failed to delete project')
      }
      
      // Clear selected items
      setSelectedProject(null)
      setSelectedConversation(null)
      setSelectedTrace(null)
      
      // Invalidate and refetch data
      await queryClient.invalidateQueries()
      await refetchProjects()
      
      // Select first available project if any remain
      const remainingProjects = projectsData?.projects?.filter(p => p !== selectedProject) || []
      if (remainingProjects.length > 0) {
        setSelectedProject(remainingProjects[0])
      }
    } catch (error) {
      console.error('Error deleting project:', error)
      alert('Failed to delete project. Please try again.')
    } finally {
      setIsDeleting(false)
      setShowDeleteConfirm(false)
    }
  }
  
  const getAnalysisData = () => {
    // Get all conversations with analysis
    return Object.entries(conversationAnalysis).map(([group_id, analysis]) => {
      // Map category IDs to names
      const categoryNames = (analysis.categories || []).map(categoryId => {
        const category = failureCategories.find(cat => cat.id === categoryId)
        return category ? category.name : `Unknown Category (${categoryId})`
      })
      
      return {
        group_id,
        marked_for_analysis: analysis.markedForAnalysis || false,
        judgment: analysis.judgment || '',
        notes: analysis.notes || '',
        analyzed_at: analysis.analyzedAt || '',
        categories: categoryNames
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
      // Could add a toast notification here if we had a toast system
      console.log('Analysis copied to clipboard')
    } catch (error) {
      console.error('Failed to copy to clipboard:', error)
    }
  }
  
  return (
    <>
      <div className="border-b border-border bg-background">
        <div className="flex items-center justify-between px-4 h-14">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
            <svg 
              viewBox="0 0 24 24" 
              className="w-6 h-6 text-white"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path 
                d="M12 2L2 7L12 12L22 7L12 2Z" 
                stroke="currentColor" 
                strokeWidth="2" 
                strokeLinecap="round" 
                strokeLinejoin="round"
              />
              <path 
                d="M2 17L12 22L22 17" 
                stroke="currentColor" 
                strokeWidth="2" 
                strokeLinecap="round" 
                strokeLinejoin="round"
              />
              <path 
                d="M2 12L12 17L22 12" 
                stroke="currentColor" 
                strokeWidth="2" 
                strokeLinecap="round" 
                strokeLinejoin="round"
              />
            </svg>
            </div>
            <h1 className="text-lg font-semibold">
              OmniAgents Studio
            </h1>
            <div className="ml-3 flex items-center gap-2">
              <select
                value={selectedProject || ''}
                onChange={(e) => setSelectedProject(e.target.value || null)}
                className="px-2 py-1 text-xs bg-muted/50 border-0 rounded-md outline-none focus:bg-muted cursor-pointer"
              >
                <option value="">Project</option>
                {projectsData?.projects?.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
              <select
                value={selectedWorkflow || ''}
                onChange={(e) => setSelectedWorkflow(e.target.value || null)}
                className="px-2 py-1 text-xs bg-muted/50 border-0 rounded-md outline-none focus:bg-muted cursor-pointer"
                disabled={!workflowsData?.workflows?.length}
              >
                <option value="">Workflow</option>
                {workflowsData?.workflows?.map((w) => (
                  <option key={w} value={w}>{w}</option>
                ))}
              </select>
            </div>
          </div>
          
          <div className="flex items-center gap-2">
            {/* View Mode Toggle */}
            {showViewToggle && onViewModeChange && (
              <div className="flex items-center gap-1 p-0.5 bg-muted rounded-md">
                <button
                  onClick={() => onViewModeChange('review')}
                  className={cn(
                    "px-4 py-1.5 text-sm font-medium rounded transition-colors",
                    viewMode === 'review'
                      ? 'bg-background shadow-sm' 
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  Review
                </button>
                <button
                  onClick={() => onViewModeChange('debug')}
                  className={cn(
                    "px-4 py-1.5 text-sm font-medium rounded transition-colors",
                    viewMode === 'debug'
                      ? 'bg-background shadow-sm' 
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  Debug
                </button>
              </div>
            )}
            
            {/* Settings Menu */}
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
      
      {/* Delete Confirmation Dialog */}
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
      
      {/* Analysis Goals Configuration Modal */}
      {showGoalsModal && (
        <AnalysisGoalsModal
          isOpen={showGoalsModal}
          onClose={() => setShowGoalsModal(false)}
        />
      )}
    </>
  )
}
