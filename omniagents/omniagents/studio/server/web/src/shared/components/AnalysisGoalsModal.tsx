import { useState, useEffect } from 'react'
import { X, AlertCircle, Zap, Database, Settings2 } from 'lucide-react'
import { useTraceStore } from '../../features/traces/store/traceStore'
import { cn } from '../../lib/utils'

interface AnalysisGoalsModalProps {
  isOpen: boolean
  onClose: () => void
}

type PresetMode = 'eval-spike' | 'saturation' | 'custom'

const PRESET_CONFIGS = {
  'eval-spike': {
    name: 'Eval Spike',
    description: 'Quick evaluation to find and fix a few issues',
    icon: Zap,
    values: {
      tracesRequired: 20,
      badTracesRequired: 5,
      badTracesRequiringNotes: 5,
      badTracesRequiringCategories: 0
    }
  },
  'saturation': {
    name: 'Saturation Run',
    description: 'Comprehensive analysis to identify patterns',
    icon: Database,
    values: {
      tracesRequired: 100,
      badTracesRequired: 20,
      badTracesRequiringNotes: 20,
      badTracesRequiringCategories: 20
    }
  },
  'custom': {
    name: 'Custom',
    description: 'Configure your own analysis goals',
    icon: Settings2,
    values: null
  }
}

