import React from 'react'

export function Header({ agentName, welcomeText }: { agentName: string; welcomeText?: string }) {
  return (
    <div className="px-4 py-3 border-b border-gray-800 bg-gray-900/60">
      <div className="text-lg font-semibold">{agentName || 'OmniAgent'}</div>
      {welcomeText ? (
        <div className="text-sm text-gray-400 mt-1">{welcomeText}</div>
      ) : null}
    </div>
  )
}

