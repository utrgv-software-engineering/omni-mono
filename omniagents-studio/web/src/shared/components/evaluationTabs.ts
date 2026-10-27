export type EvaluationTab = 'scenarios' | 'runs' | 'criteria' | 'judges' | 'optimize'

export const EVALUATION_TABS: { key: EvaluationTab; label: string }[] = [
  { key: 'scenarios', label: 'Scenarios' },
  { key: 'runs', label: 'Runs' },
  { key: 'criteria', label: 'Categories and measures' },
  { key: 'judges', label: 'Judges' },
  { key: 'optimize', label: 'Optimize' },
]
