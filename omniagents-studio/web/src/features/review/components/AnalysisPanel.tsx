import { useState, useEffect, useRef, useCallback } from 'react'
import { useTraceStore } from '../../traces/store/traceStore'
import { Target, ThumbsUp, ThumbsDown, Check, Save, Plus, Trash2, AlertTriangle } from 'lucide-react'
import { cn } from '../../../lib/utils'
import type { AnalysisData } from '../../../types'

export function AnalysisPanel() {
  const { 
    selectedConversation, 
    conversationAnalysis, 
    setConversationAnalysis,
    getAnalysisStats,
    selectedProject,
    failureCategories,
    loadFailureCategories,
    createFailureCategory,
    deleteFailureCategory,
    updateAnalysisCategories,
    analysisGoals,
    conversations
  } = useTraceStore()
  
  const [localAnalysis, setLocalAnalysis] = useState<AnalysisData>({
    markedForAnalysis: false,
    judgment: null,
    notes: '',
    categories: []
  })
  const [showSaved, setShowSaved] = useState(false)
  const [showAddCategory, setShowAddCategory] = useState(false)
  const [newCategoryName, setNewCategoryName] = useState('')
  const [newCategoryDescription, setNewCategoryDescription] = useState('')
  const [categoryError, setCategoryError] = useState('')
  const [categoryToDelete, setCategoryToDelete] = useState<{ id: string; name: string; usageCount: number } | null>(null)
  const [hoveredCategoryId, setHoveredCategoryId] = useState<string | null>(null)
  const notesRef = useRef<HTMLTextAreaElement>(null)
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  
  // Load analysis data when conversation changes
  useEffect(() => {
    if (selectedConversation) {
      const existingAnalysis = conversationAnalysis[selectedConversation.group_id] || {
        markedForAnalysis: false,
        judgment: null,
        notes: '',
        categories: []
      }
      setLocalAnalysis(existingAnalysis)
    } else {
      setLocalAnalysis({
        markedForAnalysis: false,
        judgment: null,
        notes: '',
        categories: []
      })
    }
  }, [selectedConversation, conversationAnalysis])
  
  // Load failure categories when project changes
  useEffect(() => {
    if (selectedProject) {
      loadFailureCategories(selectedProject)
    }
  }, [selectedProject, loadFailureCategories])
  
  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
      }
    }
  }, [])
  
  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't handle shortcuts when typing in notes
      if (e.target instanceof HTMLTextAreaElement) {
        // Allow Escape to blur the textarea
        if (e.key === 'Escape') {
          (e.target as HTMLTextAreaElement).blur()
        }
        return
      }
      
      // Don't handle shortcuts when in input fields
      if (e.target instanceof HTMLInputElement) {
        return
      }
      
      if (!selectedConversation) return
      
      switch (e.key) {
        case '1':
          e.preventDefault()
          handleJudgment('acceptable')
          break
        case '2':
          e.preventDefault()
          handleJudgment('unacceptable')
          break
        case 'n':
        case 'N':
          if (localAnalysis.judgment) {
            e.preventDefault()
            notesRef.current?.focus()
          }
          break
      }
    }
    
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedConversation, localAnalysis])
  
  // Show saved indicator for 2 seconds
  const triggerSavedIndicator = () => {
    setShowSaved(true)
    setTimeout(() => setShowSaved(false), 2000)
  }
  
  const handleJudgment = async (judgment: 'acceptable' | 'unacceptable') => {
    if (!selectedConversation) return
    
    const newAnalysis = {
      ...localAnalysis,
      judgment: localAnalysis.judgment === judgment ? null : judgment,
      markedForAnalysis: true,
      analyzedAt: new Date().toISOString()
    }
    setLocalAnalysis(newAnalysis)
    await setConversationAnalysis(selectedConversation.group_id, newAnalysis)
    triggerSavedIndicator()
  }
  
  const handleNotesChange = useCallback((notes: string) => {
    if (!selectedConversation) return
    
    const newAnalysis = {
      ...localAnalysis,
      notes,
      markedForAnalysis: true,
      analyzedAt: localAnalysis.analyzedAt || new Date().toISOString()
    }
    setLocalAnalysis(newAnalysis)
    
    // Debounce the API call
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current)
    }
    
    saveTimeoutRef.current = setTimeout(async () => {
      await setConversationAnalysis(selectedConversation.group_id, newAnalysis)
      // Don't show saved indicator on every keystroke, just update silently
    }, 500) // Save after 500ms of no typing
  }, [selectedConversation, localAnalysis, setConversationAnalysis])
  
  const handleCategoryToggle = async (categoryId: string) => {
    if (!selectedConversation) return
    
    const currentCategories = localAnalysis.categories || []
    const newCategories = currentCategories.includes(categoryId)
      ? currentCategories.filter(id => id !== categoryId)
      : [...currentCategories, categoryId]
    
    const newAnalysis = {
      ...localAnalysis,
      categories: newCategories
    }
    setLocalAnalysis(newAnalysis)
    
    // Update backend immediately for categories
    await updateAnalysisCategories(selectedConversation.group_id, newCategories)
    triggerSavedIndicator()
  }
  
  const handleCreateCategory = async () => {
    if (!newCategoryName.trim()) {
      setCategoryError('Category name is required')
      return
    }
    
    try {
      setCategoryError('')
      const newCategory = await createFailureCategory(
        newCategoryName.trim(),
        newCategoryDescription.trim() || undefined
      )
      
      // Clear form and close
      setNewCategoryName('')
      setNewCategoryDescription('')
      setShowAddCategory(false)
      
      // Auto-select the new category
      if (selectedConversation) {
        const newCategories = [...(localAnalysis.categories || []), newCategory.id]
        const newAnalysis = {
          ...localAnalysis,
          categories: newCategories
        }
        setLocalAnalysis(newAnalysis)
        await updateAnalysisCategories(selectedConversation.group_id, newCategories)
        triggerSavedIndicator()
      }
    } catch (error) {
      if (error instanceof Error && error.message.includes('409')) {
        setCategoryError('A category with this name already exists')
      } else {
        setCategoryError('Failed to create category')
      }
    }
  }
  
  const handleDeleteCategory = async () => {
    if (!categoryToDelete) return
    
    try {
      await deleteFailureCategory(categoryToDelete.id)
      setCategoryToDelete(null)
      triggerSavedIndicator()
    } catch (error) {
      console.error('Failed to delete category:', error)
      // Could show an error toast here
    }
  }
  
  if (!selectedConversation) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center">
          <Target className="w-12 h-12 mx-auto mb-2 opacity-20" />
          <p className="text-sm">Select a conversation to analyze</p>
        </div>
      </div>
    )
  }
  
  const isAnalyzed = localAnalysis.judgment !== null
  
  // Get overall analysis stats
  const stats = getAnalysisStats()
  const allowedGroupIds = new Set(conversations.map(c => c.group_id))
  const totalConversations = conversations.length
  
  // Calculate progress for each goal
  const tracesProgress = Math.min((totalConversations / analysisGoals.tracesRequired) * 100, 100)
  const badTracesProgress = Math.min((stats.unacceptable / analysisGoals.badTracesRequired) * 100, 100)
  
  // Count bad traces with notes
  const badTracesWithNotes = Object.entries(conversationAnalysis)
    .filter(([groupId]) => allowedGroupIds.has(groupId))
    .filter(([, a]) => a.judgment === 'unacceptable' && a.notes && a.notes.trim().length > 0)
    .length
  const documentationProgress = analysisGoals.badTracesRequiringNotes > 0 
    ? Math.min((badTracesWithNotes / analysisGoals.badTracesRequiringNotes) * 100, 100)
    : 0
  
  // Count bad traces with categories
  const badTracesWithCategories = Object.entries(conversationAnalysis)
    .filter(([groupId]) => allowedGroupIds.has(groupId))
    .filter(([, a]) => a.judgment === 'unacceptable' && a.categories && a.categories.length > 0)
    .length
  const categorizationProgress = analysisGoals.badTracesRequiringCategories > 0
    ? Math.min((badTracesWithCategories / analysisGoals.badTracesRequiringCategories) * 100, 100)
    : 0
  
  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-border p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Target className="w-4 h-4 text-blue-500" />
            <h2 className="text-lg font-semibold">Analysis</h2>
          </div>
          
          {/* Auto-save indicator */}
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {showSaved ? (
              <div className="flex items-center gap-1 text-green-600 dark:text-green-400 animate-in fade-in duration-300">
                <Check className="w-3 h-3" />
                <span>Saved</span>
              </div>
            ) : (
              <div className="flex items-center gap-1">
                <Save className="w-3 h-3" />
                <span>Auto-save</span>
              </div>
            )}
          </div>
        </div>
      </div>
      
      {/* Scrollable content area */}
      <div className="flex-1 overflow-y-auto">
        {/* Analysis Status and Actions */}
        <div className="p-4 space-y-4">
        {/* Step 1: Rate Quality */}
        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Step 1: Rate Quality
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => handleJudgment('acceptable')}
              className={cn(
                "px-3 py-3 text-sm font-medium rounded-lg transition-all flex items-center justify-center gap-2 border",
                localAnalysis.judgment === 'acceptable'
                  ? "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 border-green-200 dark:border-green-800"
                  : "bg-background hover:bg-green-50 dark:hover:bg-green-900/20 border-border"
              )}
            >
              <ThumbsUp className="w-4 h-4" />
              Good
            </button>
            <button
              onClick={() => handleJudgment('unacceptable')}
              className={cn(
                "px-3 py-3 text-sm font-medium rounded-lg transition-all flex items-center justify-center gap-2 border",
                localAnalysis.judgment === 'unacceptable'
                  ? "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800"
                  : "bg-background hover:bg-red-50 dark:hover:bg-red-900/20 border-border"
              )}
            >
              <ThumbsDown className="w-4 h-4" />
              Bad
            </button>
          </div>
        </div>
      </div>
      
      {/* Step 2: Notes Section - Only enabled after judgment */}
      <div className="border-t border-border p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Step 2: Add Notes {!isAnalyzed && "(Complete Step 1 First)"}
          </div>
          {isAnalyzed && localAnalysis.notes && (
            <div className="text-xs text-muted-foreground italic">
              Auto-saves as you type
            </div>
          )}
        </div>
        <textarea
          ref={notesRef}
          value={localAnalysis.notes}
          onChange={(e) => handleNotesChange(e.target.value)}
          disabled={!isAnalyzed}
          placeholder={isAnalyzed 
            ? "Add any notes about issues, quality problems, or observations... (Press Esc to exit)" 
            : "Rate the conversation quality first to enable notes..."
          }
          className={cn(
            "w-full h-24 p-3 text-sm border rounded-lg resize-none focus:outline-none",
            isAnalyzed 
              ? "bg-muted/30 border-input focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400" 
              : "bg-muted/10 border-border opacity-50 cursor-not-allowed"
          )}
        />
      </div>
      
      {/* Step 3: Categorize Failures - Always visible but disabled when prerequisites not met */}
      <div className="border-t border-border p-4">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
          Step 3: Categorize Failures {
            localAnalysis.judgment !== 'unacceptable' 
              ? "(Mark as Bad First)" 
              : stats.unacceptable < analysisGoals.badTracesRequiringCategories 
              ? `(Document ${analysisGoals.badTracesRequiringCategories - stats.unacceptable} more bad conversations)`
              : ""
          }
        </div>
        
        {/* Category checkboxes */}
        <div className={cn(
          "space-y-2 max-h-48 overflow-y-auto",
          (localAnalysis.judgment !== 'unacceptable' || stats.unacceptable < analysisGoals.badTracesRequiringCategories) && "opacity-50"
        )}>
          {failureCategories.map((category) => (
            <div
              key={category.id}
              className={cn(
                "flex items-start gap-2 p-2 rounded group",
                localAnalysis.judgment === 'unacceptable' && stats.unacceptable >= analysisGoals.badTracesRequiringCategories
                  ? "hover:bg-muted/50"
                  : ""
              )}
              onMouseEnter={() => setHoveredCategoryId(category.id)}
              onMouseLeave={() => setHoveredCategoryId(null)}
            >
              <label
                className={cn(
                  "flex items-start gap-2 flex-1",
                  localAnalysis.judgment === 'unacceptable' && stats.unacceptable >= analysisGoals.badTracesRequiringCategories
                    ? "cursor-pointer"
                    : "cursor-not-allowed"
                )}
              >
                <input
                  type="checkbox"
                  checked={localAnalysis.categories?.includes(category.id) || false}
                  onChange={() => handleCategoryToggle(category.id)}
                  disabled={localAnalysis.judgment !== 'unacceptable' || stats.unacceptable < analysisGoals.badTracesRequiringCategories}
                  className="mt-0.5 rounded border-border text-primary focus:ring-primary disabled:cursor-not-allowed"
                />
                <div className="flex-1">
                  <div className="text-sm font-medium">{category.name}</div>
                  {category.description && (
                    <div className="text-xs text-muted-foreground">{category.description}</div>
                  )}
                  {category.usageCount !== undefined && category.usageCount > 0 && (
                    <div className="text-xs text-muted-foreground mt-0.5">
                      Used in {category.usageCount} conversation{category.usageCount !== 1 ? 's' : ''}
                    </div>
                  )}
                </div>
              </label>
              {hoveredCategoryId === category.id && (
                <button
                  onClick={() => setCategoryToDelete({
                    id: category.id,
                    name: category.name,
                    usageCount: category.usageCount || 0
                  })}
                  className="p-1 hover:bg-destructive/10 rounded transition-colors"
                  title="Delete category"
                >
                  <Trash2 className="w-3 h-3 text-destructive" />
                </button>
              )}
            </div>
          ))}
          
          {failureCategories.length === 0 && (
            <div className="text-sm text-muted-foreground italic">
              No categories defined yet. {localAnalysis.judgment === 'unacceptable' && stats.unacceptable >= analysisGoals.badTracesRequiringCategories ? "Add one below." : "Categories can be added once prerequisites are met."}
            </div>
          )}
        </div>
        
        {/* Add new category - only show when enabled */}
        {localAnalysis.judgment === 'unacceptable' && stats.unacceptable >= analysisGoals.badTracesRequiringCategories && (
          <>
            {!showAddCategory ? (
              <button
                onClick={() => setShowAddCategory(true)}
                className="mt-3 w-full px-3 py-2 text-sm font-medium rounded-lg border border-dashed border-border hover:border-primary hover:bg-muted/50 transition-all flex items-center justify-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Add New Category
              </button>
            ) : (
              <div className="mt-3 p-3 border rounded-lg bg-muted/30">
                <input
                  type="text"
                  placeholder="Category name (required)"
                  value={newCategoryName}
                  onChange={(e) => setNewCategoryName(e.target.value)}
                  className="w-full px-3 py-2 text-sm border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                />
                <textarea
                  placeholder="Description (optional)"
                  value={newCategoryDescription}
                  onChange={(e) => setNewCategoryDescription(e.target.value)}
                  className="w-full mt-2 px-3 py-2 text-sm border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none h-16"
                />
                {categoryError && (
                  <div className="mt-2 text-xs text-red-500">{categoryError}</div>
                )}
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={handleCreateCategory}
                    className="flex-1 px-3 py-1.5 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90"
                  >
                    Create
                  </button>
                  <button
                    onClick={() => {
                      setShowAddCategory(false)
                      setNewCategoryName('')
                      setNewCategoryDescription('')
                      setCategoryError('')
                    }}
                    className="flex-1 px-3 py-1.5 text-sm font-medium border border-border rounded-lg hover:bg-muted/50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
      </div>
      
      {/* Analysis Goals Progress - Fixed at bottom */}
      <div className="flex-shrink-0 border-t border-border bg-muted/30 p-3">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
          Analysis Goals
        </div>
        
        <div className="space-y-1.5">
          {/* Goal 1: Select 100 traces */}
          <div className="flex-1">
            <div className="flex items-center justify-between text-xs mb-0.5">
              <span className="text-muted-foreground">Conversations</span>
              <span className={cn(
                "font-medium",
                totalConversations >= analysisGoals.tracesRequired ? "text-green-600" : ""
              )}>
                {totalConversations}/{analysisGoals.tracesRequired}
              </span>
            </div>
            <div className="relative h-1 bg-muted rounded-full overflow-hidden">
              <div 
                className={cn(
                  "absolute left-0 top-0 h-full transition-all rounded-full",
                  totalConversations >= analysisGoals.tracesRequired ? "bg-green-500" : "bg-blue-500"
                )}
                style={{ width: `${tracesProgress}%` }}
              />
            </div>
          </div>
          
          {/* Goal 2: Find bad traces */}
          <div className="flex-1">
            <div className="flex items-center justify-between text-xs mb-0.5">
              <span className="text-muted-foreground">Bad conversations</span>
              <span className={cn(
                "font-medium",
                stats.unacceptable >= analysisGoals.badTracesRequired ? "text-green-600" : stats.unacceptable >= analysisGoals.badTracesRequired / 2 ? "text-orange-600" : ""
              )}>
                {stats.unacceptable}/{analysisGoals.badTracesRequired}
              </span>
            </div>
            <div className="relative h-1 bg-muted rounded-full overflow-hidden">
              <div 
                className={cn(
                  "absolute left-0 top-0 h-full transition-all rounded-full",
                  stats.unacceptable >= analysisGoals.badTracesRequired 
                    ? "bg-green-500" 
                    : stats.unacceptable >= analysisGoals.badTracesRequired / 2
                    ? "bg-orange-500"
                    : "bg-red-500"
                )}
                style={{ width: `${badTracesProgress}%` }}
              />
            </div>
          </div>
          
          {/* Goal 3: Document issues */}
          <div className="flex-1">
            <div className="flex items-center justify-between text-xs mb-0.5">
              <span className="text-muted-foreground">Documentation</span>
              <span className={cn(
                "font-medium",
                badTracesWithNotes >= analysisGoals.badTracesRequiringNotes ? "text-green-600" : badTracesWithNotes >= analysisGoals.badTracesRequiringNotes / 2 ? "text-orange-600" : ""
              )}>
                {badTracesWithNotes}/{analysisGoals.badTracesRequiringNotes} noted
              </span>
            </div>
            <div className="relative h-1 bg-muted rounded-full overflow-hidden">
              <div 
                className={cn(
                  "absolute left-0 top-0 h-full transition-all rounded-full",
                  documentationProgress === 100
                    ? "bg-green-500"
                    : documentationProgress >= 50
                    ? "bg-orange-500"
                    : "bg-yellow-500"
                )}
                style={{ width: `${documentationProgress}%` }}
              />
            </div>
          </div>
          
          {/* Goal 4: Categorize failures */}
          <div className="flex-1">
            <div className="flex items-center justify-between text-xs mb-0.5">
              <span className="text-muted-foreground">Categorization</span>
              <span className={cn(
                "font-medium",
                badTracesWithCategories >= analysisGoals.badTracesRequiringCategories ? "text-green-600" : badTracesWithCategories >= analysisGoals.badTracesRequiringCategories / 2 ? "text-orange-600" : ""
              )}>
                {badTracesWithCategories}/{analysisGoals.badTracesRequiringCategories} categorized
              </span>
            </div>
            <div className="relative h-1 bg-muted rounded-full overflow-hidden">
              <div 
                className={cn(
                  "absolute left-0 top-0 h-full transition-all rounded-full",
                  categorizationProgress === 100
                    ? "bg-green-500"
                    : categorizationProgress >= 50
                    ? "bg-orange-500"
                    : "bg-purple-500"
                )}
                style={{ width: `${categorizationProgress}%` }}
              />
            </div>
          </div>
        </div>
      </div>
      
      {/* Delete Confirmation Modal */}
      {categoryToDelete && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-background border border-border rounded-lg p-4 max-w-md w-full mx-4">
            <div className="flex items-start gap-3 mb-4">
              <div className="p-2 bg-destructive/10 rounded-full">
                <AlertTriangle className="w-5 h-5 text-destructive" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-semibold mb-2">Delete Category</h3>
                <p className="text-sm text-muted-foreground">
                  Are you sure you want to delete the category <strong className="text-foreground">"{categoryToDelete.name}"</strong>?
                </p>
                {categoryToDelete.usageCount > 0 && (
                  <p className="text-sm text-orange-600 dark:text-orange-400 mt-2">
                    This category is currently used in {categoryToDelete.usageCount} conversation{categoryToDelete.usageCount !== 1 ? 's' : ''}.
                    It will be removed from all of them.
                  </p>
                )}
                <p className="text-sm text-destructive mt-2">
                  This action cannot be undone.
                </p>
              </div>
            </div>
            
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setCategoryToDelete(null)}
                className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted/50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteCategory}
                className="px-3 py-1.5 text-sm bg-destructive text-destructive-foreground rounded-md hover:bg-destructive/90 transition-colors"
              >
                Delete Category
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
