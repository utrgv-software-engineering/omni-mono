import React from 'react'

export function Header({
  agentName,
  connected,
  onMenu,
  onArtifactsToggle,
  showArtifactsButton = false,
}: {
  agentName: string
  connected?: boolean
  onMenu?: () => void
  onArtifactsToggle?: () => void
  showArtifactsButton?: boolean
}) {
  return (
    <div className="border-b border-bgCardAlt bg-bgColumn">
      <div className="container-chat px-3 h-12 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {onMenu ? (
            <button className="w-8 h-8 rounded hover:bg-bgCardAlt text-gray-100" onClick={onMenu} aria-label="Menu">☰</button>
          ) : null}
          <span className={["inline-block w-2 h-2 rounded-full", connected ? 'bg-successGreen' : 'bg-errorRed'].join(' ')} />
          <div className="text-base font-semibold text-white">{agentName || 'OmniAgent'}</div>
        </div>
        {showArtifactsButton && onArtifactsToggle && (
          <button
            className="w-8 h-8 rounded hover:bg-bgCardAlt text-gray-100 flex items-center justify-center"
            onClick={onArtifactsToggle}
            aria-label="Toggle artifacts"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M9 3v18" />
            </svg>
          </button>
        )}
      </div>
    </div>
  )
}
