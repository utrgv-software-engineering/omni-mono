import React from 'react'

export function Header({ agentName, connected, onMenu }: { agentName: string; connected?: boolean; onMenu?: () => void }) {
  return (
    <div className="border-b border-bgCardAlt bg-bgColumn">
      <div className="container-chat px-3 h-12 flex items-center">
        <div className="flex items-center gap-2">
          {onMenu ? (
            <button className="w-8 h-8 rounded hover:bg-bgCardAlt text-gray-100" onClick={onMenu} aria-label="Menu">☰</button>
          ) : null}
          <span className={["inline-block w-2 h-2 rounded-full", connected ? 'bg-successGreen' : 'bg-errorRed'].join(' ')} />
          <div className="text-base font-semibold text-white">{agentName || 'OmniAgent'}</div>
        </div>
      </div>
    </div>
  )
}
