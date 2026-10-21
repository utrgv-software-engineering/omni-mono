import React from 'react'

export type RecapInfo = {
  text: string
  timestamp: number
}

type Props = {
  recap: RecapInfo | null
  onDismiss: () => void
}

// Docked session-recap panel. Shows the most recent recap (from /recap or
// the idle auto-recap) until dismissed. Styled like the Notifications dock
// card but sized for a ~400-word block: scrollable, whitespace-preserving.
export function RecapPanel({ recap, onDismiss }: Props) {
  if (!recap) return null

  return (
    <div className="px-3 pt-2">
      <div className="rounded-md border border-bgCardAlt bg-bgCardAlt/60 p-2.5">
        <div className="flex items-center gap-2 text-xs text-textSubtle">
          <span className="font-medium text-textPrimary">Session recap</span>
          <button
            type="button"
            onClick={onDismiss}
            className="ml-auto text-textSubtle hover:text-textPrimary transition-colors px-1.5 py-0.5 rounded hover:bg-bgCardAlt"
            title="Dismiss recap"
          >
            dismiss
          </button>
        </div>
        <div className="mt-1.5 max-h-64 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-textPrimary">
          {recap.text}
        </div>
      </div>
    </div>
  )
}
