import React from 'react'

export function StatusIndicator({ connected, thinking, status }: { connected: boolean; thinking: boolean; status?: string }) {
  return (
    <div className="px-4 py-2 text-xs text-textSubtle">
      <span className={connected ? 'text-successGreen' : 'text-errorRed'}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
      {thinking ? <span className="ml-2 text-tweetBlue">Thinking…</span> : null}
      {status ? <span className="ml-2">{status}</span> : null}
    </div>
  )
}
