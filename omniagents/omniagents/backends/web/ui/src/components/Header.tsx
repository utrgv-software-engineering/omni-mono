import React from 'react'

export function Header({ agentName, welcomeText, connected }: { agentName: string; welcomeText?: string; connected?: boolean }) {
  return (
    <div className="border-b border-bgCardAlt bg-bgColumn">
      <div className="container-chat px-3 h-12 flex items-center">
        <div className="flex items-center gap-2">
          <span className={["inline-block w-2 h-2 rounded-full", connected ? 'bg-successGreen' : 'bg-errorRed'].join(' ')} />
          <div className="text-base font-semibold text-white">{agentName || 'OmniAgent'}</div>
        </div>
        {welcomeText ? (
          <div className="ml-3 text-sm text-textSubtle">{welcomeText}</div>
        ) : null}
      </div>
    </div>
  )
}