export function AnalysisGoalsModal({ isOpen, onClose }: AnalysisGoalsModalProps) {
  const { analysisGoals, setAnalysisGoals } = useTraceStore()
  
  const [selectedMode, setSelectedMode] = useState<PresetMode>('custom')
  const [tracesRequired, setTracesRequired] = useState(analysisGoals.tracesRequired)
  const [badTracesRequired, setBadTracesRequired] = useState(analysisGoals.badTracesRequired)
  const [badTracesRequiringNotes, setBadTracesRequiringNotes] = useState(analysisGoals.badTracesRequiringNotes)
  const [badTracesRequiringCategories, setBadTracesRequiringCategories] = useState(analysisGoals.badTracesRequiringCategories)
  const [errors, setErrors] = useState<Record<string, string>>({})
  
  // Reset form when modal opens
  useEffect(() => {
    if (isOpen) {
      // Determine which mode is currently selected based on values
      const isEvalSpike = 
        analysisGoals.tracesRequired === 20 &&
        analysisGoals.badTracesRequired === 5 &&
        analysisGoals.badTracesRequiringNotes === 5 &&
        analysisGoals.badTracesRequiringCategories === 0
      
      const isSaturation = 
        analysisGoals.tracesRequired === 100 &&
        analysisGoals.badTracesRequired === 20 &&
        analysisGoals.badTracesRequiringNotes === 20 &&
        analysisGoals.badTracesRequiringCategories === 20
      
      if (isEvalSpike) {
        setSelectedMode('eval-spike')
      } else if (isSaturation) {
        setSelectedMode('saturation')
      } else {
        setSelectedMode('custom')
      }
      
      setTracesRequired(analysisGoals.tracesRequired)
      setBadTracesRequired(analysisGoals.badTracesRequired)
      setBadTracesRequiringNotes(analysisGoals.badTracesRequiringNotes)
      setBadTracesRequiringCategories(analysisGoals.badTracesRequiringCategories)
      setErrors({})
    }
  }, [isOpen, analysisGoals])
  
  // Handle preset selection
  const handleModeSelect = (mode: PresetMode) => {
    setSelectedMode(mode)
    setErrors({})
    
    if (mode !== 'custom') {
      const config = PRESET_CONFIGS[mode]
      if (config.values) {
        setTracesRequired(config.values.tracesRequired)
        setBadTracesRequired(config.values.badTracesRequired)
        setBadTracesRequiringNotes(config.values.badTracesRequiringNotes)
        setBadTracesRequiringCategories(config.values.badTracesRequiringCategories)
      }
    }
  }
  
  const validateForm = () => {
    const newErrors: Record<string, string> = {}
    
    // Validate tracesRequired
    if (!tracesRequired || tracesRequired < 1) {
      newErrors.tracesRequired = 'Must be a positive integer'
    }
    
    // Validate badTracesRequired
    if (!badTracesRequired || badTracesRequired < 0) {
      newErrors.badTracesRequired = 'Must be 0 or greater'
    } else if (badTracesRequired > tracesRequired) {
      newErrors.badTracesRequired = `Must be less than or equal to total conversations (${tracesRequired})`
    }
    
    // Validate badTracesRequiringNotes
    if (!badTracesRequiringNotes && badTracesRequiringNotes !== 0) {
      newErrors.badTracesRequiringNotes = 'Must be 0 or greater'
    } else if (badTracesRequiringNotes < 0) {
      newErrors.badTracesRequiringNotes = 'Must be 0 or greater'
    } else if (badTracesRequiringNotes > badTracesRequired) {
      newErrors.badTracesRequiringNotes = `Must be less than or equal to bad conversations required (${badTracesRequired})`
    }
    
    // Validate badTracesRequiringCategories
    if (!badTracesRequiringCategories && badTracesRequiringCategories !== 0) {
      newErrors.badTracesRequiringCategories = 'Must be 0 or greater'
    } else if (badTracesRequiringCategories < 0) {
      newErrors.badTracesRequiringCategories = 'Must be 0 or greater'
    } else if (badTracesRequiringCategories > badTracesRequiringNotes) {
      newErrors.badTracesRequiringCategories = `Must be less than or equal to bad conversations requiring notes (${badTracesRequiringNotes})`
    }
    
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }
  
  const handleSave = () => {
    if (validateForm()) {
      setAnalysisGoals({
        tracesRequired,
        badTracesRequired,
        badTracesRequiringNotes,
        badTracesRequiringCategories
      })
      onClose()
    }
  }
  
  if (!isOpen) return null
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-background border border-border rounded-lg p-4 max-w-2xl w-full mx-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Configure Analysis Goals</h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-muted rounded-md transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        
        {/* Preset Mode Selection */}
        <div className="grid grid-cols-3 gap-2 mb-3">
          {Object.entries(PRESET_CONFIGS).map(([key, config]) => {
            const Icon = config.icon
            return (
              <button
                key={key}
                onClick={() => handleModeSelect(key as PresetMode)}
                className={cn(
                  "p-2 rounded-lg border transition-all text-left",
                  selectedMode === key
                    ? "bg-primary/10 border-primary"
                    : "bg-background border-border hover:bg-muted/50"
                )}
              >
                <div className="flex items-center gap-2">
                  <Icon className={cn(
                    "w-4 h-4",
                    selectedMode === key ? "text-primary" : "text-muted-foreground"
                  )} />
                  <div>
                    <div className="text-sm font-medium">{config.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {config.description}
                    </div>
                  </div>
                </div>
              </button>
            )
          })}
        </div>
        
        {/* Show preset description for non-custom modes - more compact */}
        {selectedMode !== 'custom' && PRESET_CONFIGS[selectedMode].values && (
          <div className="mb-3 p-2 bg-muted/30 rounded-lg">
            <div className="text-xs text-muted-foreground">
              {selectedMode === 'eval-spike' ? (
                <div className="space-y-0.5">
                  <strong>Quick evaluation:</strong> 20 conversations • 5 issues • Focus on notes
                </div>
              ) : (
                <div className="space-y-0.5">
                  <strong>Comprehensive analysis:</strong> 100 conversations • 20 issues • Full categorization
                </div>
              )}
            </div>
          </div>
        )}
        
        {/* Compact 2x2 grid for inputs */}
        <div className="grid grid-cols-2 gap-3 mb-4">
          {/* Step 1: Total conversations */}
          <div>
            <label className="block text-xs font-medium mb-1 text-muted-foreground">
              STEP 1: TOTAL CONVERSATIONS
            </label>
            <div className="relative">
              <input
                type="number"
                min="1"
                value={tracesRequired}
                onChange={(e) => setTracesRequired(parseInt(e.target.value) || 0)}
                disabled={selectedMode !== 'custom'}
                className={cn(
                  "w-full px-2 py-1.5 text-sm border rounded-md focus:outline-none",
                  selectedMode === 'custom' 
                    ? "bg-background focus:ring-2 focus:ring-primary" 
                    : "bg-muted/50 cursor-not-allowed"
                )}
              />
              {errors.tracesRequired && (
                <AlertCircle className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-red-500" />
              )}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">Conversations to analyze</div>
          </div>
          
          {/* Step 2: Bad conversations */}
          <div>
            <label className="block text-xs font-medium mb-1 text-muted-foreground">
              STEP 2: BAD CONVERSATIONS
            </label>
            <div className="relative">
              <input
                type="number"
                min="0"
                max={tracesRequired}
                value={badTracesRequired}
                onChange={(e) => setBadTracesRequired(parseInt(e.target.value) || 0)}
                disabled={selectedMode !== 'custom'}
                className={cn(
                  "w-full px-2 py-1.5 text-sm border rounded-md focus:outline-none",
                  selectedMode === 'custom' 
                    ? "bg-background focus:ring-2 focus:ring-primary" 
                    : "bg-muted/50 cursor-not-allowed"
                )}
              />
              {errors.badTracesRequired && (
                <AlertCircle className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-red-500" />
              )}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              Unacceptable to find
            </div>
          </div>
          
          {/* Step 3: Documentation */}
          <div>
            <label className="block text-xs font-medium mb-1 text-muted-foreground">
              STEP 3: WITH NOTES
            </label>
            <div className="relative">
              <input
                type="number"
                min="0"
                max={badTracesRequired}
                value={badTracesRequiringNotes}
                onChange={(e) => setBadTracesRequiringNotes(parseInt(e.target.value) || 0)}
                disabled={selectedMode !== 'custom'}
                className={cn(
                  "w-full px-2 py-1.5 text-sm border rounded-md focus:outline-none",
                  selectedMode === 'custom' 
                    ? "bg-background focus:ring-2 focus:ring-primary" 
                    : "bg-muted/50 cursor-not-allowed"
                )}
              />
              {errors.badTracesRequiringNotes && (
                <AlertCircle className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-red-500" />
              )}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">Bad conversations to document</div>
          </div>
          
          {/* Step 4: Categorization */}
          <div>
            <label className="block text-xs font-medium mb-1 text-muted-foreground">
              STEP 4: CATEGORIZED
            </label>
            <div className="relative">
              <input
                type="number"
                min="0"
                max={badTracesRequiringNotes}
                value={badTracesRequiringCategories}
                onChange={(e) => setBadTracesRequiringCategories(parseInt(e.target.value) || 0)}
                disabled={selectedMode !== 'custom'}
                className={cn(
                  "w-full px-2 py-1.5 text-sm border rounded-md focus:outline-none",
                  selectedMode === 'custom' 
                    ? "bg-background focus:ring-2 focus:ring-primary" 
                    : "bg-muted/50 cursor-not-allowed"
                )}
              />
              {errors.badTracesRequiringCategories && (
                <AlertCircle className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-red-500" />
              )}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">Bad conversations to categorize</div>
          </div>
        </div>
        
        {/* Show any validation errors */}
        {Object.keys(errors).length > 0 && (
          <div className="mb-3 p-2 bg-red-50 dark:bg-red-900/20 rounded-md">
            <div className="text-xs text-red-600 dark:text-red-400 space-y-0.5">
              {Object.entries(errors).map(([field, error]) => (
                <div key={field}>• {error}</div>
              ))}
            </div>
          </div>
        )}
        
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted/50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
          >
            Save Goals
          </button>
        </div>
      </div>
    </div>
  )
}
